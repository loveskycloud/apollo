"""Suite scheduling contracts; these doubles do not validate planner quality."""
import json
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
             patch("task_service.validate", side_effect=[{}, ValueError("invalid scene")]):
            service = TaskService(root, workers=3)
            try:
                with self.assertRaisesRegex(ValueError, "invalid scene"):
                    service.request(self.suite(root))
                self.assertEqual(service.jobs, {})
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
