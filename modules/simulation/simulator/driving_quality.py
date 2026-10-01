"""Trace-based diagnostics complement physical collision and arrival checks.

Only straight-road centering/weaving is assessed here, not all driving quality.
Three metres are allowed to recover after a close obstacle or bend.
"""
import csv
from pathlib import Path


def analyze_trace(path):
    with Path(path).open() as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("ML policy trace is empty")
    clear_start = None
    offsets = []
    turns = []
    direction = 0
    extreme = float(rows[0]["l"])
    max_turns = 0
    for row in rows:
        s, lateral, speed, stamp = (float(row[k]) for k in ("s", "l", "speed", "timestamp"))
        straight = max(abs(float(row[k])) for k in ("curvature", "obs10", "obs11")) < .03
        # Conservative body extent from the available observation; exact contact
        # is independently evaluated by the WorldSim oriented-body check.
        distance = float(row["obs3"])*10-.62-max(float(row["obs12"]), float(row["obs13"]))
        close = float(row["obs5"]) > .5 and distance < 1.5
        # An oncoming encounter within the 8 s plan + 1 s braking horizon is
        # not clear-road driving, even when a nearer static object occupies
        # the legacy single-actor observation. Never infer this from clipped
        # obs3/obs16 distances: a truly distant actor must not excuse bias.
        oncoming_ttc = float(row.get("oncoming_ttc_s") or "inf")
        close |= oncoming_ttc <= 9.
        if not straight or close:
            clear_start = None
        else:
            if clear_start is None:
                clear_start = s
            if s-clear_start >= 3 and speed > .2:
                offsets.append(abs(lateral))
        if not straight or speed < .15:
            direction, extreme, turns = 0, lateral, []
            continue
        # A turn must reverse lateral travel by at least 12 cm; tiny steering
        # corrections do not count. Three reversals within 10 s is weaving.
        delta = lateral-extreme
        if direction == 0 and abs(delta) >= .12:
            direction = 1 if delta > 0 else -1
            extreme = lateral
        elif direction != 0 and direction*delta >= 0:
            extreme = lateral
        elif abs(delta) >= .12:
            direction *= -1
            extreme = lateral
            turns.append(stamp)
            turns = [t for t in turns if stamp-t <= 10]
            max_turns = max(max_turns, len(turns))
    maximum = max(offsets, default=0)
    return {"status": "PASS" if maximum <= .15 and max_turns < 3 else "FAIL",
            "clear_straight_samples": len(offsets),
            "max_clear_straight_offset_m": maximum,
            "max_lateral_reversals_10s": max_turns,
            "limits": {"clear_straight_offset_m": .15, "lateral_reversals_10s": 2},
            "oncoming_anticipation_s": 9.,
            "scope": "Straight-road centering and repeated lateral reversals; does not certify all driving behavior"}
