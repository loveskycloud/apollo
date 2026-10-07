"""Exercise the compiled launcher without starting or changing a live service.

WEB_MONITOR_TEST_BINARY=/path/to/binary/bin/web_monitor_main python3 test_launcher.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("WEB_MONITOR_TEST_BINARY"), "provide the compiled launcher")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="web-monitor-launcher-")
        self.root = Path(self.temporary.name) / "moved package ' with spaces"
        self.binary = self.root / "bin/web_monitor_main"
        self.binary.parent.mkdir(parents=True)
        shutil.copy2(os.environ["WEB_MONITOR_TEST_BINARY"], self.binary)
        self.layouts = self.root / "modules/simulation/web_monitor/layouts"
        self.layouts.mkdir(parents=True)
        self.viewer = self.root / "bin/rerun"
        self.viewer.write_text('#!/usr/bin/python3\nimport json,os,sys\n'
                               'print(json.dumps({"binary":sys.argv[0],"argv":sys.argv[1:],"layout":os.environ["AD_LAYOUT_DIR"]}))\n'
                               'sys.exit(7)\n')
        self.viewer.chmod(0o755)
        self.env = dict(os.environ)
        for key in ("WEB_MONITOR_RERUN", "AD_LAYOUT_DIR"):
            self.env.pop(key, None)

    def tearDown(self):
        self.temporary.cleanup()

    def test_moved_package_from_other_directory_preserves_arguments_and_exit(self):
        recording = "record ' $(must-stay-literal).rrd"
        result = subprocess.run([self.binary, "--recording=" + recording,
                                 "--grpc_host=127.0.0.1"], cwd="/tmp", env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 7, result.stderr)
        report = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(report["argv"][-1], recording)
        self.assertIn("http://*:*", report["argv"])
        self.assertEqual(Path(report["layout"]).resolve(), self.layouts)
        self.assertEqual(report["binary"], str(self.viewer))

    def test_explicit_missing_viewer_fails_without_using_other_installations(self):
        self.env["WEB_MONITOR_RERUN"] = str(self.root / "missing-rerun")
        result = subprocess.run([self.binary], cwd="/tmp", env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("WEB_MONITOR_RERUN is not executable", result.stderr)
        self.assertNotIn("Open in browser", result.stdout)

    def test_explicit_missing_layout_fails_before_viewer_start(self):
        result = subprocess.run([self.binary, "--layout_dir=" + str(self.root / "missing")],
                                cwd="/tmp", env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Layout directory is missing", result.stderr)
        self.assertNotIn("Open in browser", result.stdout)


if __name__ == "__main__":
    unittest.main()
