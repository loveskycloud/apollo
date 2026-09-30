import csv
import tempfile
from pathlib import Path
import unittest

from driving_quality import analyze_trace


class QualityTest(unittest.TestCase):
    def metrics(self, offsets, obstacle=0):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"policy.csv"
            with path.open("w") as stream:
                writer = csv.DictWriter(stream, fieldnames=["s", "l", "speed", "timestamp", "curvature", "obs10", "obs11", "obs3", "obs12", "obs13", "obs5"])
                writer.writeheader()
                for i, offset in enumerate(offsets):
                    writer.writerow(dict(s=i*.5,l=offset,speed=.8,timestamp=i*.5,
                        curvature=0,obs10=0,obs11=0,obs3=.8,obs12=.2,obs13=.2,obs5=obstacle))
            return analyze_trace(path)

    def test_far_obstacle_does_not_excuse_lane_bias(self):
        self.assertEqual(self.metrics([.3]*20, 1)["status"], "FAIL")
        self.assertEqual(self.metrics([0]*20, 1)["status"], "PASS")

    def test_significant_repeated_reversals_fail(self):
        report = self.metrics([0,.1,.2,.1,0,-.1,-.2,-.1,0,.1,.2,.1,0,-.1,-.2])
        self.assertGreaterEqual(report["max_lateral_reversals_10s"], 3)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(self.metrics([-.01,.01]*20)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
