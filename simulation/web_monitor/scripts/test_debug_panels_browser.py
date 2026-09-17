"""Real mouse/keyboard acceptance of AD debug panels; JS diagnostics are read-only.

Run with the control bag on a freshly restarted test service.
"""
import argparse
import bisect
import io
import json
import math
import os
from pathlib import Path
import sys
import time

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"


def _apollo_record_tools():
    for candidate in (
        "/apollo_workspace/modules/simulation/tools/apollo_record_tools",
        "/apollo_workspace/simulation/tools/apollo_record_tools",
        str(Path(__file__).resolve().parents[2] / "tools/apollo_record_tools"),
    ):
        if Path(candidate).is_dir():
            return candidate
    return "/apollo_workspace/modules/simulation/tools/apollo_record_tools"


sys.path.insert(0, _apollo_record_tools())
from google.protobuf.message_factory import GetMessageClass
from mcap.reader import make_reader
from mcap_topic_debug import pool_from_file_descriptor_set
from playwright.sync_api import sync_playwright
from PIL import Image
import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:9091/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy")
    p.add_argument("--record", default="/home/wangsheng/code/apollo/data/bag/20260729201441.record.00000.20260729201441")
    p.add_argument("--mcap", default="/apollo_workspace/data/bag/.wm_mcap_cache/015f40ec5fab311b.mcap")
    p.add_argument("--out", required=True)
    p.add_argument("--catalog-only", action="store_true")
    args = p.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    reference = []
    with open(args.mcap,"rb") as f:
        reader = make_reader(f)
        cls = None
        for schema, _, message in reader.iter_messages(topics=["/apollo/control"]):
            if cls is None:
                cls = GetMessageClass(pool_from_file_descriptor_set(schema.data).FindMessageTypeByName(schema.name))
            msg = cls(); msg.ParseFromString(message.data)
            reference.append((message.log_time, msg.steering_target, msg.brake))
    times = [r[0] for r in reference]
    logs, replies, evidence = [], [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium",headless=True,args=["--use-gl=angle","--use-angle=swiftshader","--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width":1440,"height":1000},device_scale_factor=1)
        from urllib.parse import urlsplit
        parsed=urlsplit(args.url)
        page.context.grant_permissions(["clipboard-read","clipboard-write"],origin=f"{parsed.scheme}://{parsed.netloc}")
        page.route("https://tel.rerun.io/**",lambda r:r.fulfill(status=200,body="{}"))
        page.on("console",lambda m:logs.append([m.type,m.text]) if m.type != "debug" else None)
        page.on("pageerror",lambda e:logs.append(["pageerror",str(e)]))
        page.on("response",lambda r:replies.append([r.url,r.status,r.text()]) if "/api/debug_query" in r.url else None)

        def click(x,y): page.mouse.click(x,y); page.wait_for_timeout(300)
        def panels(): return page.evaluate("window._handle.get_debug_panels_state()")
        def playback():
            return page.evaluate("""() => {const h=window._handle,id=h.get_active_recording_id();return {playing:h.get_playing(id),panic:h.has_panicked(), ...h.get_point_cloud_state('/lidar/up/points')};}""")
        def wait_panel(kind):
            deadline=time.monotonic()+50
            while time.monotonic()<deadline:
                found=[p for p in panels() if p["kind"]==kind]
                if found and not found[-1]["pending"]:
                    assert not found[-1]["error"],found[-1]
                    if found[-1].get("at_ns"):
                        if kind in ("Topic inspector","Value watch","Trajectory XY","Topic health") and found[-1].get("follow",True):
                            if found[-1]["at_ns"] != playback()["playhead_ns"]:
                                page.wait_for_timeout(200); continue
                        return found[-1]
                page.wait_for_timeout(200)
            raise AssertionError(("Panel not ready",kind,panels()))
        def snap(name, data=None):
            page.wait_for_timeout(300); page.screenshot(path=str(out/f"{name}.png"))
            evidence.append({"name":name,"panel":data,"playback":playback()})
            print(name, json.dumps({k:v for k,v in (data or {}).items() if k not in ("fields","visible_fields")}),flush=True)
        def verify_snapshot(data):
            t=int(data["at_ns"]); expected=reference[bisect.bisect_right(times,t)-1]
            fields={f["path"]:f["value"] for f in data["fields"]}
            assert int(data["sample_ns"])==expected[0],(data,expected)
            assert fields["steering_target"]==expected[1]
            assert fields["brake"]==expected[2]
        def close_panel(): click(1075,84)
        def fill(x,y,text):
            click(x,y); page.keyboard.press("Control+A"); page.keyboard.press("Backspace"); page.keyboard.insert_text(text); page.wait_for_timeout(350)
        try:
            page.goto(args.url+"&persist=false&renderer=webgl&theme=dark",wait_until="domcontentloaded")
            page.wait_for_function("window._handle !== undefined",timeout=60000)
            page.wait_for_timeout(500)
            click(37,115)
            page.wait_for_timeout(600)
            # Source width changes with recording properties; locate the visible
            # dark input band instead of assuming a fixed wrapped-label height.
            pixels=np.asarray(Image.open(io.BytesIO(page.screenshot())).convert("RGB"))
            band=pixels[290:341,96:370].astype(int)
            dark=(band.max(axis=2)<45)&((band.max(axis=2)-band.min(axis=2))<8)
            rows=np.flatnonzero(dark.mean(axis=1)>0.5)
            assert len(rows)>5,"Source path input not visible"
            click(205,290+int(np.median(rows)))
            page.keyboard.insert_text(args.record); page.wait_for_timeout(400)
            page.keyboard.press("Enter")
            page.wait_for_function("""() => {const h=window._handle,id=h.get_active_recording_id(),tl=id&&h.get_active_timeline(id);return tl&&h.get_time_for_timeline(id,tl)!=null&&h.get_point_cloud_state('/lidar/up/points').cached_ranges_ns.length>0;}""",timeout=45000)
            page.wait_for_timeout(1500)
            click(37,115); click(130,40) # close Source and topic picker
            click(840,970) # mid-bag while paused
            click(37,263) # Panel drawer
            page.wait_for_timeout(600)
            snap("panel-catalog")
            if args.catalog_only: return
            kinds=["Topic inspector","Signal plot","Value watch","Control dashboard","Trajectory XY","State transitions","Topic health"]
            for index,kind in enumerate(kinds):
                click(180,124+index*45)
                data=wait_panel(kind)
                assert not playback()["playing"] and not playback()["panic"]
                if kind in ("Topic inspector","Value watch"): verify_snapshot(data)
                if kind in ("Signal plot","Control dashboard"):
                    assert all(s["count"]>100 and not s["error"] for s in data["series"]),data
                    if kind=="Control dashboard":
                        assert len(data["series"])==12
                        assert [s["field"] for s in data["series"]]==[
                            "debug.simple_lon_debug.speed_reference","speed_mps",
                            "debug.simple_lon_debug.acceleration_reference","debug.simple_lon_debug.current_acceleration",
                            "steering_target","steering_percentage","throttle","brake",
                            "debug.simple_lon_debug.station_error","debug.simple_lat_debug.lateral_error",
                            "debug.simple_lat_debug.heading_error","latency_stats.total_time_ms"]
                        center=int(data["at_ns"])
                        raw=reference[bisect.bisect_left(times,center-5_000_000_000):bisect.bisect_right(times,center+5_000_000_000)]
                        for chart_index,raw_index in [(4,1),(7,2)]:
                            values=[row[raw_index] for row in raw]
                            chart=data["series"][chart_index]
                            assert chart["count"]==len(values)
                            assert math.isclose(chart["stats"]["mean"],sum(values)/len(values),rel_tol=1e-12,abs_tol=1e-12)
                            assert chart["stats"]["min"]==min(values) and chart["stats"]["max"]==max(values)
                if kind=="Value watch":
                    assert set(data["visible_fields"])=={"speed","throttle","brake","steering_target","gear_location"}
                if kind=="Trajectory XY": assert data["planned_count"]>10 and data["actual_count"]>100
                if kind=="State transitions": assert all(s["count"]>0 for s in data["series"])
                if kind=="Topic health": assert data["selected"]["count"]==5994 and data["topics_count"]==37
                snap(kind.lower().replace(" ","-"),data)
                if kind=="Topic inspector":
                    click(1090,970); data=wait_panel(kind); verify_snapshot(data)
                    assert data["sample_ns"]!=evidence[-1]["panel"]["sample_ns"]
                    snap("inspector-paused-seek",data)
                    previous=data["previous_ns"]
                    click(370,211); data=wait_panel(kind); verify_snapshot(data)
                    assert data["sample_ns"]==previous,(previous,data)
                    next_time=data["next_ns"]
                    click(473,211); data=wait_panel(kind); verify_snapshot(data)
                    assert data["sample_ns"]==next_time
                    fill(515,235,"steering")
                    data=wait_panel(kind)
                    assert data["visible_fields"] and all("steering" in f for f in data["visible_fields"])
                    snap("inspector-field-filter",data)
                    fill(515,235,"")
                    click(323,137) # freeze
                    frozen=wait_panel(kind)["sample_ns"]
                    click(900,970); data=wait_panel(kind)
                    assert not data["follow"] and data["sample_ns"]==frozen
                    snap("inspector-frozen",data)
                    click(323,137); data=wait_panel(kind); verify_snapshot(data)
                    assert data["sample_ns"]!=frozen
                    click(555,211)
                    page.wait_for_timeout(500)
                    copied=json.loads(page.evaluate("navigator.clipboard.readText()"))
                    assert copied["steering_target"]==next(f["value"] for f in data["fields"] if f["path"]=="steering_target")
                    snap("inspector-copy-json",data)
                    # Missing topic is a visible panel error, not an empty success.
                    fill(500,162,"/missing_debug_topic")
                    page.wait_for_function("window._handle.get_debug_panels_state().some(p=>p.error&&p.error.includes('Topic not present'))",timeout=10000)
                    snap("missing-topic-error",panels()[0])
                    fill(500,162,"/apollo/control"); wait_panel(kind)
                    click(1090,970); wait_panel(kind)
                if kind=="Signal plot":
                    fill(500,190,"debug.simple_lat_debug.lateral_error")
                    click(745,190); data=wait_panel(kind)
                    assert len(data["series"])==2 and all(s["count"]>100 and not s["error"] for s in data["series"])
                    snap("custom-signal-plot",data)
                    before=playback()["playhead_ns"]
                    click(600,385); page.wait_for_timeout(400)
                    assert playback()["playhead_ns"]!=before and not playback()["playing"]
                    snap("plot-click-seek",wait_panel(kind))
                    click(1090,970); wait_panel(kind)
                if kind=="Control dashboard":
                    page.mouse.move(1080,260); page.wait_for_timeout(300)
                    page.mouse.down(); page.mouse.move(1080,690,steps=20); page.mouse.up(); page.wait_for_timeout(500)
                    snap("control-errors-and-runtime",data)
                if kind=="Trajectory XY":
                    old_sample=data["sample_ns"]
                    click(800,970); data=wait_panel(kind)
                    assert data["sample_ns"]!=old_sample and data["planned_count"]>10
                    snap("trajectory-paused-seek",data)
                    click(1090,970); wait_panel(kind)
                if kind=="State transitions":
                    state_data=next(json.loads(r[2]) for r in reversed(replies) if json.loads(r[2]).get("mode")=="states")
                    expected=state_data["series"][1]["times_ns"][1]
                    click(420,398); page.wait_for_timeout(400)
                    assert not playback()["playing"]
                    assert playback()["playhead_ns"]==expected,(playback(),expected)
                    snap("state-event-seek",wait_panel(kind))
                close_panel()
                assert not panels(),("Close panel failed",panels())
            # Two independent request streams follow the same playhead.
            click(180,124); wait_panel("Topic inspector")
            click(180,214); wait_panel("Value watch")
            click(910,970)
            inspector=wait_panel("Topic inspector"); watch=wait_panel("Value watch")
            verify_snapshot(inspector); verify_snapshot(watch)
            assert inspector["sample_ns"]==watch["sample_ns"] and len(panels())==2
            snap("two-panels-synchronized",watch)
            close_panel(); close_panel()
            click(180,124); wait_panel("Topic inspector")
            click(370,284) # turn pinned fields into watch
            data=wait_panel("Value watch")
            assert set(data["visible_fields"])=={"speed","throttle","brake","steering_target","gear_location"}
            snap("inspector-to-watch",data)
            # In watch, only the plot action remains at the left of this row.
            click(400,284); data=wait_panel("Signal plot")
            assert len(data["series"])==4 and all(s["count"]>100 and not s["error"] for s in data["series"])
            snap("pinned-fields-to-plot",data)
            assert not [l for l in logs if l[0] in ("error","pageerror")],logs
            print("PASS: seven panels, exact paused topic samples, seek updates, control curves, trajectory, state and health",flush=True)
        except Exception:
            import traceback
            (out/"failure-state.json").write_text(json.dumps({"panels":panels(),"playback":playback(),"traceback":traceback.format_exc()},indent=2))
            page.screenshot(path=str(out/"failure.png"))
            raise
        finally:
            (out/"evidence.json").write_text(json.dumps(evidence,indent=2))
            (out/"browser-log.json").write_text(json.dumps(logs,indent=2))
            (out/"responses.json").write_text(json.dumps(replies,indent=2))
            browser.close()


if __name__=="__main__": main()
