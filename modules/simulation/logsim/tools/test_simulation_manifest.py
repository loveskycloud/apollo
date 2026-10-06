import json
from pathlib import Path
import tempfile
import unittest

from simulation_manifest import load_manifest, record_topics, channel_policy


class SimulationManifestTests(unittest.TestCase):
    def test_control_only_keeps_upstream_planning_and_shared_pose(self):
        topics = record_topics(["CONTROL"], load_manifest())
        self.assertIn("/apollo/planning", topics)
        self.assertIn("/apollo/control", topics)
        self.assertIn("/apollo/localization/pose", topics)
        self.assertNotIn("/apollo/prediction", topics)
        self.assertEqual(topics.count("/apollo/localization/pose"), 1)

    def test_ml_records_both_command_status_outputs(self):
        topics = record_topics(["ROUTING", "ML_PLANNING"], load_manifest())
        self.assertIn("/apollo/raw_routing_request", topics)
        self.assertIn("/apollo/raw_routing_response", topics)
        self.assertIn("/apollo/planning/command_status", topics)
        self.assertIn("/apollo/planning/reference_line_offset_command_status", topics)
        self.assertNotIn("/apollo/control", topics)
        self.assertNotIn("/apollo/prediction", topics)

    def test_record_union_is_stable_and_deduplicated(self):
        manifest = load_manifest()
        selected = ["PREDICTION", "PLANNING", "CONTROL"]
        topics = record_topics(selected, manifest)
        self.assertEqual(topics, record_topics(selected, manifest))
        self.assertEqual(len(topics), len(set(topics)))
        self.assertIn("/apollo/planning/command", topics)

    def test_unknown_module_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Module missing"):
            record_topics(["TYPO"], load_manifest())

    def test_localization_replaces_live_pose_but_preserves_bag_pose(self):
        policy = channel_policy(["LOCALIZATION", "PLANNING"], load_manifest())
        self.assertNotIn("/apollo/localization/pose", policy["inject_channels"])
        self.assertNotIn("/apollo/planning", policy["inject_channels"])
        self.assertIn("/apollo/localization/pose", policy["suppress_channels"])
        self.assertIn("/apollo/localization/msf_status", policy["suppress_channels"])
        self.assertIn("/tf", policy["suppress_channels"])
        for topic in ("/apollo/sensor/gnss/odometry", "/apollo/sensor/gnss/corrected_imu",
                      "/apollo/sensor/gnss/ins_stat", "/tf_static"):
            self.assertIn(topic, policy["inject_channels"])
            self.assertIn("/bag" + topic, policy["record_channels"])
        self.assertEqual(policy["bag_topic_mappings"]["/apollo/localization/pose"],
                         "/bag/apollo/localization/pose")
        self.assertFalse(any(topic.startswith("/bag/") for topic in policy["inject_channels"]))

    def test_disabled_modules_use_original_pose_and_planning(self):
        policy = channel_policy(["CONTROL"], load_manifest())
        self.assertIn("/apollo/localization/pose", policy["inject_channels"])
        self.assertIn("/apollo/planning", policy["inject_channels"])
        self.assertNotIn("/apollo/planning", policy["suppress_channels"])
        self.assertIn("/bag/apollo/planning", policy["record_channels"])

    def test_perception_recomputes_obstacles_without_duplicate_sensor_inputs(self):
        policy = channel_policy(["PERCEPTION", "PREDICTION"], load_manifest())
        self.assertIn("/apollo/perception/obstacles", policy["suppress_channels"])
        self.assertNotIn("/apollo/perception/obstacles", policy["inject_channels"])
        for topic in ("/apollo/sensor/rslidar/up/PointCloud2", "/tf", "/tf_static"):
            self.assertIn(topic, policy["inject_channels"])
            self.assertNotIn(topic, policy["bag_topic_mappings"])
            self.assertNotIn("/bag" + topic, policy["record_channels"])
        self.assertEqual(policy["bag_topic_mappings"]["/apollo/perception/obstacles"],
                         "/bag/apollo/perception/obstacles")
        combined = channel_policy(["LOCALIZATION", "PERCEPTION", "PREDICTION"], load_manifest())
        self.assertNotIn("/tf", combined["inject_channels"])
        self.assertNotIn("/apollo/localization/pose", combined["inject_channels"])
        self.assertIn("/tf_static", combined["inject_channels"])
        with self.assertRaisesRegex(ValueError, "BAG sensor inputs"):
            channel_policy(["PERCEPTION"], load_manifest(), "WORLD")

    def test_world_has_no_bag_references_or_sensor_localization(self):
        policy = channel_policy(["fake_prediction", "PLANNING", "ROUTING"],
                                load_manifest(), "WORLD")
        self.assertEqual(policy["bag_topic_mappings"], {})
        self.assertFalse(any(topic.startswith("/bag/") for topic in policy["record_channels"]))
        with self.assertRaisesRegex(ValueError, "BAG sensor inputs"):
            channel_policy(["LOCALIZATION"], load_manifest(), "WORLD")

    def test_invalid_schema_is_an_error(self):
        cases = [
            {"common": [], "modules": {}},
            {"common": "pose", "modules": []},
            {"common": ["pose"], "modules": []},
            {"common": ["/pose", "/pose"], "modules": []},
            {"common": ["/invalid topic"], "modules": []},
            {"common": [], "modules": [{"name": "PLANNING", "input": [], "outputs": []}]},
            {"common": [], "modules": [
                {"name": "PLANNING", "inputs": [], "outputs": []},
                {"name": "PLANNING", "inputs": [], "outputs": []}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "simulation.manifest.json"
            for case in cases:
                with self.subTest(manifest=case):
                    path.write_text(json.dumps(case))
                    with self.assertRaises(ValueError):
                        load_manifest(path)


if __name__ == "__main__":
    unittest.main()
