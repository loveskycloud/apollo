"""Strict WorldSim planning availability, independent of arrival/collision."""
import math


class PlanningContinuity:
    def __init__(self, period_s=.1):
        self.period_ns = round(period_s*1e9)
        self.first_world = self.last_world = None
        self.previous = self.first = None
        self.frames = self.empty = self.invalid = self.gaps = 0
        self.issue_count = 0
        self.evidence = []

    def issue(self, stamp, reason):
        self.issue_count += 1
        if len(self.evidence) < 20:
            self.evidence.append({'time_s': stamp/1e9, 'reason': reason})

    def world(self, stamp):
        if self.first_world is None:
            self.first_world = stamp
        self.last_world = stamp

    def planning(self, stamp, msg):
        self.frames += 1
        if self.first is None:
            self.first = stamp
        if self.previous is not None and stamp-self.previous > self.period_ns+1000:
            self.gaps += 1
            self.issue(stamp, f'Planning publication gap {(stamp-self.previous)/1e9:.6f} s')
        self.previous = stamp
        points = msg.trajectory_point
        if not points:
            self.empty += 1
            self.issue(stamp, 'Empty trajectory: '+msg.estop.reason)
            return
        bad = msg.estop.is_estop or msg.header.status.error_code != 0 or msg.decision.main_decision.HasField('not_ready')
        bad = bad or len(points) < 2
        last_t = None
        for p in points:
            values = (p.relative_time, p.path_point.x, p.path_point.y, p.path_point.theta, p.v, p.a, p.path_point.kappa)
            if not all(math.isfinite(v) for v in values) or (last_t is not None and p.relative_time <= last_t):
                bad = True
            last_t = p.relative_time
        if points[0].relative_time > 1e-6 or points[-1].relative_time < self.period_ns/1e9-1e-6:
            bad = True
        if bad:
            self.invalid += 1
            self.issue(stamp, 'Unusable trajectory: status/estop/points/time horizon')

    def result(self):
        boundary = self.first is None or self.first_world is None
        if not boundary:
            boundary = (self.first-self.first_world > self.period_ns+1000 or
                        self.last_world-self.previous > self.period_ns+1000)
        return {'status': 'FAIL' if boundary or self.empty or self.invalid or self.gaps else 'PASS',
                'frames': self.frames, 'empty_frames': self.empty, 'invalid_frames': self.invalid,
                'publication_gaps': self.gaps, 'boundary_missing': boundary,
                'expected_period_s': self.period_ns/1e9, 'first_failures': self.evidence,
                'omitted_failure_details': max(0, self.issue_count-len(self.evidence))}
