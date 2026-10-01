"""Collision verdict across repeats and physical OBB geometry regressions."""
import subprocess
import tempfile
from pathlib import Path
import unittest

from task_service import collision_summary


class CollisionTests(unittest.TestCase):
    def test_any_repeat_collision_fails(self):
        clear = dict(status="PASS", complete=True, checked_frames=100, collision_count=0)
        hit = dict(status="FAIL", complete=True, checked_frames=100, collision_count=1)
        self.assertEqual(collision_summary([clear, hit])["status"], "FAIL")
        self.assertEqual(collision_summary([hit, clear])["status"], "FAIL")
        self.assertEqual(collision_summary([clear, clear])["status"], "PASS")

    def test_missing_or_incomplete_evidence_cannot_pass(self):
        self.assertEqual(collision_summary([])["status"], "NOT_EVALUATED")
        self.assertEqual(collision_summary([{}])["status"], "INCOMPLETE")
        self.assertEqual(collision_summary([dict(status="PASS", complete=False)])["status"], "INCOMPLETE")

    def test_oriented_physical_footprints(self):
        header = Path(__file__).resolve().parents[1] / "worldsim/core/collision.h"
        code = r'''
#include <cassert>
#include "HEADER"
using namespace apollo::simulation::worldsim;
int main() {
  const BodyBox ego{.26, 0, 0, .36, .25};
  assert(BodiesCollide(ego, {.81,0,0,.2,.2})); // front .62, other rear .61
  assert(BodiesCollide(ego, {.82,0,0,.2,.2})); // touching
  assert(!BodiesCollide(ego, {.821,0,0,.2,.2}));
  assert(!BodiesCollide(ego, {-.301,0,0,.2,.2})); // rear -.1
  assert(BodiesCollide(ego, {0,.44,0,.2,.2}));
  assert(!BodiesCollide(ego, {0,.451,0,.2,.2}));
  const BodyBox rotated{0,0,1.5707963267948966,.5,.1};
  assert(BodiesCollide(rotated, {0,.55,0,.1,.1}));
  assert(!BodiesCollide(rotated, {.3,0,0,.1,.1}));
}
'''.replace("HEADER", str(header))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "collision.cc"
            binary = Path(directory) / "collision"
            source.write_text(code)
            subprocess.run(["g++", "-std=c++17", str(source), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    unittest.main()
