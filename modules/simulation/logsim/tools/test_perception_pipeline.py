from pathlib import Path
import tempfile
import unittest

from perception_pipeline import prepare_perception_pipeline
from simulation_manifest import channel_policy, load_manifest


class PerceptionPipelineTests(unittest.TestCase):
    def test_launch_uses_profile_dags_and_keeps_sensor_topics_unmapped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            component = root / "modules/perception/custom"
            component.mkdir(parents=True)
            (component / "output.pb.txt").write_text(
                'output_obstacles_channel_name: "/apollo/perception/obstacles"\n')
            for index, sensor in enumerate(("lidar/up/PointCloud2", "camera/front/image")):
                (component / f"{index}.dag").write_text(
                    'module_config { module_library: "library_%s.so" components { '
                    'class_name: "Component%s" config { name: "stage%s" '
                    'config_file_path: "/apollo/modules/perception/custom/output.pb.txt" '
                    'readers { channel: "/apollo/sensor/%s" } } } }' %
                    (index, index, index, sensor))
            launch = root / "perception.launch"
            launch.write_text('<cyber><module><dag_conf>/apollo/modules/perception/custom/0.dag</dag_conf>'
                              '<dag_conf>modules/perception/custom/1.dag</dag_conf></module></cyber>')
            target = root / "frozen.dag"
            original = load_manifest()
            manifest = prepare_perception_pipeline(root, launch, target, original)
            policy = channel_policy(["PERCEPTION", "PREDICTION"], manifest)
            for topic in ("/apollo/sensor/lidar/up/PointCloud2", "/apollo/sensor/camera/front/image"):
                self.assertIn(topic, policy["inject_channels"])
                self.assertIn(topic, policy["record_channels"])
                self.assertNotIn(topic, policy["bag_topic_mappings"])
            self.assertNotIn("/apollo/sensor/lidar16/compensator/PointCloud2", policy["inject_channels"])
            self.assertIn("/apollo/perception/obstacles", policy["suppress_channels"])
            self.assertIn("/bag/apollo/perception/obstacles", policy["record_channels"])
            self.assertIn("library_0.so", target.read_text())
            self.assertIn("library_1.so", target.read_text())
            self.assertIn(str(component / "output.pb.txt"), target.read_text())
            self.assertEqual(original, load_manifest())

    def test_missing_or_empty_pipeline_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launch = root / "perception.launch"
            launch.write_text('<cyber><module/></cyber>')
            with self.assertRaisesRegex(ValueError, "no DAGs"):
                prepare_perception_pipeline(root, launch, root / "frozen.dag", load_manifest())
            launch.write_text('<cyber><module><dag_conf>missing.dag</dag_conf></module></cyber>')
            with self.assertRaises(FileNotFoundError):
                prepare_perception_pipeline(root, launch, root / "frozen.dag", load_manifest())


if __name__ == "__main__":
    unittest.main()
