#!/usr/bin/env python3
"""Browser coverage for the actual Sim editor/queue, with server-side jobs."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright
from sim_browser_helpers import open_sim_tasks


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:9091/?url=rerun%2Bhttp%3A%2F%2F127.0.0.1%3A9877%2Fproxy&persist=false&renderer=webgl&theme=dark")
    parser.add_argument("--out", required=True)
    parser.add_argument("--exercise", action="store_true", help="Submit real bag/world jobs through mouse/keyboard, cancel a queued job, replay output")
    parser.add_argument("--replay-job", help="Inspect/replay an existing completed task without enqueueing")
    parser.add_argument("--clock-checks", action="store_true", help="Verify the two product clocks, paused seeks and actual playback")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chromium", headless=True,
            args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = await browser.new_page(viewport={"width":1440,"height":1000})
        errors = []
        console = []
        query_clocks = []
        def observe_query(request):
            if request.url.endswith('/api/debug_query') and request.post_data:
                query_clocks.append(json.loads(request.post_data).get('clock'))
        page.on('request', observe_query)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: console.append({"type": message.type, "text": message.text}))
        await page.route("https://tel.rerun.io/**", lambda route: route.fulfill(status=200,body="{}"))
        try:
            await page.goto(args.url)
            await page.wait_for_function("()=>window._handle && !window._handle.has_panicked()",timeout=60000)
            await page.wait_for_timeout(3000)
            await page.mouse.click(35,340)
            await page.wait_for_function("()=>window._handle.get_simulation_state()?.enqueue",timeout=30000)
            await page.wait_for_timeout(2500)
            await page.wait_for_function("()=>window._handle.get_simulation_state()?.catalog_ready",timeout=30000)
            await page.screenshot(path=str(out/"sim-editor.png"))
            state = await page.evaluate("()=>window._handle.get_simulation_state()")
            if args.exercise:
                async def state_now():
                    return await page.evaluate("()=>window._handle.get_simulation_state()")
                async def click(key):
                    state = await state_now()
                    await page.mouse.click(*state[key], delay=80)
                    await page.wait_for_timeout(350)
                async def fill(key, value):
                    await click(key)
                    await page.keyboard.press("Control+a")
                    await page.keyboard.insert_text(value)
                    await page.keyboard.press("Tab")
                async def enqueue():
                    previous = (await state_now())["last_enqueued"]
                    await page.wait_for_function("()=>!window._handle.get_simulation_state().pending",timeout=10000)
                    await click("enqueue")
                    await page.wait_for_function("previous=>{const s=window._handle.get_simulation_state();return s.last_enqueued && s.last_enqueued!==previous}",arg=previous,timeout=15000)
                    job_id = (await state_now())["last_enqueued"]
                    await page.wait_for_function("id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id)",arg=job_id,timeout=15000)
                    return job_id
                root = "/apollo_workspace"
                fixtures = root + "/simulation/web_monitor/test-artifacts/simulation-20260912"
                await fill("Scenario", fixtures + "/input.record")
                await fill("Map", root + "/data/bag/data_with_map/extracted/od_hq_map")
                await fill("Vehicle config", root + "/data/bag/data_with_map/extracted/Jiyu_01/modules/common/data/vehicle_param.pb.txt")
                bag_job = await enqueue()
                await click("config_tab")
                await click("world")
                await page.wait_for_function("()=>window._handle.get_simulation_state().kind==='world'",timeout=10000)
                await fill("Scenario", fixtures + "/world.scenario.json")
                await click("ROUTING")
                world_job = await enqueue()
                await click("config_tab")
                cancelled_job = await enqueue()
                await open_sim_tasks(page, cancelled_job)
                for _ in range(20):
                    await click("cancel_" + cancelled_job)
                    await page.wait_for_timeout(300)
                    if next(j for j in (await state_now())["jobs"] if j["id"] == cancelled_job)["stage"] == "cancelled": break
                await page.screenshot(path=str(out / "queued.png"))
                await page.wait_for_function("""ids=>ids.every(id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id && ['completed','failed','cancelled'].includes(j.stage)))""",
                    arg=[bag_job,world_job], timeout=240000)
                state = await state_now()
                jobs = {j["id"]:j for j in state["jobs"]}
                (out / "jobs.json").write_text(json.dumps(jobs, indent=2))
                assert jobs[bag_job]["stage"] == "completed", jobs[bag_job]
                assert jobs[bag_job]["config"]["kind"] == "bag"
                assert jobs[world_job]["stage"] == "completed", jobs[world_job]
                assert jobs[world_job]["config"]["kind"] == "world"
                assert jobs[cancelled_job]["stage"] == "cancelled"
                bag_end = jobs[bag_job]["history"][-1]["wall_time"]
                world_start = next(e["wall_time"] for e in jobs[world_job]["history"] if e["stage"] == "data_preparation")
                assert world_start >= bag_end, "Queue overlapped tasks"
                await page.screenshot(path=str(out / "completed.png"))
                args.replay_job = world_job
                print("PASS: mouse/keyboard enqueue, FIFO, cancellation, bag+world repeat analysis",flush=True)
            if args.replay_job:
                await open_sim_tasks(page, args.replay_job)
                key = "replay_" + args.replay_job + "_0"
                await page.wait_for_function("key=>window._handle.get_simulation_state()[key]",arg=key,timeout=30000)
                state = await page.evaluate("()=>window._handle.get_simulation_state()")
                await page.mouse.click(*state[key],delay=80)
                await page.wait_for_function("()=>{const h=window._handle,r=h.get_playback_state();return r?.clock_ready&&!r.pending&&!r.waiting_receipt&&!r.error&&r.receivers.includes(r.proxy)&&r.recording===h.get_active_recording_id()}", timeout=90000)
                await page.wait_for_function("()=>{try{return window._handle.get_ad_scene_state().planned_path.point_count>0}catch{return false}}", timeout=90000)
                await page.wait_for_function("""()=>{try{const s=window._handle.get_vehicle_dashboard_state();return s.views.length && s.data.covered && s.data.streams.chassis.sample && !s.data.error}catch{return false}}""",timeout=60000)
                await page.wait_for_function("()=>{const p=window._handle.get_debug_panels_state();return p.length>=5 && p.every(p=>!p.pending && !p.error && p.at_ns)}",timeout=60000)
                hud = await page.evaluate("()=>window._handle.get_vehicle_dashboard_state()")
                await page.mouse.click(114,40,delay=80)  # Collapse observed viewport Layers header.
                await page.mouse.click(*hud["views"][0]["camera"]["locate"],delay=80)
                await page.mouse.move(70,900)
                await page.wait_for_timeout(8000)
                evidence = await page.evaluate("()=>({scene:window._handle.get_ad_scene_state(),playback:window._handle.get_playback_state(),hud:window._handle.get_vehicle_dashboard_state(),panels:window._handle.get_debug_panels_state(),panic:window._handle.has_panicked()})")
                assert not evidence["panic"]
                assert evidence["scene"]["ego_model"]["count"] > 0
                assert evidence["scene"]["planned_path"]["point_count"] > 0
                assert evidence["hud"]["views"][0]["camera"]["following"]
                (out / "replay-state.json").write_text(json.dumps(evidence,indent=2))
                await page.screenshot(path=str(out / "replay.png"))
                print("PASS: replay renders ego/trajectory, chassis HUD and all five Control debug panels",flush=True)
                if args.clock_checks:
                    samples = []
                    for clock in ("publish_time", "message_time"):
                        await page.evaluate("clock=>{const h=window._handle,id=h.get_active_recording_id();h.set_time_for_timeline(id,clock,2000000000)}", clock)
                        await page.wait_for_function("clock=>window._handle.get_ad_scene_state().clock===clock",arg=clock,timeout=15000)
                        # Include cold, distant windows and the end of this 60 s
                        # simulation, then seek backwards into the initial cache.
                        for ns in (2_000_000_000, 30_000_000_000, 58_000_000_000, 6_000_000_000, 3_000_000_000):
                            await page.evaluate("({clock,ns})=>{const h=window._handle;h.set_time_for_timeline(h.get_active_recording_id(),clock,ns)}", {"clock":clock,"ns":ns})
                            await page.wait_for_function("ns=>{const h=window._handle,s=h.get_ad_scene_state(),p=h.get_debug_panels_state(),age=ns-Number(s.ego_pose?.sample_ns);return s.playhead_ns===String(ns)&&s.ego_pose.count>0&&age>=0&&age<=10000000&&p.length>=5&&p.every(p=>!p.error&&!p.pending&&p.has_data)}",arg=ns,timeout=30000)
                            await page.wait_for_timeout(500)
                            sample = await page.evaluate("()=>({scene:window._handle.get_ad_scene_state(),panels:window._handle.get_debug_panels_state(),playing:window._handle.get_playing(window._handle.get_active_recording_id())})")
                            assert not sample["playing"], sample
                            assert set(sample["scene"]["timelines"]) == {"publish_time", "message_time"}, sample
                            samples.append(sample)
                        await page.mouse.click(130,973,delay=80)
                        await page.wait_for_function("()=>Number(window._handle.get_ad_scene_state().playhead_ns)>5000000000",timeout=20000)
                        sample = await page.evaluate("()=>({scene:window._handle.get_ad_scene_state(),panels:window._handle.get_debug_panels_state(),playing:window._handle.get_playing(window._handle.get_active_recording_id())})")
                        assert sample["playing"] and all(not p.get("error") for p in sample["panels"]),sample
                        samples.append(sample)
                        await page.mouse.click(130,973,delay=80)
                        await page.screenshot(path=str(out / f"{clock}.png"))
                    (out / "clock-checks.json").write_text(json.dumps(samples,indent=2))
                    (out / "query-clocks.json").write_text(json.dumps(query_clocks,indent=2))
                    assert set(query_clocks) == {"publish_time", "message_time"}, query_clocks
                    print("PASS: exactly publish_time/message_time; paused forward/back seeks and playback on both clocks",flush=True)
            (out/"state.json").write_text(json.dumps(state,indent=2))
            assert not errors, errors
            print("PASS: Sim editor opened and live queue responded",flush=True)
        finally:
            (out/"errors.json").write_text(json.dumps(errors,indent=2))
            (out/"console.json").write_text(json.dumps(console,indent=2))
            await page.screenshot(path=str(out/"final.png"))
            await browser.close()


asyncio.run(main())
