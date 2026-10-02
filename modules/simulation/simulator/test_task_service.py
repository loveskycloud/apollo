import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_service import ROOT, SIM_PKG, MODULES, TaskService, validate, preflight_plugins, preflight_world, preflight_module_dags, _inherit_scenario_environment, resolve, catalog
from bag_diff import compare, algorithm_payload


class QueueTests(unittest.TestCase):
    def test_default_runs_once_at_ten_ms_and_explicit_repeats_are_preserved(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = root / "input.record"
            source.touch()
            vehicle = root / "vehicle.pb.txt"
            vehicle.touch()
            map_dir = root / "map"
            map_dir.mkdir()
            for name in ("base_map.bin", "sim_map.bin"):
                (map_dir / name).touch()
            request = {"kind": "bag", "source": str(source), "map": str(map_dir),
                       "vehicle": str(vehicle), "modules": ["PLANNING"]}
            with patch("task_service.INPUT_ROOTS", [root]):
                config = validate(request)
                self.assertEqual((config["repeat"], config["step_ms"]), (1, 10))
                self.assertEqual(validate({**request, "repeat": 3})["repeat"], 3)
                with self.assertRaisesRegex(ValueError, "either PLANNING or ML_PLANNING"):
                    validate({**request, "modules": ["PLANNING", "ML_PLANNING"]})

    def test_parking_scorecard_keeps_failed_suite_member_without_analysis(self):
        with tempfile.TemporaryDirectory() as path:
            service=TaskService(path)
            try:
                source=SIM_PKG/'scene_editor/examples/parking_missions_v2/parallel_w55_aisle120_rear__out__base.worldsim.scenario.json'
                service.jobs['failed']={'id':'failed','stage':'failed','suite_id':'parking','config':{'source':str(source)}}
                service.jobs['unrelated']={'id':'unrelated','stage':'failed','suite_id':'another','config':{'source':str(source)}}
                result=service.request({'action':'parking_scorecard','suite_id':'parking'})['scorecard']
                self.assertEqual(result['case_count'],1)
                self.assertEqual(result['rates']['exit_success'],{'passed':0,'total':1,'rate':0.})
                self.assertEqual(result['safety']['collision_evaluated_cases'],0)
                with self.assertRaisesRegex(ValueError,'requires a suite_id'):service.request({'action':'parking_scorecard'})
            finally:service.close()

    def test_final_native_progress_is_published_to_live_job_events(self):
        with tempfile.TemporaryDirectory() as path:
            service = TaskService(path)
            try:
                job_id = "0123456789abcdef"
                service.jobs[job_id] = {"id": job_id, "stage": "simulation_running",
                                        "progress": 12, "history": []}
                progress = Path(path) / "progress.json"
                service.read_progress(job_id, progress)  # No first sample yet.
                self.assertEqual(service.jobs[job_id]["progress"], 12)
                revision = service.revision
                state = {"percent": 100, "sim_time_s": 8.51, "events_done": 751,
                         "events_total": 0, "speedup": 3.2}
                progress.write_text(json.dumps(state))
                service.read_progress(job_id, progress)
                event = service.events(after=revision, timeout=0)
                self.assertEqual(event["jobs"][0]["progress"], 100)
                self.assertEqual(event["jobs"][0]["simulation"], state)
            finally:
                service.close()

    def test_catalog_deduplicates_same_directory_but_keeps_distinct_resources(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            canonical = root / "data/map_data/test-map"
            canonical.mkdir(parents=True)
            (canonical / "base_map.txt").write_text('')
            (canonical / "sim_map.bin").write_bytes(b'')
            alias = root / "modules/map/data/test-map"
            alias.parent.mkdir(parents=True)
            alias.symlink_to(canonical, target_is_directory=True)
            distinct = root / "modules/simulation/scene_editor/public/maps/test-map"
            distinct.mkdir(parents=True)
            (distinct / "base_map.txt").write_text('')
            (distinct / "sim_map.txt").write_text('')
            with patch("task_service.ROOT", root), patch("task_service.SIM_PKG", root / "modules/simulation"):
                maps = [p for p in catalog()["maps"] if Path(p).name == "test-map"]
            self.assertEqual(maps, sorted([str(canonical), str(distinct)]))

    def test_catalog_excludes_editor_only_map_from_scenario_inheritance(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            canonical = root / "data/map_data/test-map"
            canonical.mkdir(parents=True)
            (canonical / "base_map.bin").write_bytes(b'')
            (canonical / "sim_map.txt").write_text('')
            editor = root / "modules/simulation/scene_editor/public/maps/test-map"
            editor.mkdir(parents=True)
            (editor / "base_map.txt").write_text('editor map')
            scene = root / "scene.json"
            scene.write_text(json.dumps({"mapId": "test-map"}))
            config = {"kind": "world", "source": str(scene), "vehicle": "/explicit/car"}
            with patch("task_service.ROOT", root), patch("task_service.SIM_PKG", root / "modules/simulation"):
                self.assertEqual([p for p in catalog()["maps"] if Path(p).name == "test-map"],
                                 [str(canonical)])
                _inherit_scenario_environment(config)
            self.assertEqual(config["map"], str(canonical))
            self.assertEqual((editor / "base_map.txt").read_text(), 'editor map')

    def test_enqueue_rejects_map_without_sim_map_before_creating_task(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            scene = root / "scene.json"
            scene.write_text('{}')
            map_dir = root / "map"
            map_dir.mkdir()
            (map_dir / "base_map.txt").write_text('')
            service = TaskService(root / "jobs")
            try:
                with patch("task_service.INPUT_ROOTS", [root]):
                    with self.assertRaisesRegex(ValueError, "has no sim_map.bin / sim_map.txt"):
                        service.request({"action": "enqueue", "config": {
                            "kind": "world", "source": str(scene),
                            "map": str(map_dir), "vehicle": str(root / "vehicle"),
                        }})
                self.assertEqual(service.jobs, {})
                self.assertEqual(list(service.pending_ids), [])
            finally:
                service.close()

    def test_delete_removes_owned_outputs_cache_and_publishes_snapshot(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            job_id = "0123456789abcdef"
            source = root / "input.record"
            source.write_text("original input")
            cache = root / "data/bag/.wm_mcap_cache"
            cache.mkdir(parents=True)
            key = "1234567890abcdef"
            service = TaskService(root / "jobs")
            try:
                directory = service.root / job_id
                directory.mkdir()
                (directory / "simulation.record").write_text("output")
                (directory / "input-link").symlink_to(source)
                (cache / (key + ".source.json")).write_text(json.dumps({"source": str(directory / "simulation.record")}))
                (cache / (key + ".progress.json")).write_text('{"status":"done"}')
                (cache / (key + ".mcap")).write_text("converted")
                (cache / "unrelated.mcap").write_text("keep")
                with service.lock:
                    service.jobs[job_id] = {"id": job_id, "stage": "completed", "config": {"source": str(source)}}
                    service.save()
                    service.notify_jobs([job_id])
                revision = service.events()["revision"]
                with patch("task_service.ROOT", root):
                    reply = service.request({"action": "delete", "id": job_id})
                self.assertEqual(reply["deleted_id"], job_id)
                self.assertEqual(reply["jobs"], [])
                self.assertFalse(directory.exists())
                self.assertEqual(source.read_text(), "original input")
                self.assertEqual(list(cache.iterdir()), [cache / "unrelated.mcap"])
                event = service.events(revision, timeout=0)
                self.assertTrue(event["snapshot"])
                self.assertEqual(event["jobs"], [])
                self.assertEqual(json.loads((service.root / "jobs.json").read_text()), {})
            finally:
                service.close()
            restarted = TaskService(root / "jobs")
            try:
                self.assertEqual(restarted.jobs, {})
            finally:
                restarted.close()

    def test_delete_rejects_active_tasks_bad_ids_and_external_symlinks(self):
        with tempfile.TemporaryDirectory() as path:
            service = TaskService(Path(path) / "jobs")
            job_id = "0123456789abcdef"
            try:
                service.jobs[job_id] = {"id": job_id, "stage": "simulation_running"}
                with self.assertRaisesRegex(ValueError, "Cancel the running task"):
                    service.request({"action": "delete", "id": job_id})
                service.jobs[job_id]["stage"] = "completed"
                service.active_ids.add(job_id)
                with self.assertRaisesRegex(ValueError, "Cancel the running task"):
                    service.delete_task(job_id)
                service.active_ids.clear()
                for invalid in ("../outside", "", None):
                    with self.assertRaisesRegex(ValueError, "Invalid task ID"):
                        service.delete_task(invalid)
                (service.root / job_id).symlink_to(path, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symbolic link"):
                    service.delete_task(job_id)
                self.assertIn(job_id, service.jobs)
            finally:
                service.close()

    def test_delete_queued_task_and_report_filesystem_failure(self):
        with tempfile.TemporaryDirectory() as path:
            service = TaskService(path)
            job_id = "0123456789abcdef"
            try:
                directory = service.root / job_id
                directory.mkdir()
                service.jobs[job_id] = {"id": job_id, "stage": "queued"}
                with service.lock:
                    service.pending_ids.append(job_id)
                    with patch("task_service.shutil.rmtree", side_effect=PermissionError("cannot remove output")):
                        with self.assertRaisesRegex(PermissionError, "cannot remove output"):
                            service.delete_task(job_id)
                    self.assertIn(job_id, service.jobs)
                    service.delete_task(job_id)
                    self.assertNotIn(job_id, service.pending_ids)
            finally:
                service.close()

    def test_only_current_simulation_paths_are_resolved(self):
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = root / "modules/simulation/scene_editor/examples/test.json"
            source.parent.mkdir(parents=True)
            source.write_text('{}')
            with patch("task_service.ROOT", root), patch("task_service.INPUT_ROOTS", [root]):
                self.assertEqual(resolve(str(source)), source)
                self.assertEqual(resolve("modules/simulation/scene_editor/examples/test.json"), source)
                self.assertEqual(resolve("/home/wangsheng/code/apollo/modules/simulation/scene_editor/examples/test.json"), source)
                # Old addresses must not silently redirect to the relocated file.
                with self.assertRaises(FileNotFoundError):
                    resolve("simulation/scene_editor/examples/test.json")
                with self.assertRaises(FileNotFoundError):
                    resolve(str(root / "simulation/scene_editor/examples/test.json"))

    def test_inherit_scenario_environment_and_keep_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as path:
            scene = Path(path) / "scene.json"
            scene.write_text(json.dumps({"mapId": "test-map", "ego": {"vehicleProfile": "test-car"}}))
            resources = {"maps": [path + "/test-map"], "vehicles": [path + "/test-car"]}
            config = {"kind": "world", "source": str(scene), "map": "", "vehicle": ""}
            with patch("task_service.catalog", return_value=resources):
                _inherit_scenario_environment(config)
            self.assertEqual(config["map"], path + "/test-map")
            self.assertEqual(config["vehicle"], path + "/test-car")
            config["map"] = "/explicit/map"
            with patch("task_service.catalog", side_effect=AssertionError("must retain explicit choices")):
                _inherit_scenario_environment(config)
            self.assertEqual(config["map"], "/explicit/map")

    def test_inheritance_rejects_missing_ambiguous_and_record_defaults(self):
        with tempfile.TemporaryDirectory() as path:
            scene = Path(path) / "scene.json"
            scene.write_text(json.dumps({"mapId": "test-map", "ego": {}}))
            config = {"kind": "world", "source": str(scene), "map": "/explicit/map", "vehicle": ""}
            with patch("task_service.catalog", return_value={"maps": [], "vehicles": []}):
                with self.assertRaisesRegex(ValueError, "no vehicle configuration"):
                    _inherit_scenario_environment(config)
            config.update(map="", vehicle="/explicit/car")
            with patch("task_service.catalog", return_value={"maps": ["/one/test-map", "/two/test-map"]}):
                with self.assertRaisesRegex(ValueError, "matches 2 resources.* /one/test-map; /two/test-map"):
                    _inherit_scenario_environment(config)
            config["kind"] = "bag"
            with self.assertRaisesRegex(ValueError, "LogSim requires an explicit"):
                _inherit_scenario_environment(config)

    def test_selected_module_dags_must_exist_in_task_snapshot(self):
        with tempfile.TemporaryDirectory() as path:
            runtime = Path(path)
            with self.assertRaisesRegex(ValueError, "fake_prediction has no DAG"):
                preflight_module_dags(runtime, ["fake_prediction"])
            dag = runtime / MODULES["fake_prediction"][0]
            dag.parent.mkdir(parents=True)
            dag.write_text((SIM_PKG / "fake_prediction/dag/fake_prediction.dag").read_text())
            preflight_module_dags(runtime, ["fake_prediction"])
            with self.assertRaisesRegex(ValueError, "ML_PLANNING has no DAG"):
                preflight_module_dags(runtime, ["fake_prediction", "ML_PLANNING"])

    def test_world_preflight_missing_schema_does_not_load_legacy_copy(self):
        import builtins
        original_import = builtins.__import__
        imports = []

        def import_without_installed_schema(name, *args, **kwargs):
            imports.append(name)
            if name == "modules.simulation.worldsim.proto.scenario_pb2":
                raise ModuleNotFoundError("Canonical WorldSim schema is not installed")
            if name == "simulation.worldsim.proto.scenario_pb2":
                self.fail("Legacy schema would conflict with the next canonical import")
            return original_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as path:
            fixture = Path(path) / "scene.json"
            fixture.write_text('{}')
            with patch("builtins.__import__", side_effect=import_without_installed_schema):
                with self.assertRaisesRegex(ModuleNotFoundError, "Canonical WorldSim"):
                    preflight_world(fixture)
            # The same process must recover after installation without retaining
            # a legacy descriptor under the same protobuf symbol names.
            preflight_world(fixture)
            preflight_world(fixture)
        from modules.simulation.worldsim.proto.scenario_pb2 import Scenario
        self.assertEqual(Scenario.DESCRIPTOR.file.name,
                         "modules/simulation/worldsim/proto/scenario.proto")
        self.assertNotIn("simulation.worldsim.proto.scenario_pb2", imports)

    def test_event_protocol_keeps_command_replies_separate(self):
        import subprocess
        import sys
        import task_service
        with tempfile.TemporaryDirectory() as path:
            (Path(path) / "jobs.json").write_text(json.dumps({"old": {"id": "old", "stage": "completed"}}))
            result = subprocess.run([sys.executable, task_service.__file__, "--state-dir", path, "--events"],
                input='{"action":"subscribe"}\n{"action":"list"}\n{"action":"unknown"}\n',
                text=True, capture_output=True, timeout=10, check=True)
            lines = [json.loads(line) for line in result.stdout.splitlines()]
            events = [line for line in lines if line.get("event") == "simulation_jobs"]
            replies = [line for line in lines if "event" not in line]
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0]["snapshot"])
            self.assertEqual(events[0]["jobs"], [{"id": "old", "stage": "completed"}])
            self.assertEqual([reply["status"] for reply in replies], ["ok", "ok", "error"])
            self.assertEqual(replies[1]["jobs"], events[0]["jobs"])

    def test_events_snapshot_delta_idle_and_shutdown(self):
        import threading
        gate = threading.Event()
        with tempfile.TemporaryDirectory() as path, patch("task_service.validate", side_effect=lambda x: x), patch.object(TaskService, "run", lambda *_: gate.wait(3)):
            service = TaskService(path)
            try:
                snapshot = service.events()
                self.assertTrue(snapshot["snapshot"])
                self.assertEqual(snapshot["jobs"], [])
                self.assertIsNone(service.events(snapshot["revision"], timeout=0))
                first = service.request({"action": "enqueue", "config": {}})["id"]
                second = service.request({"action": "enqueue", "config": {}})["id"]
                delta = service.events(snapshot["revision"], timeout=0)
                self.assertFalse(delta["snapshot"])
                self.assertEqual([j["id"] for j in delta["jobs"]], [first, second])
                service.update(first, stage="simulation_running", progress=0.5)
                service.update(first, stage="completed", progress=1)
                latest = service.events(delta["revision"], timeout=0)
                self.assertEqual(len(latest["jobs"]), 1)
                self.assertEqual(latest["jobs"][0]["stage"], "completed")
                self.assertEqual(delta["jobs"][0]["stage"], "queued")
                service.request({"action": "cancel", "id": second})
                cancelled = service.events(latest["revision"], timeout=0)
                self.assertEqual(cancelled["jobs"][0]["stage"], "cancelled")
                received = []
                waiter = threading.Thread(target=lambda: received.append(service.events(cancelled["revision"], timeout=2)))
                waiter.start()
                service.update(first, analysis={"result": "PASS"})
                waiter.join(2)
                self.assertFalse(waiter.is_alive())
                self.assertEqual(received[0]["jobs"][0]["analysis"], {"result": "PASS"})
            finally:
                gate.set()
                service.close()
            self.assertIsNone(service.events(service.revision, timeout=0))

    def test_editor_project_has_actionable_export_error(self):
        with tempfile.TemporaryDirectory() as path:
            fixture = Path(path) / "scene.json"
            for value in ({"kind": "mine-project", "scenario": {}},
                          {"agents": [{"type": "ego"}]}):
                fixture.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "导出 WorldSim 场景"):
                    preflight_world(fixture)
            fixture.write_text('{"kind":"unknown-format"}')
            from google.protobuf.json_format import ParseError
            with self.assertRaisesRegex(ParseError, 'no field named "kind"'):
                preflight_world(fixture)

    def test_commented_plugin_is_not_a_dependency(self):
        with tempfile.TemporaryDirectory() as path:
            conf = Path(path) / "modules/planning/planning_component/conf"
            conf.mkdir(parents=True)
            for name in ("public_road_planner_config.pb.txt", "traffic_rule_config.pb.txt"):
                (conf / name).write_text('# type: "MissingCommentedPlugin"\n')
            preflight_plugins(Path(path), ["PLANNING"])
            (conf / "public_road_planner_config.pb.txt").write_text('scenario { type: "MissingActivePlugin" }\n')
            with self.assertRaisesRegex(ValueError, "MissingActivePlugin"):
                preflight_plugins(Path(path), ["PLANNING"])

    def test_world_preflight_does_not_ignore_unknown_actions(self):
        with tempfile.TemporaryDirectory() as path:
            fixture = Path(path) / "scene.json"
            fixture.write_text(json.dumps({"ego":{"position":{"x":1}}, "triggers":[
                {"id":"bad", "type":"TRIGGER_TYPE_TIME", "actions":[
                    {"kind":"ACTION_STOP", "targetAgentId":"ego"}]}]}))
            with self.assertRaisesRegex(ValueError, "direct ego trigger actions"):
                preflight_world(fixture)
            fixture.write_text('{"duration":"NaN"}')
            with self.assertRaisesRegex(ValueError, "Non-finite"):
                preflight_world(fixture)

    def test_incompatible_profile_is_explicit_failure(self):
        profile = ROOT / "data/bag/data_with_map/extracted/Jiyu_01"
        if not profile.is_dir():
            self.skipTest("Legacy profile fixture unavailable")
        with self.assertRaisesRegex(ValueError, "unavailable planning plugins"):
            preflight_plugins(profile, ["PLANNING"])

    def test_algorithm_comparison_preserves_command_changes(self):
        # Imports the actual installed Apollo schema through the comparator.
        algorithm_payload("/apollo/control", b"")
        from modules.common_msgs.control_msgs.control_cmd_pb2 import ControlCommand
        a = ControlCommand(acceleration=1.0, steering_target=-3.0)
        b = ControlCommand.FromString(a.SerializeToString())
        a.latency_stats.total_time_ms = 1
        b.latency_stats.total_time_ms = 2
        self.assertEqual(algorithm_payload("/apollo/control", a.SerializeToString()),
                         algorithm_payload("/apollo/control", b.SerializeToString()))
        b.acceleration = 1.0000000001
        self.assertNotEqual(algorithm_payload("/apollo/control", a.SerializeToString()),
                            algorithm_payload("/apollo/control", b.SerializeToString()))

    def test_comparison_is_not_a_constant_pass(self):
        with patch("bag_diff.messages", side_effect=[iter([("a", 10, b"x")]), iter([("a", 10, b"y")])]):
            self.assertEqual(compare("left", "right")["result"], "FAIL")
        with patch("bag_diff.messages", side_effect=[iter([("a", 10, b"x")]), iter([("a", 11, b"x")])]):
            self.assertEqual(compare("left", "right")["result"], "FAIL")
        with patch("bag_diff.messages", side_effect=[iter([("a", 10, b"x")]), iter([("a", 10, b"x")])]):
            self.assertEqual(compare("left", "right")["result"], "PASS")

    def test_missing_file_is_error_not_pass(self):
        with self.assertRaises(ValueError):
            compare("/missing/left.record", "/missing/right.record")

    def test_unsupported_input_rejected(self):
        with self.assertRaises(ValueError):
            validate({"kind": "shell", "source": "anything"})

    def test_restart_does_not_claim_inflight_task_completed(self):
        with tempfile.TemporaryDirectory() as path:
            (Path(path) / "jobs.json").write_text(json.dumps({"old": {"stage": "simulation_running"}}))
            service = TaskService(path)
            try:
                self.assertEqual(service.jobs["old"]["stage"], "interrupted")
                with self.assertRaises(BlockingIOError):
                    TaskService(path)
            finally:
                service.close()

    def test_fifo_and_cancel_waiting_job(self):
        # Explicit unit double: exercises queue state, not algorithm correctness.
        import threading
        import time
        gate = threading.Event()
        calls = []
        def execute(service, job_id):
            calls.append(job_id)
            gate.wait(2)
            service.update(job_id, stage="completed")
        with tempfile.TemporaryDirectory() as path, patch("task_service.validate", side_effect=lambda x:x), patch.object(TaskService, "run", execute):
            service = TaskService(path)
            try:
                first = service.request({"action":"enqueue", "config":{}})["id"]
                for _ in range(100):
                    if calls: break
                    time.sleep(.01)
                second = service.request({"action":"enqueue", "config":{}})["id"]
                third = service.request({"action":"enqueue", "config":{}})["id"]
                service.request({"action":"cancel", "id":second})
                gate.set()
                for _ in range(100):
                    if len(calls) == 2: break
                    time.sleep(.01)
                self.assertEqual(calls, [first, third])
                self.assertEqual(service.jobs[second]["stage"], "cancelled")
            finally:
                gate.set()
                service.close()


if __name__ == "__main__":
    unittest.main()
