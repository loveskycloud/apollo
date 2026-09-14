"""Real UI clicks and screenshots for docked AD layouts. JS diagnostics are read-only."""
import argparse
import bisect
import io
import json
import os
import time
from pathlib import Path
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
from mcap.reader import make_reader
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

URL = "http://127.0.0.1:9091/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy&persist=false&renderer=webgl&theme=dark"
SCENE = "() => ({...window._handle.get_ad_scene_state(), cloud:window._handle.get_point_cloud_state('/lidar/up/points'), panels:window._handle.get_debug_panels_state(), panic:window._handle.has_panicked(), playing:window._handle.get_playing(window._handle.get_active_recording_id())})"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcap", required=True, help="Semantic MCAP produced by the current converter")
    parser.add_argument("--record", help="Optional original .record path to open through the UI")
    parser.add_argument("--url", default=URL)
    parser.add_argument("--out", required=True)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--custom", action="store_true")
    parser.add_argument("--geometry-only", action="store_true")
    args = parser.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    logs, evidence = [], []
    sample_times = {}
    geometry, classes = {}, {}
    with open(args.mcap, "rb") as source:
        for schema, channel, message in make_reader(source).iter_messages(topics=["/vehicle", "/planning/trajectory", "/lidar/up/points"]):
            sample_times.setdefault(channel.topic, []).append(message.log_time)
            if channel.topic != "/lidar/up/points":
                if schema.id not in classes:
                    pool=descriptor_pool.DescriptorPool()
                    for fd in descriptor_pb2.FileDescriptorSet.FromString(schema.data).file: pool.Add(fd)
                    classes[schema.id]=message_factory.GetMessageClass(pool.FindMessageTypeByName(schema.name))
                msg=classes[schema.id].FromString(message.data)
                if channel.topic == "/vehicle":
                    p=msg.pose.position
                    geometry[(channel.topic,message.log_time)]=[p.x,p.y,p.z]
                else:
                    geometry[(channel.topic,message.log_time)]=list(msg.xyz)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True, args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width":1440,"height":1000}, device_scale_factor=1)
        page.route("https://tel.rerun.io/**", lambda r:r.fulfill(status=200,body="{}"))
        page.on("console",lambda m:logs.append([m.type,m.text]) if m.type!="debug" else None)
        page.on("pageerror",lambda e:logs.append(["pageerror",str(e)]))
        def shot(name):
            bitmap = page.screenshot(path=str(out/f"{name}.png"))
            try:
                value = page.evaluate(SCENE)
                if name in ("perception", "planning", "planning-seek", "control"):
                    assert not value["panic"] and not value["playing"]
                    assert value["cloud"]["timeline"] == "message_time", value["cloud"]
                    t = int(value["playhead_ns"])
                    for key, topic in [("ego_pose", "/vehicle"), ("planned_path", "/planning/trajectory"), ("cloud", "/lidar/up/points")]:
                        times = sample_times[topic]
                        i = bisect.bisect_right(times, t)-1
                        assert i >= 0 and int(value[key]["sample_ns"]) == times[i], (name,key,value[key])
                        expected=geometry.get((topic,times[i]))
                        if key=="ego_pose": np.testing.assert_allclose(value[key]["translation"],expected,atol=1e-5)
                        if key=="planned_path":
                            assert value[key]["point_count"]==len(expected)//3
                            np.testing.assert_allclose(value[key]["first"],expected[:3],atol=1e-5)
                            np.testing.assert_allclose(value[key]["last"],expected[-3:],atol=1e-5)
                    assert value["ego_model"]["count"] > 0
                    if name in ("perception", "planning", "planning-seek", "control"):
                        width = 620 if name == "control" else 895
                        pixels = np.asarray(Image.open(io.BytesIO(bitmap)).convert("RGB"))[30:565,80:width].astype(int)
                        r,g,b=(pixels[:,:,i] for i in range(3))
                        colored = int(((r>180)&(g>80)&(b<170)).sum())
                        assert colored > 1000, (name,"No rendered point cloud",colored)
                        value["scene_colored_pixels"] = colored
                evidence.append({"name":name,**value})
                (out/f"{name}.json").write_text(json.dumps(value,indent=2))
                print(json.dumps({"name":name,"layout":value["layout"],"cloud":value["cloud"],"panels":[{k:p.get(k) for k in ("kind","error","sample_ns")} for p in value["panels"]]}),flush=True)
            except Exception:
                if name != "final": raise
        try:
            page.goto(args.url.replace("persist=false", "persist=true") if args.custom else args.url,wait_until="domcontentloaded")
            page.wait_for_function("window._handle !== undefined",timeout=60000)
            page.wait_for_timeout(700)
            page.mouse.click(37,115); page.wait_for_timeout(600)
            pixels=np.asarray(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))
            band=pixels[290:341,96:370].astype(int)
            dark=(band.max(axis=2)<45)&((band.max(axis=2)-band.min(axis=2))<8)
            rows=np.flatnonzero(dark.mean(axis=1)>.5)
            assert len(rows)>5
            page.mouse.click(205,290+int(np.median(rows)))
            page.keyboard.insert_text(args.record or args.mcap); page.wait_for_timeout(400); page.keyboard.press("Enter")
            page.wait_for_function("() => {try{return window._handle.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length>0}catch{return false}}",timeout=60000)
            page.wait_for_timeout(1500)
            page.mouse.click(37,115)
            page.wait_for_timeout(400)
            layers=page.evaluate("window._handle.get_display_layers_state()")
            planning=next(l for l in layers["layers"] if l["path"]=="planning/trajectory")
            if not planning["enabled"]:
                page.mouse.click(*planning["checkbox"]); page.wait_for_timeout(1000)
            page.mouse.click(140,40); page.wait_for_timeout(500); shot("pose-and-plan-only")
            if args.geometry_only:
                page.mouse.click(840,970); page.wait_for_timeout(2500)
                page.mouse.click(37,189); page.wait_for_timeout(400); page.mouse.click(180,113)
                page.wait_for_timeout(1200); page.mouse.click(37,189); page.wait_for_timeout(2000)
                shot("planning-no-lidar")
                value=page.evaluate(SCENE)
                assert not value["panic"] and not value["playing"]
                assert value["cloud"]["points"]==0 and value["ego_model"]["count"]>0
                assert value["planned_path"]["point_count"]>0
                return
            page.mouse.click(140,40); page.wait_for_timeout(350)
            page.wait_for_timeout(500)
            shot("topics")
            if args.probe: return
            lidar=next(l for l in page.evaluate("window._handle.get_display_layers_state()")["layers"] if l["path"]=="sensing/lidar/main")
            page.mouse.click(*lidar["checkbox"]); page.wait_for_timeout(1500)
            shot("selected")
            page.mouse.click(140,40)
            page.mouse.click(840,970); page.wait_for_timeout(2500)
            shot("perception")
            before_layout = page.evaluate(SCENE)["playhead_ns"]
            page.mouse.click(37,189); page.wait_for_timeout(400); shot("layout-menu")
            page.mouse.click(180,113); page.wait_for_timeout(1500); page.mouse.click(37,189)
            page.wait_for_timeout(3500); shot("planning")
            assert page.evaluate(SCENE)["playhead_ns"] == before_layout, "Layout change moved playhead"
            page.mouse.click(805,12); page.wait_for_timeout(600); shot("planning-warning")
            page.keyboard.press("Escape")
            page.mouse.move(840,970)
            page.mouse.click(1090,970); page.wait_for_timeout(2500); shot("planning-seek")
            before_layout = page.evaluate(SCENE)["playhead_ns"]
            page.mouse.click(37,189); page.wait_for_timeout(300); page.mouse.click(180,161); page.wait_for_timeout(1000); page.mouse.click(37,189)
            page.wait_for_function("""() => {
                const ps=window._handle.get_debug_panels_state();
                return ps.length===5 && ps.every(p=>!p.pending && !p.error) &&
                    ps.filter(p=>p.kind==='Signal plot').every(p=>p.series?.length && p.series.every(s=>s.count>0 && !s.error));
            }""", timeout=45000)
            page.wait_for_timeout(400); shot("control")
            assert page.evaluate(SCENE)["playhead_ns"] == before_layout, "Layout change moved playhead"
            page.mouse.click(37,263); page.wait_for_timeout(500); shot("panel-editor")
            if args.custom:
                before = page.evaluate(SCENE)
                assert len(before["layout"]["views"]) == 6
                page.mouse.click(167,113)  # + Topic inspector
                page.wait_for_timeout(2000); shot("panel-added")
                added = page.evaluate(SCENE)
                assert len(added["layout"]["views"]) == 7
                page.mouse.move(1200,12); page.mouse.down()
                page.mouse.move(410,581,steps=30); page.wait_for_timeout(500); page.mouse.up()
                page.wait_for_timeout(1000); shot("panel-docked")
                # The same ordered view list backs the editor and the read-only diagnostics.
                names = [v["name"] for v in added["layout"]["views"]]
                remove_index = names.index("State transitions")
                page.mouse.click(94,419+23*remove_index)
                page.wait_for_timeout(800); shot("panel-removed")
                saved_names = sorted(v["name"] for v in page.evaluate(SCENE)["layout"]["views"])
                assert len(saved_names) == 6 and "State transitions" not in saved_names
                page.mouse.click(170,557)  # Save as Custom
                page.wait_for_timeout(600)
                page.mouse.click(170,580)
                with page.expect_download() as download:
                    page.get_by_role("link",name="click here to download your file").click()
                download.value.save_as(str(out/"custom-layout.rbl"))
                if page.get_by_role("button",name="Ok",exact=True).count():
                    page.get_by_role("button",name="Ok",exact=True).click()
                assert (out/"custom-layout.rbl").stat().st_size > 1000
                page.mouse.click(37,189); page.wait_for_timeout(300)
                page.mouse.click(180,113); page.wait_for_timeout(1000)
                page.mouse.click(180,209); page.wait_for_timeout(1800)
                page.mouse.click(37,189); page.wait_for_timeout(1200); shot("custom-restored")
                assert sorted(v["name"] for v in page.evaluate(SCENE)["layout"]["views"]) == saved_names
                # eframe periodically saves browser-local settings.
                page.wait_for_timeout(32000)
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function("window._handle !== undefined",timeout=60000)
                page.wait_for_timeout(3500); shot("custom-reloaded")
                assert sorted(v["name"] for v in page.evaluate(SCENE)["layout"]["views"]) == saved_names
                # Reopen the source: Custom persists the window tree/config, not bag payloads.
                page.mouse.click(37,115); page.wait_for_timeout(600)
                pixels=np.asarray(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))
                band=pixels[290:341,96:370].astype(int)
                dark=(band.max(axis=2)<45)&((band.max(axis=2)-band.min(axis=2))<8)
                rows=np.flatnonzero(dark.mean(axis=1)>.5)
                assert len(rows)>5
                page.mouse.click(205,290+int(np.median(rows))); page.keyboard.press("Control+A")
                page.keyboard.insert_text(args.mcap); page.keyboard.press("Enter")
                page.wait_for_function("() => window._handle.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length>0",timeout=60000)
                page.mouse.click(37,115); page.wait_for_timeout(3500); shot("custom-reopened")
                assert sorted(v["name"] for v in page.evaluate(SCENE)["layout"]["views"]) == saved_names
            assert not page.evaluate("window._handle.has_panicked()")
        finally:
            shot("final")
            (out/"browser-log.json").write_text(json.dumps(logs,indent=2))
            (out/"evidence.json").write_text(json.dumps(evidence,indent=2))
            browser.close()


if __name__=="__main__":main()
