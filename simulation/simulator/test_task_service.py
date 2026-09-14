import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_service import ROOT, TaskService, validate, preflight_plugins, preflight_world
from bag_diff import compare, algorithm_payload


class QueueTests(unittest.TestCase):
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
