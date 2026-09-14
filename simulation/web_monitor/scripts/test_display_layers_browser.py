"""Real browser layer switches: cached visibility, paused seek, colors and layout changes."""
import argparse
import asyncio
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

STATE = """() => ({layers:window._handle.get_display_layers_state(),
 scene:window._handle.get_ad_scene_state(),cloud:window._handle.get_point_cloud_state('/lidar/up/points'),
 playing:window._handle.get_playing(window._handle.get_active_recording_id()),panic:window._handle.has_panicked()})"""


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mcap", required=True)
    ap.add_argument("--record", help="Open this original record through the UI instead of its cache")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cameras-only", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:9091/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy&persist=false&renderer=webgl&theme=dark")
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    evidence, errors = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chromium", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width":1440,"height":1000})
        await page.route("https://tel.rerun.io/**", lambda r:r.fulfill(status=200,body="{}"))
        page.on("pageerror",lambda e:errors.append(str(e)))
        async def click(x,y):
            await page.mouse.click(x,y); await page.wait_for_timeout(400)
        async def shot(name):
            bitmap=await page.screenshot(path=str(out/f"{name}.png"))
            state=await page.evaluate(STATE)
            assert not state["playing"] and not state["panic"]
            evidence.append({"name":name,**state})
            return state,np.asarray(Image.open(io.BytesIO(bitmap)).convert("RGB")).astype(int)
        async def toggle(path,on):
            layers=await page.evaluate("window._handle.get_display_layers_state()")
            layer=next(l for l in layers["layers"] if l["path"]==path)
            if not layer["checkbox"]:
                await click(125,40)
                layers=await page.evaluate("window._handle.get_display_layers_state()")
                layer=next(l for l in layers["layers"] if l["path"]==path)
            assert layer["checkbox"], layer
            if layer["enabled"] != on:
                await click(*layer["checkbox"])
            await page.wait_for_function("""([path,on])=>{
                const s=window._handle.get_display_layers_state(),l=s.layers.find(l=>l.path===path);
                return l.enabled===on && s.views.every(v=>v.entities.filter(e=>l.entities.some(root=>e.path===root||e.path.startsWith(root+'/'))).every(e=>e.visible===on));
            }""",arg=[path,on],timeout=10000)
        async def close_layers():
            if any(l["checkbox"] for l in (await page.evaluate("window._handle.get_display_layers_state()"))["layers"]):
                await click(125,40)
        try:
            await page.goto(args.url,wait_until="domcontentloaded")
            await page.wait_for_function("window._handle !== undefined",timeout=60000)
            await page.wait_for_timeout(1000); await click(37,115)
            bitmap=np.asarray(Image.open(io.BytesIO(await page.screenshot())).convert("RGB"))
            band=bitmap[290:341,96:370].astype(int)
            rows=np.flatnonzero(((band.max(2)<45)&(band.max(2)-band.min(2)<8)).mean(1)>.5)
            assert len(rows)>5
            await click(205,290+int(np.median(rows)))
            await page.keyboard.insert_text(args.record or args.mcap); await page.wait_for_timeout(400); await page.keyboard.press("Enter")
            await page.wait_for_function("()=>{try{return window._handle.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length>0}catch{return false}}",timeout=60000)
            await page.wait_for_timeout(1000); await click(37,115)
            initial,_=await shot("layer-tree")
            if args.cameras_only:
                paths=[l["path"] for l in initial["layers"]["layers"]]
                assert "sensing/camera/camera_front/front120" in paths and "sensing/camera/camera_front/front30" in paths
                assert "sensing/camera/camera_left" in paths and "sensing/camera/camera_right" in paths
                assert "sensing/camera/camera_back" in paths  # This older bag really contains Rear.
                await close_layers(); await page.wait_for_timeout(5000)
                _,before=await shot("camera-on")
                await toggle("sensing/camera/camera_front/front120",False); await close_layers()
                _,after=await shot("camera-off-cached")
                assert np.count_nonzero(np.max(np.abs(before[40:440,900:1150]-after[40:440,900:1150]),axis=2)>30)>10000
                await toggle("sensing/camera/camera_front/front120",True); await close_layers()
                await page.wait_for_timeout(2000); await shot("camera-restored")
                await click(125,40)
                nodes=(await page.evaluate("window._handle.get_display_layers_state()"))["nodes"]
                await click(*nodes["sensing/camera"])
                state,_=await shot("camera-group-off")
                assert all(not l["enabled"] for l in state["layers"]["layers"] if l["path"].startswith("sensing/camera/"))
                print("PASS: distinct front cameras, cached image hide/restore, parent camera switch",flush=True)
                return
            assert not next(l for l in initial["layers"]["layers"] if l["path"]=="planning/trajectory")["enabled"]
            await toggle("planning/trajectory",True); await close_layers()
            await click(840,970)
            await page.wait_for_function("()=>window._handle.get_ad_scene_state().planned_path.point_count>0",timeout=30000)
            await page.wait_for_timeout(1500)
            on,_=await shot("planning-on")
            await toggle("planning/trajectory",False); await close_layers()
            off,_=await shot("planning-off-cached")
            assert off["scene"]["planned_path"]["point_count"]>0, "Hiding must not delete cached data"
            assert on["scene"]["playhead_ns"]==off["scene"]["playhead_ns"]
            await toggle("localization/pose",False); await close_layers(); await shot("ego-off-cached")
            await toggle("sensing/lidar/main",True); await close_layers()
            await page.wait_for_function("()=>window._handle.get_point_cloud_state('/lidar/up/points').points>0",timeout=30000)
            await page.wait_for_timeout(1200)
            _,pixels=await shot("height-colored-lidar")
            p=pixels[70:560,80:890]; r,g,b=(p[:,:,i] for i in range(3))
            yellow=int(((r>210)&(g>165)&(b<125)).sum())
            orange=int(((r>210)&(g>100)&(g<165)&(b<130)).sum())
            assert yellow>2000 and orange>30, (yellow,orange)
            await toggle("sensing/lidar/main",False); await close_layers()
            hidden,pixels=await shot("lidar-off-cached")
            assert hidden["cloud"]["points"]>0
            p=pixels[70:560,80:890]; r,g,b=(p[:,:,i] for i in range(3))
            assert int(((r>210)&(g>165)&(b<125)).sum())<100
            await toggle("perception/obstacles",True)
            await toggle("prediction/trajectories",True)
            await page.wait_for_timeout(1800); await shot("empty-prediction-notice")
            await close_layers(); await page.wait_for_timeout(2000); await shot("perception-prediction")
            for label,row in [("planning",113),("control",161)]:
                await click(37,189); await click(180,row); await click(37,189)
                await page.wait_for_timeout(1200)
                state,_=await shot(label+"-preserves-hidden")
                for layer in state["layers"]["layers"]:
                    if layer["path"] in ("sensing/lidar/main","planning/trajectory","localization/pose"):
                        assert not layer["enabled"]
                        for view in state["layers"]["views"]:
                            assert all(not e["visible"] for e in view["entities"] if e["path"] in layer["entities"])
            await toggle("sensing/lidar/main",True); await toggle("planning/trajectory",True); await toggle("localization/pose",True)
            await close_layers(); await click(1080,970); await page.wait_for_timeout(2000)
            await shot("all-enabled-paused-seek")
            assert not errors,errors
            print(f"PASS: cached layer hide/show, layout preservation, paused seek, yellow={yellow} orange={orange}",flush=True)
        finally:
            await page.screenshot(path=str(out/"final.png"))
            (out/"evidence.json").write_text(json.dumps(evidence,indent=2))
            (out/"page-errors.json").write_text(json.dumps(errors,indent=2))
            await browser.close()


if __name__=="__main__":asyncio.run(main())
