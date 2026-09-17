import os
import sys
import tempfile
import unittest
import hashlib
import json
from pathlib import Path

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
sys.path.insert(0, "/opt/apollo/neo/python")
import numpy as np
from google.protobuf import text_format, json_format
from hd_map import map_pb2, load_map, resolve_map, build_map_meshes, ribbon, MapMesh


class MapTests(unittest.TestCase):
    def fixture(self):
        data = map_pb2.Map()
        lane = data.lane.add()
        lane.id.id = "test"
        for curve, y in [(lane.central_curve, 0), (lane.left_boundary.curve, 2), (lane.right_boundary.curve, -2)]:
            line = curve.segment.add().line_segment
            for x in [0., 5., 10.]:
                line.point.add(x=500000.+x, y=4000000.+y, z=7.)
        return data

    def test_real_boundary_triangulation_and_shared_origin(self):
        meshes = build_map_meshes(self.fixture(), [500000., 4000000., 7.])
        mesh = meshes["/hdmap/road_surface"]
        p = np.array(mesh.xyz).reshape(-1, 3)
        t = p[np.array(mesh.triangle_indices).reshape(-1, 3)]
        area = np.abs(np.cross(t[:, 1, :2]-t[:, 0, :2], t[:, 2, :2]-t[:, 0, :2])).sum()/2
        self.assertAlmostEqual(area, 40.)
        np.testing.assert_allclose(p[:, 2], .01)
        self.assertEqual(mesh.rgba, 0x2E333DFF)
        self.assertEqual(MapMesh.FromString(mesh.SerializeToString()), mesh)
        self.assertEqual(meshes["/hdmap/lane_boundaries"].rgba, 0xEBECEEFF)
        self.assertEqual(meshes["/hdmap/lane_centerlines"].rgba, 0x6B8F71FF)

    def test_marks_preserve_small_offsets_before_float32(self):
        mesh = build_map_meshes(self.fixture(), [500000.01, 4000000.01, 7.])["/hdmap/lane_centerlines"]
        p = np.array(mesh.xyz).reshape(-1, 3)
        np.testing.assert_allclose(p[0], [-.01, .03, .035], atol=1e-8)
        self.assertAlmostEqual(np.linalg.norm(p[0]-p[1]), .08)

    def test_invalid_data_and_missing_origin_fail_loud(self):
        with self.assertRaisesRegex(ValueError, "origin"):
            build_map_meshes(self.fixture(), None)
        bad = self.fixture()
        bad.lane[0].ClearField("left_boundary")
        with self.assertRaisesRegex(ValueError, "boundaries"):
            build_map_meshes(bad, [0, 0, 0])
        with self.assertRaisesRegex(ValueError, "degenerate"):
            ribbon(np.zeros((2, 3)), .07, .04)

    def test_two_dimensional_map_uses_shared_display_plane(self):
        data = self.fixture()
        lane = data.lane[0]
        for curve in (lane.central_curve, lane.left_boundary.curve, lane.right_boundary.curve):
            for point in curve.segment[0].line_segment.point:
                point.ClearField("z")
        mesh = build_map_meshes(data, [500000., 4000000., 7.])["/hdmap/lane_centerlines"]
        np.testing.assert_allclose(np.array(mesh.xyz).reshape(-1, 3)[:,2], .035)

    def test_bin_txt_json_and_no_guessed_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = self.fixture()
            for ext, blob in [("bin", data.SerializeToString()), ("txt", text_format.MessageToString(data).encode()), ("json", json_format.MessageToJson(data).encode())]:
                path = root / ("base_map." + ext)
                path.write_bytes(blob)
                self.assertEqual(load_map(path), data)
            self.assertIsNone(resolve_map([root / "manual.record"]))
            self.assertEqual(resolve_map([root / "manual.record"], root), root / "base_map.bin")

    def test_simulation_snapshot_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "map").mkdir()
            path = root / "map/base_map.txt"
            path.write_text(text_format.MessageToString(self.fixture()))
            (root / "manifest.json").write_text(json.dumps({"config":{"map":"original-map"}, "sha256":{"map/base_map.txt":hashlib.sha256(path.read_bytes()).hexdigest()}}))
            record = root / "run-1/simulation.record"
            self.assertEqual(resolve_map([record]), path)
            path.write_text("modified")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                resolve_map([record])


if __name__ == "__main__":
    unittest.main()
