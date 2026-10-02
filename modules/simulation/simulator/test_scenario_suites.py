"""Suite scheduling contracts; these doubles do not validate planner quality."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from task_service import TaskService, TERMINAL, preflight_world, SIM_PKG


class SuiteTests(unittest.TestCase):
    def test_thirty_workers_and_limit_validation(self):
        with tempfile.TemporaryDirectory() as root:
            service = TaskService(root, workers=30)
            try:
                self.assertEqual(service.worker_count, 30)
                self.assertEqual(len(service.threads), 30)
            finally:
                service.close()
            with self.assertRaisesRegex(ValueError, "1..30"):
                TaskService(root, workers=31)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("queue did not reach expected state")
            time.sleep(.01)

    def suite(self, root):
        path = Path(root) / "example.suite.json"
        path.write_text(json.dumps({"kind": "worldsim-suite", "version": 1,
            "name": "test", "mapId": "test-map", "scenarios": [f"{i}.json" for i in range(5)]}))
        return {"action": "enqueue_suite", "config": {"suite": str(path),
            "map": "/maps/test-map", "concurrency": 3}}

    def test_bounded_parallel_failures_do_not_skip_other_members(self):
        gate = threading.Event()
        active = set()
        peak = []
        guard = threading.Lock()

        def execute(service, job_id):
            with guard:
                active.add(job_id)
                peak.append(len(active))
            service.update(job_id, stage="simulation_running")
            gate.wait(3)
            with guard:
                active.remove(job_id)
            if service.jobs[job_id]["suite_index"] == 2:
                raise RuntimeError("deliberate member failure")
            service.update(job_id, stage="completed")

        with tempfile.TemporaryDirectory() as root, \
             patch("task_service.resolve", side_effect=Path), \
             patch("task_service.validate", side_effect=lambda c: c), \
             patch("task_service.preflight_world"), \
             patch.object(TaskService, "run", execute):
            service = TaskService(root, workers=3)
            try:
                reply = service.request(self.suite(root))
                self.wait_for(lambda: len(active) == 3)
                self.assertEqual(len(reply["ids"]), 5)
                self.assertEqual(len(set(j["suite_id"] for j in service.jobs.values())), 1)
                gate.set()
                self.wait_for(lambda: all(j["stage"] in TERMINAL for j in service.jobs.values()))
                self.assertEqual(max(peak), 3)
                self.assertEqual([j["stage"] for j in service.jobs.values()].count("completed"), 4)
                self.assertEqual(service.jobs[reply["ids"][1]]["stage"], "failed")
            finally:
                gate.set()
                service.close()

    def test_single_concurrency_and_cancel_queued_member(self):
        gate = threading.Event()
        calls = []

        def execute(service, job_id):
            calls.append(job_id)
            service.update(job_id, stage="simulation_running")
            gate.wait(3)
            service.update(job_id, stage="completed")

        with tempfile.TemporaryDirectory() as root, \
             patch("task_service.resolve", side_effect=Path), \
             patch("task_service.validate", side_effect=lambda c: c), \
             patch("task_service.preflight_world"), \
             patch.object(TaskService, "run", execute):
            service = TaskService(root, workers=3)
            try:
                request = self.suite(root)
                request["config"]["concurrency"] = 1
                reply = service.request(request)
                self.wait_for(lambda: len(calls) == 1)
                service.request({"action": "cancel", "id": reply["ids"][1]})
                time.sleep(.05)
                self.assertEqual(len(calls), 1)
                gate.set()
                self.wait_for(lambda: all(j["stage"] in TERMINAL for j in service.jobs.values()))
                self.assertEqual(calls, [reply["ids"][i] for i in (0, 2, 3, 4)])
                self.assertEqual(service.jobs[reply["ids"][1]]["stage"], "cancelled")
            finally:
                gate.set()
                service.close()

    def test_invalid_member_rejects_whole_suite(self):
        with tempfile.TemporaryDirectory() as root, \
             patch("task_service.resolve", side_effect=Path), \
             patch("task_service.validate", side_effect=[{"map": "/maps/test-map"}, ValueError("invalid scene")]):
            service = TaskService(root, workers=3)
            try:
                with self.assertRaisesRegex(ValueError, "invalid scene"):
                    service.request(self.suite(root))
                self.assertEqual(service.jobs, {})
            finally:
                service.close()

    def test_cancel_suite_stops_process_and_queue_only_for_that_submission(self):
        children = {}

        def execute(service, job_id):
            with service.lock:
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                         start_new_session=True)
                children[job_id] = service.children[job_id] = child
                service.update(job_id, stage="simulation_running")
            while child.poll() is None:
                service.check_cancel(job_id)
                time.sleep(.01)
            service.check_cancel(job_id)

        with tempfile.TemporaryDirectory() as root, \
             patch("task_service.resolve", side_effect=Path), \
             patch("task_service.validate", side_effect=copy.deepcopy), \
             patch("task_service.preflight_world"), \
             patch.object(TaskService, "run", execute):
            service = TaskService(root)
            try:
                request = self.suite(root)
                request["config"]["concurrency"] = 1
                first = service.request(request)
                second = service.request(request)  # Same name, different submission.
                self.wait_for(lambda: first["ids"][0] in children)
                service.request({"action": "cancel_suite", "suite_id": first["suite_id"]})
                self.wait_for(lambda: not set(first["ids"]) & service.active_ids)
                self.assertTrue(all(service.jobs[i]["stage"] == "cancelled" for i in first["ids"]))
                self.assertTrue(all(service.jobs[i]["stage"] != "cancelled" for i in second["ids"]))
                self.assertIsNotNone(children[first["ids"][0]].poll())
                self.assertFalse(set(first["ids"]) & set(service.pending_ids))
            finally:
                service.close()

    def stored_members(self, service):
        ids = [f"{i:016x}" for i in range(1, 4)]
        for index, job_id in enumerate(ids):
            directory = service.root / job_id
            directory.mkdir()
            (directory / "simulation.record").write_bytes(b"test-owned-output")
            service.jobs[job_id] = dict(id=job_id, suite_id="original", suite_name="same name",
                suite_index=index + 1, suite_size=3, concurrency=1,
                stage=["failed", "cancelled", "completed"][index],
                config=dict(kind="world", source=f"/scenarios/{index}.json", repeat=index + 1,
                            modules=["PLANNING", "fake_prediction", "ROUTING"], seed=42),
                created_at=1, progress=100, history=[], outputs=[str(directory / "simulation.record")])
        service.save()
        return ids

    def test_retry_all_preserves_originals_and_revalidates_before_publishing(self):
        with tempfile.TemporaryDirectory() as root, \
             patch("task_service.validate", side_effect=copy.deepcopy), \
             patch("task_service.preflight_world") as preflight, \
             patch.object(TaskService, "run", lambda service, job_id: service.update(job_id, stage="completed")):
            service = TaskService(root)
            try:
                with service.lock:
                    ids = self.stored_members(service)
                    originals = copy.deepcopy(service.jobs)
                    service.jobs[ids[1]]["stage"] = "queued"
                    with self.assertRaisesRegex(ValueError, "entire suite"):
                        service.request(dict(action="retry_suite", suite_id="original"))
                    service.jobs[ids[1]]["stage"] = "cancelled"
                    with patch("task_service.validate", side_effect=[originals[ids[0]]["config"], ValueError("missing resource")]):
                        with self.assertRaisesRegex(ValueError, "missing resource"):
                            service.request(dict(action="retry_suite", suite_id="original"))
                    self.assertEqual(service.jobs, originals)
                    with patch("task_service.preflight_world", side_effect=ValueError("invalid scenario")):
                        with self.assertRaisesRegex(ValueError, "invalid scenario"):
                            service.request(dict(action="retry_suite", suite_id="original"))
                    self.assertEqual(service.jobs, originals)
                    reply = service.request(dict(action="retry_suite", suite_id="original"))
                    self.assertNotEqual(reply["suite_id"], "original")
                    self.assertEqual(len(reply["ids"]), 3)
                    for old, new in zip(ids, reply["ids"]):
                        self.assertEqual(service.jobs[old], originals[old])
                        job = service.jobs[new]
                        self.assertEqual(job["config"], originals[old]["config"])
                        self.assertEqual(job["stage"], "queued")
                        self.assertEqual(job["outputs"], [])
                        self.assertEqual(job["progress"], 0)
                        self.assertEqual(job["retry_of_suite_id"], "original")
                        self.assertEqual(job["concurrency"], 1)
                        self.assertGreater(job["created_at"], 1)
                    self.assertEqual(preflight.call_count, 3)
                self.wait_for(lambda: not service.active_ids and not service.pending_ids)
                self.assertTrue(all(service.jobs[i]["stage"] == "completed" for i in reply["ids"]))
            finally:
                service.close()

    def test_suite_delete_preflights_all_and_deletes_owned_outputs_and_cache(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache, \
             patch.dict("os.environ", {"WEB_MONITOR_CONVERT_CACHE": cache}):
            service = TaskService(root)
            try:
                ids = self.stored_members(service)
                other = "0000000000000004"
                service.jobs[other] = dict(service.jobs[ids[0]], id=other, suite_id="another")
                service.save()
                cache_root = Path(cache)
                for i in ids:
                    (cache_root / (i + ".source.json")).write_text(json.dumps({"source": str(service.root / i / "simulation.record")}))
                    (cache_root / (i + ".progress.json")).write_text('{"status":"done"}')
                    (cache_root / (i + ".mcap")).write_bytes(b"test-cache")
                service.active_ids.add(ids[-1])
                with self.assertRaisesRegex(ValueError, "wait for it to stop"):
                    service.request(dict(action="delete_suite", suite_id="original"))
                self.assertTrue(all((service.root / i).exists() for i in ids))
                service.active_ids.clear()
                (cache_root / (ids[-1] + ".progress.json")).write_text('{"status":"running"}')
                with self.assertRaisesRegex(ValueError, "conversion"):
                    service.request(dict(action="delete_suite", suite_id="original"))
                self.assertEqual(len(list(cache_root.iterdir())), 9)
                (cache_root / (ids[-1] + ".progress.json")).write_text('{"status":"done"}')
                revision = service.revision
                reply = service.request(dict(action="delete_suite", suite_id="original"))
                self.assertEqual(reply["deleted_ids"], ids)
                self.assertEqual(list(service.jobs), [other])
                self.assertFalse(any((service.root / i).exists() for i in ids))
                self.assertEqual(list(cache_root.iterdir()), [])
                event = service.events(revision, timeout=0)
                self.assertTrue(event["snapshot"])
                self.assertEqual([j["id"] for j in event["jobs"]], [other])
                self.assertEqual(list(json.loads((service.root / "jobs.json").read_text())), [other])
            finally:
                service.active_ids.clear()
                service.close()

    def test_suite_delete_filesystem_failure_is_reported_and_successes_persisted(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as cache, \
             patch.dict("os.environ", {"WEB_MONITOR_CONVERT_CACHE": cache}):
            service = TaskService(root)
            try:
                ids = self.stored_members(service)
                revision = service.revision
                import shutil
                remove = shutil.rmtree
                def fail_second(path):
                    if path.name == ids[1]:
                        raise PermissionError("deliberate deletion failure")
                    remove(path)
                with patch("task_service.shutil.rmtree", side_effect=fail_second):
                    with self.assertRaisesRegex(PermissionError, "deliberate deletion failure"):
                        service.request(dict(action="delete_suite", suite_id="original"))
                self.assertEqual(list(service.jobs), ids[1:])
                self.assertEqual(list(json.loads((service.root / "jobs.json").read_text())), ids[1:])
                self.assertTrue(service.events(revision, timeout=0)["snapshot"])
                self.assertFalse((service.root / ids[0]).exists())
                self.assertTrue((service.root / ids[1]).exists())
            finally:
                service.close()

    def test_suite_actions_require_known_submission(self):
        with tempfile.TemporaryDirectory() as root:
            service = TaskService(root)
            try:
                for action in ("cancel_suite", "retry_suite", "delete_suite"):
                    for suite_id in (None, "", "unknown"):
                        with self.assertRaisesRegex(ValueError, "suite_id"):
                            service.request(dict(action=action, suite_id=suite_id))
            finally:
                service.close()

    def test_shipped_scenes_have_native_location_triggers_and_correct_map(self):
        root = SIM_PKG / "scene_editor/examples/beijing_zongyuan_1haolou"
        manifest = json.loads((root / "beijing_zongyuan_1haolou.suite.json").read_text())
        self.assertEqual(len(manifest["scenarios"]), 381)
        excluded={x['scenario'] for x in manifest['excluded']}
        self.assertEqual(len(excluded),15)
        self.assertFalse(excluded.intersection(manifest['scenarios']))
        trigger_count = 0
        unique = set()
        for member in manifest["scenarios"]:
            preflight_world(root / member)
            scene = json.loads((root / member).read_text())
            unique.add(json.dumps({k:scene[k] for k in ("ego", "agents", "triggers")},sort_keys=True))
            self.assertEqual(scene["mapId"], manifest["mapId"])
            self.assertGreater(scene["ego"]["position"]["y"], 8_000_000)
            trigger_count += sum(t["type"] == "TRIGGER_TYPE_LOCATION" for t in scene["triggers"])
        self.assertGreaterEqual(trigger_count, 100)
        self.assertGreaterEqual(len(unique), 100)
        coverage = json.loads((root / "coverage.json").read_text())
        clear = {c["anchor"] for c in coverage["scenarios"] if c["family"] == "clear"}
        self.assertEqual(len(clear), 76)
        families = {c["family"] for c in coverage["scenarios"]}
        self.assertTrue({"overtake", "vehicle_train", "pedestrian_group", "static_slalom", "mixed"} <= families)


if __name__ == "__main__":
    unittest.main()
