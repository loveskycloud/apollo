import unittest

from mcap_playback_plan import clock_plan as plan


class PlaybackPlanTests(unittest.TestCase):
    def setUp(self):
        self.index = {"/camera/A": {"keys": [8_000_000_000, 10_000_000_000]},
                      "/camera/B": {"keys": [9_000_000_000, 11_000_000_000]}}

    def test_initial_window_reaches_all_first_keyframes(self):
        result = plan(self.index, 0, 500_000_000, True, list(self.index))
        self.assertEqual(result["seek_ns"], 9_000_000_000)
        self.assertLessEqual(result["import_begin_ns"], 8_000_000_000)
        self.assertGreater(result["end_ns"], result["seek_ns"])

    def test_paused_seek_loads_previous_keyframe_without_moving_time(self):
        result = plan(self.index, 10_500_000_000, 12_000_000_000, False, list(self.index))
        self.assertEqual(result["seek_ns"], 10_500_000_000)
        self.assertEqual(result["import_begin_ns"], 9_000_000_000)

    def test_apollo_camera_checkbox_maps_to_semantic_payload(self):
        result = plan(self.index, 0, 1, True, ["/apollo/camera/A/compressed"])
        self.assertIn("/camera/A", result["topics"])
        self.assertEqual(result["seek_ns"], 8_000_000_000)

    def test_pre_keyframe_seek_does_not_invent_a_picture(self):
        result = plan(self.index, 1, 2, False, list(self.index))
        self.assertEqual(result["seek_ns"], 1)
        self.assertGreater(result["ready_ns"], result["end_ns"])

    def test_missing_keyframes_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError, "No self-contained"):
            plan({"/camera/A": {"keys": []}}, 0, 10, True, ["/camera/A"])

    def test_lidar_only_open_starts_at_actual_first_sample(self):
        result = plan({"/lidar/up/points": {"first": 125_000_000}},
                      0, 2_000_000_000, True, ["/lidar/up/points"])
        self.assertEqual(result["seek_ns"], 125_000_000)
        self.assertEqual(result["import_begin_ns"], 0)

    def test_lidar_seek_does_not_move_to_first_camera_keyframe(self):
        index = dict(self.index, **{"/lidar/up/points": {"first": 125_000_000}})
        result = plan(index, 2_000_000_000, 4_000_000_000, False, list(index))
        self.assertEqual(result["seek_ns"], 2_000_000_000)
        self.assertEqual(result["import_begin_ns"], 2_000_000_000)
        self.assertEqual(result["topic_begin_ns"]["/lidar/up/points"], 2_000_000_000)
        self.assertEqual(result["topic_begin_ns"]["/camera/A"], 8_000_000_000)


if __name__ == "__main__":
    unittest.main()
