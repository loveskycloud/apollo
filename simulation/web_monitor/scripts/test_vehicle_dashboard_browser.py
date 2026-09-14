"""Real browser: bottom-centred collapsible HUD, raw chassis parity and camera actions."""
import argparse
import asyncio
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

sys.path.insert(0, "/apollo_workspace/tools/apollo_record_tools")
from mcap_debug_query import DebugQueries

STATE = """()=>({hud:window._handle.get_vehicle_dashboard_state(),
    scene:window._handle.get_ad_scene_state(),
    playing:window._handle.get_playing(window._handle.get_active_recording_id()),
    panic:window._handle.has_panicked()})"""


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mcap", required=True)
    ap.add_argument("--record", help="Open original record in UI; use mcap as independent oracle")
    ap.add_argument("--camera-modes", action="store_true", help="Exercise planar lock and continuous heading-up/chase follow")
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:9091/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy&persist=false&renderer=webgl&theme=dark")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    engine = DebugQueries(); engine.open(args.mcap)
    evidence, errors, console = [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chromium", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width":1440,"height":1000})
        await page.route("https://tel.rerun.io/**", lambda r:r.fulfill(status=200,body="{}"))
        page.on("pageerror", lambda e:errors.append(str(e)))
        page.on("console", lambda m:console.append({"type":m.type,"text":m.text})
                if m.type in ("warning","error") or "re_grpc_client" in m.text or "Active recording" in m.text else None)
        async def click(x,y):
            await page.mouse.click(x,y); await page.wait_for_timeout(450)
        async def current(): return await page.evaluate(STATE)
        async def ready():
            await page.wait_for_function("""()=>{try{const s=window._handle.get_vehicle_dashboard_state();return s.views.length && s.data.covered && s.data.streams.chassis.sample && !s.data.error}catch{return false}}""",timeout=60000)
        def check(state):
            assert not state["panic"]
            data = state["hud"]["data"]
            assert data["covered"] and not data["error"], data
            assert "control" not in data["streams"]
            at = int(data["at_ns"])
            raw = engine.snapshot("/apollo/canbus/chassis", at, "message_time")
            sample = data["streams"]["chassis"]["sample"]
            assert sample["sample_ns"] == raw["sample_ns"], (sample,raw)
            for field in ["speed_mps","steering_percentage","throttle_percentage","brake_percentage","driving_mode","gear_location"]:
                actual, expected = sample[field],raw["message"].get(field)
                if isinstance(expected,(int,float)):
                    assert abs(actual-expected)<1e-5,(field,actual,expected)
                else: assert actual==expected,(field,actual,expected)
            for view in state["hud"]["views"]:
                p,r=view["pane"],view["rect"]
                assert abs((p[0]+p[2]-r[0]-r[2])/2)<1
                assert abs(p[3]-r[3]-12)<1
                assert r[0]>=p[0] and r[2]<=p[2] and r[1]>=p[1],view
                c=view["camera"]
                if c.get("overhead"):
                    delta=np.array(c["position"])-c["target"]
                    np.testing.assert_allclose(delta[:2],[0,0],atol=.001)
                    assert delta[2]>=20
                if c.get("following") and c["ego"] is not None:
                    pose=engine.snapshot("/vehicle",int(c["pose_ns"]),"message_time")["message"]["pose"]
                    xyz=[pose["position"].get(k,0) for k in ("x","y","z")]
                    x,y,z,w=[pose["orientation"].get(k,0) for k in ("x","y","z","w")]
                    heading=np.array([2*(x*y-w*z),1-2*(x*x+z*z),0.])
                    heading/=np.linalg.norm(heading)
                    np.testing.assert_allclose(c["ego"],xyz,atol=.001)
                    np.testing.assert_allclose(c["heading"],heading,atol=1e-5)
                    np.testing.assert_allclose(c["target"],xyz,atol=.001)
                    delta=np.array(c["position"])-c["target"]
                    if c["overhead"]:
                        np.testing.assert_allclose(c["up"],heading,atol=1e-5)
                        np.testing.assert_allclose(delta,[0,0,c["top_height"]],atol=.001)
                    else:
                        np.testing.assert_allclose(delta,-heading*.8*c["chase_distance"]+np.array([0,0,.6*c["chase_distance"]]),atol=.001)
        async def shot(name):
            await page.mouse.move(60,940); await page.wait_for_timeout(250)
            state=await current();check(state)
            evidence.append({"name":name,**state})
            await page.screenshot(path=str(out/f"{name}.png"))
            return state
        try:
            await page.goto(args.url,wait_until="domcontentloaded")
            await page.wait_for_function("window._handle !== undefined",timeout=60000)
            await page.wait_for_timeout(1000); await click(37,115)
            pixels=np.asarray(Image.open(io.BytesIO(await page.screenshot())).convert("RGB"))
            band=pixels[290:341,96:370].astype(int)
            rows=np.flatnonzero(((band.max(2)<45)&(band.max(2)-band.min(2)<8)).mean(1)>.5)
            assert len(rows)>5
            await click(205,290+int(np.median(rows)))
            await page.keyboard.insert_text(args.record or args.mcap);await page.wait_for_timeout(400);await page.keyboard.press("Enter")
            await page.wait_for_function("()=>{try{return window._handle.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length>0}catch{return false}}",timeout=60000)
            await click(37,115)
            layers=await page.evaluate("window._handle.get_display_layers_state()")
            if not any(l["checkbox"] for l in layers["layers"]):await click(125,40)
            for path in ["sensing/lidar/main","planning/trajectory"]:
                layers=await page.evaluate("window._handle.get_display_layers_state()")
                layer=next(l for l in layers["layers"] if l["path"]==path)
                if not layer["enabled"]:await click(*layer["checkbox"])
            await click(125,40)
            await click(840,970); await ready()
            await page.wait_for_function("()=>window._handle.get_point_cloud_state('/lidar/up/points').points>0",timeout=30000)
            first=await shot("expanded-feedback-only")
            assert not first["playing"]
            await page.mouse.move(*first["hud"]["views"][0]["camera"]["top"])
            await page.wait_for_timeout(1000)
            hover=await page.screenshot(path=str(out/"view-button-hover.png"))
            pixels=np.asarray(Image.open(io.BytesIO(hover)).convert("RGB")).astype(int)
            cx,cy=map(int,first["hud"]["views"][0]["camera"]["top"])
            # The purple tooltip must open left of the toolbar, not over the lower button.
            purple=(pixels[:,:,0]>pixels[:,:,1]+3)&(pixels[:,:,2]>pixels[:,:,0]+5)&(pixels[:,:,0]<80)
            assert purple[max(24,cy-55):cy+75,max(75,cx-260):cx-20].sum()>100
            assert purple[max(24,cy-55):cy+75,cx+20:cx+240].sum()<20
            await page.mouse.move(*first["hud"]["views"][0]["camera"]["locate"])
            await page.wait_for_timeout(1000)
            await page.screenshot(path=str(out/"ego-button-hover.png"))
            await click(*first["hud"]["views"][0]["toggle"])
            collapsed=await shot("collapsed")
            v=collapsed["hud"]["views"][0]
            assert not v["expanded"] and v["rect"][3]-v["rect"][1]==28
            await click(*v["toggle"]);expanded=await shot("expanded-again")
            view=expanded["hud"]["views"][0];r=view["rect"]
            await page.mouse.move(r[0]+35,r[1]+60);await page.mouse.down()
            await page.mouse.move(r[0]+70,r[1]+68,steps=10);await page.mouse.up()
            await page.wait_for_timeout(500)
            after_drag=(await current())["hud"]["views"][0]
            np.testing.assert_allclose(after_drag["camera"]["position"],view["camera"]["position"],atol=.01)
            for name,row in [("planning",113),("control",161)]:
                await click(37,189);await click(180,row);await click(37,189)
                await ready();await page.wait_for_timeout(1500);await shot(name+"-bottom-centre")
            for x in [640,1080,840]:
                await click(x,970);await ready();await page.wait_for_timeout(1200)
                state=await shot(f"paused-seek-{x}")
                assert not state["playing"]
                v=state["hud"]["views"][0];assert v["camera"]["ego"] is not None,v
                if not v["camera"].get("following"):await click(*v["camera"]["locate"])
                await page.wait_for_timeout(1400)
                centered=await shot(f"ego-{x}")
                c=centered["hud"]["views"][0]["camera"]
                np.testing.assert_allclose(c["target"],c["ego"],atol=.01)
            before=await current();c=before["hud"]["views"][0]["camera"]
            old_offset=np.array(c["position"])-c["target"]
            await click(*c["top"]);await page.wait_for_timeout(1500)
            top=await shot("birds-eye")
            c=top["hud"]["views"][0]["camera"]
            delta=np.array(c["position"])-c["target"]
            assert delta[2]>0 and np.linalg.norm(delta[:2])<.05,c
            await click(*c["top"]);await page.wait_for_timeout(1500)
            restored=await shot("restored-3d")
            c=restored["hud"]["views"][0]["camera"]
            np.testing.assert_allclose(np.array(c["position"])-c["target"],old_offset,atol=.05)
            await page.set_viewport_size({"width":1120,"height":820})
            await page.wait_for_timeout(1200);await shot("narrow-pane")
            await page.set_viewport_size({"width":1440,"height":1000})
            await page.wait_for_timeout(1200)
            if args.camera_modes:
                async def camera():return (await current())["hud"]["views"][0]["camera"]
                async def scene_drag(button="left", modifiers=()):
                    v=(await current())["hud"]["views"][0];p=v["pane"]
                    start=(p[0]+130,p[1]+165)
                    for key in modifiers:await page.keyboard.down(key)
                    await page.mouse.move(*start);await page.mouse.down(button=button)
                    await page.mouse.move(start[0]+65,start[1]+28,steps=12)
                    await page.mouse.up(button=button)
                    for key in modifiers:await page.keyboard.up(key)
                    await page.wait_for_timeout(350)
                c=await camera();assert c["following"] and not c["overhead"]
                for button in ("left","right","middle"):
                    await scene_drag(button);check(await current())
                await shot("chase-orbit-input-locked")
                await click(*c["top"]);await shot("heading-up-follow")
                for x in (1080,700):
                    await click(x,970);await ready();await page.wait_for_timeout(1300)
                    await shot(f"heading-up-seek-{x}")
                for button in ("left","right","middle"):
                    await scene_drag(button);check(await current())
                c=await camera();await click(*c["locate"])
                released=await shot("planar-follow-off")
                old=np.array(released["hud"]["views"][0]["camera"]["target"])
                for button,mods in [("left",()),("right",()),("middle",()),("left",("Control",)),("left",("Alt",))]:
                    await scene_drag(button,mods);check(await current())
                c=await camera()
                assert not c["following"] and c["overhead"]
                assert np.linalg.norm(np.array(c["target"])-old)>1
                assert abs(c["target"][2]-old[2])<.001
                # Double-click and modified drags must not restore an orbital camera.
                v=(await current())["hud"]["views"][0];p=v["pane"]
                await page.mouse.dblclick(p[0]+110,p[1]+130);await page.wait_for_timeout(500)
                await shot("planar-drag-and-doubleclick-lock")
                fixed=np.array((await camera())["target"])
                await click(900,970);await ready();await page.wait_for_timeout(1200)
                np.testing.assert_allclose((await camera())["target"],fixed,atol=.001)
                await shot("planar-free-seek-stays-put")
                c=await camera();old_height=c["top_height"]
                await page.mouse.move(p[0]+100,p[1]+160);await page.mouse.wheel(0,-160)
                await page.wait_for_timeout(700)
                assert abs((await camera())["top_height"]-old_height)>1
                await shot("planar-zoom")
                c=await camera();await click(*c["locate"]);await shot("planar-follow-reenabled")
                # Both modes must track continuously during playback, including heading.
                for name in ("planar","chase"):
                    if name=="chase":await click(*(await camera())["top"])
                    await click(130,973)
                    seen=[]
                    for _ in range(16):
                        await page.wait_for_timeout(200)
                        state=await current();check(state);assert state["playing"]
                        seen.append(state["hud"]["views"][0]["camera"])
                    await click(130,973)
                    assert len({tuple(c["ego"]) for c in seen})>8
                    assert len({tuple(c["heading"]) for c in seen})>8
                    evidence.append({"name":name+"-continuous-follow","cameras":seen})
                    await shot(name+"-continuous-follow")
                c=await camera();await click(*c["locate"])
                c=await camera();assert not c["following"] and not c["overhead"]
                fixed=np.array(c["target"])
                await click(840,970);await ready();await page.wait_for_timeout(1000)
                np.testing.assert_allclose((await camera())["target"],fixed,atol=.001)
                before=np.array((await camera())["position"])
                await scene_drag("left")
                assert np.linalg.norm(np.array((await camera())["position"])-before)>1
                await shot("chase-follow-off-free-orbit")
                c=await camera();await click(*c["locate"]);await shot("chase-reset-comfortable-range")
            async def delay(route):
                if route.request.post_data_json.get("mode")=="dashboard_window":await asyncio.sleep(.35)
                await route.continue_()
            await page.route("**/api/debug_query",delay)
            # Real playback UI button, not a JavaScript setter.
            await click(130,973)
            samples=set()
            for _ in range(28):
                await page.wait_for_timeout(200)
                s=await current();check(s)
                samples.add(s["hud"]["data"]["streams"]["chassis"]["sample"]["sample_ns"])
                assert s["playing"],s
            await click(130,973)
            assert len(samples)>10,len(samples)
            await shot("buffered-playback-no-spinner")
            assert not errors,errors
            print(f"PASS: chassis parity, missing != zero, collapse/expand, layouts, 3 paused seeks, ego/top/restore, {len(samples)} buffered samples",flush=True)
        finally:
            await page.screenshot(path=str(out/"final.png"))
            (out/"evidence.json").write_text(json.dumps(evidence,indent=2))
            (out/"errors.json").write_text(json.dumps(errors,indent=2))
            (out/"console.json").write_text(json.dumps(console,indent=2))
            await browser.close()


if __name__=="__main__":asyncio.run(main())
