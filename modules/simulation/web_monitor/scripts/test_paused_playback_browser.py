"""Real Chromium clicks + screenshots + exact MCAP/LatestAt frame checks.

Run against a freshly restarted web_monitor (no existing recordings).
Requires playwright (with Chromium), Pillow, numpy, mcap and Foxglove schemas.
No playback state is set through the JavaScript API: it is read-only diagnostics.
"""

import argparse
import bisect
import io
import json
import os
from pathlib import Path
import time

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
import numpy as np
from PIL import Image
from foxglove_schemas_protobuf.PointCloud_pb2 import PointCloud
from mcap.reader import make_reader
from playwright.sync_api import sync_playwright

STATE = """() => {
  const h = window._handle;
  const id = h.get_active_recording_id();
  return {...h.get_point_cloud_state('/lidar/up/points'),
          id, playing: h.get_playing(id), panic: h.has_panicked()};
}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9090/")
    parser.add_argument("--record", required=True)
    parser.add_argument("--mcap", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lidar-checkbox-y", type=int, default=374)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    samples = []
    with open(args.mcap, "rb") as f:
        for _, _, message in make_reader(f).iter_messages(topics=["/lidar/up/points"]):
            cloud = PointCloud.FromString(message.data)
            samples.append((message.log_time, len(cloud.data) // cloud.point_stride))
    times = [t for t, _ in samples]
    evidence, logs, requests, replies = [], [], [], []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=[
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        # Keep the test local; these unrelated background requests are not under test.
        page.route("https://tel.rerun.io/**", lambda r: r.fulfill(status=200, body="{}"))
        page.on("console", lambda m: logs.append((m.type, m.text)) if m.type != "debug" else None)
        page.on("pageerror", lambda e: logs.append(("pageerror", str(e))))
        page.on("request", lambda r: requests.append((r.url, r.post_data))
                if r.url.startswith(args.url) and "/api/" in r.url else None)
        page.on("response", lambda r: replies.append((r.url, r.status, r.text()))
                if r.url.startswith(args.url) and "/api/" in r.url else None)

        def click(x, y):
            page.mouse.click(x, y)
            page.wait_for_timeout(250)  # allow egui to consume the gesture / finish drawer animation

        def state():
            return page.evaluate(STATE)

        def verify_sample(s):
            assert not s["panic"], s
            t = int(s["playhead_ns"])
            i = bisect.bisect_right(times, t) - 1
            assert i >= 0, ("playhead before first lidar frame", s)
            expected_time, expected_points = samples[i]
            assert int(s["sample_ns"]) == expected_time, (s, samples[i])
            assert s["points"] == expected_points, (s, samples[i])

        def wait_frame():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                s = state()
                if s["sample_ns"] is not None:
                    i = bisect.bisect_right(times, int(s["playhead_ns"])) - 1
                    if i >= 0 and int(s["sample_ns"]) == times[i]:
                        verify_sample(s)
                        return s
                page.wait_for_timeout(150)
            raise AssertionError(("current frame did not arrive", state()))

        def snapshot(name, s):
            page.wait_for_timeout(250)
            data = page.screenshot(path=str(out / f"{name}.png"))
            pixels = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))[30:940, 80:885]
            r, g, b = (pixels[:, :, i].astype(float) for i in range(3))
            colored = int(((r > 180) & (g > 80) & (b < 170)).sum())
            assert colored > 3000, ("no visible point cloud", name, colored)
            evidence.append({"name": name, **s, "visible_point_pixels": colored})
            print(json.dumps(evidence[-1]), flush=True)

        try:
            separator = "&" if "?" in args.url else "?"
            page.goto(args.url + separator + "persist=false&renderer=webgl&theme=dark",
                      wait_until="domcontentloaded")
            page.wait_for_function("window._handle !== undefined", timeout=60000)
            page.wait_for_timeout(400)
            click(37, 115)
            page.wait_for_timeout(600)
            pixels = np.asarray(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))
            band = pixels[290:341, 96:370].astype(int)
            dark = (band.max(axis=2) < 45) & ((band.max(axis=2) - band.min(axis=2)) < 8)
            rows = np.flatnonzero(dark.mean(axis=1) > 0.5)
            assert len(rows) > 5, "Source path input not visible"
            click(205, 290 + int(np.median(rows)))
            page.keyboard.insert_text(args.record)
            page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            page.wait_for_function("""() => {
              const h=window._handle, id=h.get_active_recording_id();
              const tl=id && h.get_active_timeline(id);
              if (!tl || h.get_time_for_timeline(id, tl) == null) return false;
              return id && id !== 'ad_layout_workspace' &&
                h.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length > 0;
            }""", timeout=30000)
            page.wait_for_timeout(1500)
            initial = state()
            assert not initial["playing"], ("initial load auto-played", initial)
            page.wait_for_timeout(1000)
            assert state()["playhead_ns"] == initial["playhead_ns"], "paused initial playhead moved"
            click(37, 115)  # close Source
            page.wait_for_timeout(400)
            page.screenshot(path=str(out / "topics-before-selection.png"))
            start = time.monotonic()
            layers = page.evaluate("window._handle.get_display_layers_state()")
            lidar = next(l for l in layers["layers"] if l["path"] == "sensing/lidar/main")
            assert lidar["checkbox"] and not lidar["enabled"]
            click(*lidar["checkbox"])
            s = wait_frame()
            assert not s["playing"] and s["playhead_ns"] == initial["playhead_ns"], s
            s["load_latency_s"] = round(time.monotonic() - start, 3)
            click(140, 40)  # collapse Topics
            snapshot("paused-first-cloud", s)

            for name, x in [("seek-earlier", 523), ("seek-forward", 1090), ("seek-back-again", 635)]:
                start = time.monotonic()
                click(x, 970)
                s = wait_frame()
                assert not s["playing"], ("seek resumed playback", s)
                s["seek_latency_s"] = round(time.monotonic() - start, 3)
                snapshot(name, s)
                page.wait_for_timeout(500)
                assert state()["playhead_ns"] == s["playhead_ns"], "paused seek playhead moved"

            deadline = time.monotonic() + 30
            while True:
                s = state()
                t = int(s["playhead_ns"])
                if any(int(b) <= t and int(e) >= t + 4_000_000_000 for b, e in s["cached_ranges_ns"]):
                    break
                assert time.monotonic() < deadline, ("buffer never filled", s)
                page.wait_for_timeout(200)
            click(130, 973)  # Play, first and only Play click in the whole test
            playback_samples = []
            wall_start = time.monotonic()
            while time.monotonic() - wall_start < 3:
                s = state()
                verify_sample(s)
                assert s["playing"], ("stalled inside buffered range", s)
                playback_samples.append(s)
                page.wait_for_timeout(100)
            click(130, 973)  # Pause
            final = state()
            assert not final["playing"], final
            advance = (int(final["playhead_ns"]) - t) / 1e9
            distinct = len({s["sample_ns"] for s in playback_samples})
            assert advance > 1.5 and distinct >= 12, ("playback did not advance smoothly", advance, distinct)
            snapshot("after-buffered-playback", final)
            evidence.append({"name": "buffered-playback", "advance_s": advance,
                             "distinct_lidar_frames": distinct, "samples": playback_samples})
            print(f"PASS: paused initial cloud, three paused seeks, buffered playback ({distinct} frames)", flush=True)
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                page.screenshot(path=str(out / "failure.png"))
            except Exception as screenshot_error:
                print(f"Failure screenshot unavailable: {screenshot_error}", flush=True)
            raise
        finally:
            (out / "evidence.json").write_text(json.dumps(evidence, indent=2))
            (out / "browser-log.json").write_text(json.dumps(logs, indent=2))
            (out / "requests.json").write_text(json.dumps(requests, indent=2))
            (out / "responses.json").write_text(json.dumps(replies, indent=2))
            browser.close()


if __name__ == "__main__":
    main()
