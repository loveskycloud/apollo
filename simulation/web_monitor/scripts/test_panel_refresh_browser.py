"""Playback regression: background panel queries must not flash spinners or move charts.

Real Chromium clicks; read-only diagnostics. Delays are injected into HTTP requests,
not into the viewer's playback state. Run against an otherwise unused test server.
"""
import argparse
import asyncio
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

STATE = """() => {
 const h=window._handle,id=h.get_active_recording_id();
 return {playing:h.get_playing(id),panic:h.has_panicked(),
   time:h.get_ad_scene_state().playhead_ns,panels:h.get_debug_panels_state()};
}"""


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9091/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy&persist=false&renderer=webgl&theme=dark")
    parser.add_argument("--mcap", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, logs = [], []
    delay_s = 0.0
    fail_next = False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chromium", headless=True, args=[
            "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.route("https://tel.rerun.io/**", lambda r: r.fulfill(status=200, body="{}"))

        async def query_route(route):
            nonlocal fail_next
            if fail_next:
                fail_next = False
                await route.fulfill(status=500, content_type="application/json", body=json.dumps(
                    {"status": "error", "message": "Injected debug query failure (test)"}))
            else:
                await asyncio.sleep(delay_s)
                await route.continue_()

        await page.route("**/api/debug_query", query_route)
        page.on("pageerror", lambda e: logs.append(str(e)))

        async def click(x, y):
            await page.mouse.click(x, y)
            await page.wait_for_timeout(350)

        async def settled(count):
            await page.wait_for_function("""n => {
                const ps=window._handle.get_debug_panels_state();
                return ps.length===n && ps.every(p=>p.has_data && !p.pending && !p.error);
            }""", arg=count, timeout=60000)

        async def snapshot(name):
            await page.screenshot(path=str(out / f"{name}.png"))
            state = await page.evaluate(STATE)
            evidence.append({"name": name, **state})
            return state

        async def playback(name, count, seconds):
            nonlocal delay_s
            await settled(count)
            before = await snapshot(name + "-paused")
            heights = {p["id"]: p["content_top_y"] for p in before["panels"]}
            revisions = {p["id"]: set() for p in before["panels"]}
            pending_samples = 0
            delay_s = 0.35
            await click(130, 973)
            deadline = asyncio.get_running_loop().time() + seconds
            shot_pending = False
            while asyncio.get_running_loop().time() < deadline:
                state = await page.evaluate(STATE)
                assert state["playing"] and not state["panic"], state
                assert len(state["panels"]) == count
                for panel in state["panels"]:
                    assert panel["has_data"] and not panel["initial_loading"] and not panel["error"], panel
                    assert abs(panel["content_top_y"] - heights[panel["id"]]) < 0.5, ("chart moved", panel, heights)
                    revisions[panel["id"]].add(panel["revision"])
                    pending_samples += int(panel["pending"])
                evidence.append({"name": name + "-playing", **state})
                if not shot_pending and any(p["pending"] for p in state["panels"]):
                    await snapshot(name + "-refreshing")
                    shot_pending = True
                await page.wait_for_timeout(120)
            await click(130, 973)
            delay_s = 0.0
            await settled(count)
            after = await snapshot(name + "-paused-after")
            assert not after["playing"]
            assert int(after["time"]) > int(before["time"]) + 2_000_000_000
            assert pending_samples > 0
            assert all(len(values) >= 2 for values in revisions.values()), revisions
            print(json.dumps({"layout": name, "pending_samples_with_visible_data": pending_samples,
                              "distinct_revisions": {str(k): len(v) for k, v in revisions.items()},
                              "spinner_frames_after_warmup": 0, "chart_displacement_px": 0}), flush=True)

        try:
            await page.goto(args.url, wait_until="domcontentloaded")
            await page.wait_for_function("window._handle !== undefined", timeout=60000)
            await page.wait_for_timeout(1000)
            await click(37, 115)
            await page.wait_for_timeout(350)
            pixels = np.asarray(Image.open(io.BytesIO(await page.screenshot())).convert("RGB"))
            band = pixels[290:341, 96:370].astype(int)
            dark = (band.max(axis=2) < 45) & ((band.max(axis=2) - band.min(axis=2)) < 8)
            rows = np.flatnonzero(dark.mean(axis=1) > 0.5)
            assert len(rows) > 5
            await click(205, 290 + int(np.median(rows)))
            await page.keyboard.insert_text(args.mcap)
            await page.wait_for_timeout(400)
            await page.keyboard.press("Enter")
            await page.wait_for_function("() => {try{return window._handle.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length>0}catch{return false}}", timeout=60000)
            await page.wait_for_timeout(1000)
            await click(37, 115)
            await click(140, 40)
            await click(760, 970)
            await click(37, 189)
            await click(180, 113)
            await click(37, 189)
            await playback("planning", 3, 7)
            # A seek may retain the old picture transiently, but must converge to exact current queries.
            await click(640, 970)
            await settled(3)
            seek = await snapshot("planning-seek")
            assert all(p["at_ns"] == seek["time"] for p in seek["panels"]), seek
            await click(37, 189)
            await click(180, 161)
            await click(37, 189)
            await playback("control", 5, 9)
            # Fail-loud regression: do not conceal a failed refresh behind the retained chart.
            fail_next = True
            await click(1080, 970)
            await page.wait_for_function("() => window._handle.get_debug_panels_state().some(p=>p.error?.includes('Injected'))", timeout=15000)
            failure = await snapshot("explicit-error")
            assert any(p["error"] and not p["has_data"] for p in failure["panels"])
            assert not logs, logs
            print("PASS: warm panels refresh without spinners/vertical jitter; data advances, seek converges, errors remain visible", flush=True)
        finally:
            await page.screenshot(path=str(out / "final.png"))
            (out / "evidence.json").write_text(json.dumps(evidence, indent=2))
            (out / "page-errors.json").write_text(json.dumps(logs, indent=2))
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
