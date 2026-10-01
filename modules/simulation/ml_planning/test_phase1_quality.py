import unittest
from phase1_native import quality_metrics


class QualityTest(unittest.TestCase):
    def rows(self,speeds):
        return [dict(timestamp=1+i*.1,speed=v,l=0,shield=0) for i,v in enumerate(speeds)]

    def test_constant_speed_has_zero_acceleration_and_jerk(self):
        result=quality_metrics(self.rows([.5]*10))
        self.assertEqual(result["rms_acceleration_mps2"],0)
        self.assertEqual(result["max_abs_jerk_mps3"],0)

    def test_velocity_change_uses_seconds_and_reports_safety_intervention(self):
        rows=self.rows([0,.1,.3,.4])
        rows[1]["shield"]=1
        result=quality_metrics(rows)
        self.assertAlmostEqual(result["max_abs_jerk_mps3"],10)
        self.assertAlmostEqual(result["safety_intervention_fraction"],.25)

    def test_standstill_cannot_claim_moving_comfort(self):
        self.assertIsNone(quality_metrics(self.rows([0]*5))["rms_acceleration_mps2"])

    def test_missing_samples_and_invalid_time_rejected(self):
        with self.assertRaises(ValueError):quality_metrics([])
        rows=self.rows([.1]*5);rows[2]["timestamp"]=rows[1]["timestamp"]
        with self.assertRaises(ValueError):quality_metrics(rows)


if __name__=="__main__":unittest.main()
