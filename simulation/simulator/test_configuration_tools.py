import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from configuration_tools import (GLOBAL_FLAGS, VEHICLE_CONFIG,
    apply_workspace_configuration, update_global_flagfile, vehicle_geometry)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.map = self.root / "maps/chosen"
        self.map.mkdir(parents=True)
        (self.map / "base_map.txt").write_text("map")
        self.flags = self.root / GLOBAL_FLAGS
        self.flags.parent.mkdir(parents=True)
        self.flags.write_text("# keep\n--map_dir=old\n--log_dir=data/log\n--half_vehicle_width=9\n--map_dir=stale\n")
        (self.root / VEHICLE_CONFIG).write_text("vehicle_param { width: 2.11 }")
        self.profile = self.make_profile("ranger", .86)

    def make_profile(self, name, width):
        profile = self.root / "profiles" / name
        path = profile / VEHICLE_CONFIG
        path.parent.mkdir(parents=True)
        path.write_text(f"vehicle_param {{ width: {width} }}")
        # Allow linking into modules/<pkg> without creating buildtool stubs.
        for pkg in ("common", "planning"):
            cyber = self.root / "modules" / pkg / "cyberfile.xml"
            cyber.parent.mkdir(parents=True, exist_ok=True)
            if not cyber.exists():
                cyber.write_text(f'<package><name>{pkg}</name></package>\n')
        config = profile / "modules/planning/conf/test.pb.txt"
        config.parent.mkdir(parents=True)
        config.write_text(name)
        return profile

    def apply(self, profile=None):
        profile = profile or self.profile
        return apply_workspace_configuration(self.root, profile, self.map, profile / VEHICLE_CONFIG)

    def test_actual_links_map_width_and_no_backup(self):
        result = self.apply()
        self.assertEqual(result["half_vehicle_width"], .43)
        self.assertEqual((self.root / "profiles/current").resolve(), self.profile)
        self.assertEqual((self.root / VEHICLE_CONFIG).resolve(), self.profile / VEHICLE_CONFIG)
        self.assertEqual((self.root / "modules/planning/conf/test.pb.txt").read_text(), "ranger")
        text = self.flags.read_text()
        self.assertIn("--map_dir=" + str(self.map), text)
        self.assertIn("--half_vehicle_width=0.43", text)
        self.assertEqual(text.count("--map_dir="), 1)
        self.assertIn("--log_dir=data/log", text)
        self.assertIn("# keep", text)
        self.assertFalse(any("backup" in p.name or p.suffix == ".bak" for p in self.root.rglob("*")))

    def test_profile_switch_updates_all_file_links(self):
        self.apply()
        second = self.make_profile("other", .5)
        self.apply(second)
        self.assertIn("--half_vehicle_width=0.25", self.flags.read_text())
        self.assertEqual((self.root / "modules/planning/conf/test.pb.txt").read_text(), "other")

    def test_missing_new_profile_field_does_not_recover_installed_defaults(self):
        self.apply()
        second = self.make_profile("other", .5)
        (second / "modules/planning/conf/test.pb.txt").unlink()
        with self.assertRaisesRegex(ValueError, "missing active configuration"):
            self.apply(second)
        self.assertEqual((self.root / "profiles/current").resolve(), self.profile)

    def test_invalid_width_or_schema_is_error(self):
        path = self.profile / VEHICLE_CONFIG
        for text in ("vehicle_param {}", "vehicle_param {width: 0}",
                     "vehicle_param {width: nan}", "vehicle_param {width: -1}",
                     "vehicle_param {unknown: 1}"):
            path.write_text(text)
            with self.assertRaises(Exception):
                self.apply()
            self.assertFalse((self.root / "profiles/current").exists())

    def test_mismatched_vehicle_is_error_before_apply(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            apply_workspace_configuration(self.root, self.profile, self.map, self.root / VEHICLE_CONFIG)
        self.assertFalse((self.root / "profiles/current").exists())

    def test_io_failure_is_not_reported_success_or_rolled_back(self):
        with patch("configuration_tools.atomic_text", side_effect=OSError("read-only filesystem")):
            with self.assertRaisesRegex(OSError, "read-only filesystem"):
                self.apply()
        self.assertTrue((self.root / VEHICLE_CONFIG).is_symlink())
        self.assertFalse(any("backup" in p.name for p in self.root.rglob("*")))

    def test_global_flagfile_symlink_does_not_edit_profile(self):
        original = "# profile\n--map_dir=profile_old\n"
        source = self.profile / GLOBAL_FLAGS
        source.write_text(original)
        self.flags.unlink()
        self.flags.symlink_to(source)
        update_global_flagfile(self.flags, self.map, self.profile / VEHICLE_CONFIG)
        self.assertEqual(source.read_text(), original)
        self.assertFalse(self.flags.is_symlink())
        self.assertIn("--half_vehicle_width=0.43", self.flags.read_text())

    def test_broken_profile_file_is_error_before_apply(self):
        (self.profile / "missing.txt").symlink_to(self.profile / "absent")
        with self.assertRaisesRegex(ValueError, "Broken profile input"):
            self.apply()
        self.assertFalse((self.root / "profiles/current").exists())

    def test_workspace_vehicle_is_not_replaced_with_self_link(self):
        apply_workspace_configuration(self.root, None, self.map, self.root / VEHICLE_CONFIG)
        self.assertFalse((self.root / VEHICLE_CONFIG).is_symlink())
        self.assertEqual(vehicle_geometry(self.root / VEHICLE_CONFIG)["half_vehicle_width"], 1.055)


if __name__ == "__main__":
    unittest.main()
