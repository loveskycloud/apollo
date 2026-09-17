"""Actual Sim replay, map layer clicks, layout switches and paused/play clock checks."""
import argparse
import asyncio
import json
import os
import io
import sys
from pathlib import Path

from playwright.async_api import async_playwright
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/apollo_record_tools"))
sys.path.insert(0, "/opt/apollo/neo/python")
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
import numpy as np
from mcap.reader import make_reader
from hd_map import MapMesh, load_map, build_map_meshes
from modules.common_msgs.localization_msgs import localization_pb2


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:9090/?url=&renderer=webgl&theme=dark")
    parser.add_argument("--job", default="efcce7f9cf4c49c1")
    parser.add_argument("--out", required=True)
    parser.add_argument("--record", help="Test Source mouse/keyboard input instead of Sim Replay")
    parser.add_argument("--map", help="Explicit map for --record")
    parser.add_argument("--staging-assets", type=Path, help="Explicit pre-deployment assets; omit for deployed acceptance")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, errors, console, conversions = [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chromium", headless=True,
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width":1440,"height":1000})
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console.append({"type":m.type,"text":m.text}))
        page.on("request", lambda r: conversions.append(json.loads(r.post_data)) if r.url.endswith('/api/convert_record') and r.post_data else None)
        await page.route("https://tel.rerun.io/**", lambda r: r.fulfill(status=200, body="{}"))
        async def state():
            return await page.evaluate("()=>({scene:window._handle.get_ad_scene_state(),layers:window._handle.get_display_layers_state(),playback:window._handle.get_playback_state(),playing:window._handle.get_playing(window._handle.get_active_recording_id()),hud:window._handle.get_vehicle_dashboard_state()})")
        async def capture(name):
            s = await state()
            evidence.append({"name":name, **s})
            await page.screenshot(path=str(out / (name + ".png")))
            return s
        async def visible(expected):
            await page.wait_for_function("""expected=>{
                const s=window._handle.get_display_layers_state();
                const scenes=s.views.filter(v=>v.name.endsWith('3D'));
                return scenes.length && scenes.every(v=>{
                    const maps=v.entities.filter(e=>e.path.startsWith('/hdmap/'));
                    return maps.length===3 && maps.every(e=>e.visible===expected);
                });
            }""", arg=expected, timeout=20000)
        try:
            await page.goto(args.url)
            await page.wait_for_function("()=>window._handle && !window._handle.has_panicked()", timeout=60000)
            await page.wait_for_timeout(3000)
            if args.record:
                assert args.map
                await page.mouse.click(37,115)
                await page.wait_for_timeout(700)
                bitmap=np.asarray(Image.open(io.BytesIO(await page.screenshot())).convert("RGB"))
                band=bitmap[290:341,96:370].astype(int)
                rows=np.flatnonzero(((band.max(2)<45)&(band.max(2)-band.min(2)<8)).mean(1)>.5)
                assert len(rows)>5
                await page.mouse.click(205,290+int(np.median(rows)))
                await page.wait_for_timeout(400)
                await page.keyboard.press("Control+A")
                await page.keyboard.insert_text(args.record)
                await page.wait_for_timeout(400)
                await page.keyboard.press("Tab")
                await page.wait_for_timeout(400)
                await page.keyboard.press("Control+A")
                await page.keyboard.insert_text(args.map)
                await page.wait_for_timeout(400)
                await page.screenshot(path=str(out/"source-map-input.png"))
                await page.keyboard.press("Shift+Tab")
                await page.wait_for_timeout(400)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(600)
                assert conversions and conversions[-1] == {"path":args.record,"map":args.map}, conversions
                await page.mouse.click(37,115)
            else:
                await page.mouse.click(35,340)
                key = f"replay_{args.job}_0"
                await page.wait_for_function("key=>window._handle.get_simulation_state()?.[key]", arg=key, timeout=30000)
                s = await page.evaluate("()=>window._handle.get_simulation_state()")
                await page.mouse.click(*s[key])
            await page.wait_for_function("""()=>{const h=window._handle,p=h.get_playback_state();
                return p?.clock_ready&&!p.pending&&!p.waiting_receipt&&!p.error&&p.receivers.includes(p.proxy)&&p.recording===h.get_active_recording_id()
                    && Object.values(h.get_ad_scene_state().map).every(m=>m.vertices>0 && m.static);
            }""", timeout=120000)
            await visible(True)
            s = await capture("first-paused-with-layers")
            assert not s["playing"]
            # Compare rendered first vertices/counts against the actual embedded map
            # and verify that the embedding uses the task snapshot + pose origin.
            with Path(s["playback"]["mcap"]).open("rb") as stream:
                reader = make_reader(stream)
                meta = next(m for m in reader.iter_metadata() if m.name == "apollo_converter")
                hd = json.loads(meta.metadata["hd_map"])
                expected = build_map_meshes(load_map(hd["path"]), hd["origin"])
                if not args.record:
                    assert f"/jobs/{args.job}/map/" in hd["path"], hd
                else:
                    assert hd["path"] == str(Path(args.map).resolve()), hd
                counts = {}
                first_pose = None
                for schema, channel, msg in reader.iter_messages():
                    if channel.topic == "/apollo/localization/pose" and first_pose is None:
                        p = localization_pb2.LocalizationEstimate.FromString(msg.data).pose.position
                        first_pose = [p.x, p.y, p.z]
                    if not channel.topic.startswith("/hdmap/"): continue
                    mesh = MapMesh.FromString(msg.data)
                    assert mesh == expected[channel.topic]
                    name = channel.topic.split('/')[-1]
                    drawn = s["scene"]["map"][name]
                    assert drawn["vertices"] == len(mesh.xyz)//3
                    np.testing.assert_allclose(drawn["first"], list(mesh.xyz[:3]), atol=1e-4)
                    counts[channel.topic] = counts.get(channel.topic, 0)+1
                assert len(counts)==3 and set(counts.values())=={1}, counts
                np.testing.assert_allclose(hd["origin"], first_pose, rtol=0, atol=1e-9)
                lane_point = load_map(hd["path"]).lane[0].central_curve.segment[0].line_segment.point[0]
                ribbon_start = np.array(expected["/hdmap/lane_centerlines"].xyz[:6]).reshape(2,3).mean(axis=0)
                z = lane_point.z if lane_point.HasField("z") else first_pose[2]
                np.testing.assert_allclose(ribbon_start, np.array([lane_point.x,lane_point.y,z])-first_pose+[0,0,.035], rtol=0, atol=1e-8)
                (out/"map-source.json").write_text(json.dumps({**hd,"messages":counts},indent=2))
            # Every independent checkbox must hide cached geometry immediately.
            for name in ("road_surface", "lane_boundaries", "lane_centerlines"):
                key = "map/" + name
                s = await state()
                xy = s["layers"]["nodes"][key]
                await page.mouse.click(*xy)
                await page.wait_for_function("key=>window._handle.get_display_layers_state().views.every(v=>v.entities.filter(e=>e.path==='/hdmap/'+key).every(e=>!e.visible))", arg=name)
                s = await capture("hidden-"+name)
                assert s["scene"]["map"][name]["vertices"]>0, "Hiding should retain cached mesh"
                await page.mouse.click(*xy)
                await visible(True)
            s = await state()
            await page.mouse.click(*s["layers"]["nodes"]["map"])
            await visible(False)
            await capture("map-hidden")
            await page.mouse.click(*s["layers"]["nodes"]["map"])
            await visible(True)
            await page.mouse.click(114,40)  # Close observed Layers header.
            await page.wait_for_function("()=>window._handle.get_vehicle_dashboard_state().views.length")
            s = await state()
            await page.mouse.click(*s["hud"]["views"][0]["camera"]["locate"])
            await page.wait_for_timeout(2000)
            await capture("control-map-ego-follow")
            s = await state()
            camera = s["hud"]["views"][0]["camera"]
            await page.mouse.click(*camera["top"])
            await page.wait_for_timeout(2000)
            await capture("control-map-top-follow")
            # Layout navigation is actual mouse input (Planning / Perception / Control).
            for name, y in [("Planning",113),("Perception",65),("Control",161)]:
                before = (await state())["scene"]["playhead_ns"]
                await page.mouse.click(37,189)
                await page.wait_for_timeout(400)
                await page.mouse.click(180,y)
                await page.wait_for_timeout(1200)
                await page.mouse.click(37,189)
                await visible(True)
                await page.wait_for_timeout(1200)
                s = await capture(name.lower()+"-map")
                assert any(name in v["name"] for v in s["layers"]["views"]), s["layers"]
                assert s["scene"]["playhead_ns"]==before and not s["playing"]
            for clock in ["publish_time","message_time"]:
                for ns in [30_000_000_000, 58_000_000_000, 3_000_000_000]:
                    await page.evaluate("({clock,ns})=>{const h=window._handle;h.set_time_for_timeline(h.get_active_recording_id(),clock,ns)}", {"clock":clock,"ns":ns})
                    await page.wait_for_function("ns=>{const s=window._handle.get_ad_scene_state();return s.playhead_ns===String(ns)&&s.ego_pose.sample_ns===String(ns)}", arg=ns, timeout=30000)
                    s = await capture(f"{clock}-{ns}")
                    assert not s["playing"] and set(s["scene"]["timelines"])=={"publish_time","message_time"}
                    assert all(m["vertices"]>0 and m["static"] for m in s["scene"]["map"].values())
                await page.mouse.click(130,973)
                await page.wait_for_function("()=>Number(window._handle.get_ad_scene_state().playhead_ns)>7000000000",timeout=25000)
                s = await capture(clock+"-playing")
                assert s["playing"]
                assert all(m["vertices"]>0 and m["static"] for m in s["scene"]["map"].values())
                await page.mouse.click(130,973)
            assert not errors, errors
            print("PASS: verified map snapshot + coordinate origin, 3 static meshes, independent/group visibility, all layouts, paused seeks and playback on both clocks",flush=True)
        finally:
            (out/"evidence.json").write_text(json.dumps(evidence,indent=2))
            (out/"errors.json").write_text(json.dumps(errors,indent=2))
            (out/"console.json").write_text(json.dumps(console,indent=2))
            (out/"conversions.json").write_text(json.dumps(conversions,indent=2))
            await page.screenshot(path=str(out/"final.png"))
            await browser.close()


asyncio.run(main())
