#!/usr/bin/env python3
"""Regression: reload/new-tab recovery without a second Replay click.

UI opening/layout/panel actions use real mouse input. Timeline seeking uses the
viewer's public time API; diagnostics only observe rendered/query state.
"""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright
from sim_browser_helpers import open_sim_tasks

STATE = """()=>{const h=window._handle,diagnostic_errors={};
    const read=(key,f)=>{try{return f()}catch(e){diagnostic_errors[key]=String(e);return null}};
    return {diagnostic_errors,
    playback:read('playback',()=>h.get_playback_state()),scene:read('scene',()=>h.get_ad_scene_state()),
    panels:read('panels',()=>h.get_debug_panels_state()),hud:read('hud',()=>h.get_vehicle_dashboard_state()),
    layers:read('layers',()=>h.get_display_layers_state()),playing:read('playing',()=>h.get_playing(h.get_active_recording_id())),
    bookmark:JSON.parse(sessionStorage.getItem('wm_playback_bookmark_v1')),
    panic:h.has_panicked()}}"""
READY = """()=>{const h=window._handle,p=h?.get_playback_state();return p?.clock_ready
    &&!p.pending&&!p.waiting_receipt&&!p.error&&p.source&&p.recording===h.get_active_recording_id()
    &&h.get_ad_scene_state().ego_pose.count>0} """


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--job', default='efcce7f9cf4c49c1')
    parser.add_argument('--out', required=True)
    parser.add_argument('--staging-assets', type=Path, help='Predeployment check only; omit for acceptance')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, errors, requests, console = [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])

        async def new_page():
            page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('console', lambda m: console.append({'type':m.type,'text':m.text}))
            page.on('request', lambda r: requests.append({'url': r.url, 'body': r.post_data})
                    if '/api/' in r.url else None)
            await page.route('https://tel.rerun.io/**', lambda r: r.fulfill(status=200, body='{}'))
            if args.staging_assets:
                await page.route('**/re_viewer_bg.wasm', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
                await page.route('**/re_viewer.js', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))
            return page

        async def capture(page, name):
            state = await page.evaluate(STATE)
            if state['playback'] and state['playback']['clock_ready']:
                assert not state['diagnostic_errors'], state['diagnostic_errors']
            evidence.append({'name': name, **state})
            (out/'evidence.json').write_text(json.dumps(evidence, indent=2))
            await page.screenshot(path=str(out/f'{name}.png'))
            return state

        async def click(page, x, y):
            await page.mouse.click(x, y)
            await page.wait_for_timeout(450)

        async def replay(page, run):
            await click(page, 35, 340)
            await open_sim_tasks(page, args.job)
            key = f'replay_{args.job}_{run}'
            await page.wait_for_function('key=>window._handle.get_simulation_state()?.[key]', arg=key)
            sim = await page.evaluate('()=>window._handle.get_simulation_state()')
            await click(page, *sim[key])
            await page.wait_for_function(READY, timeout=90000)
            await page.wait_for_function('()=>window._handle.get_debug_panels_state().length>=5&&window._handle.get_debug_panels_state().every(p=>p.has_data&&!p.pending&&!p.error)', timeout=30000)

        async def seek(page, ns, clock='message_time'):
            await page.evaluate('''({ns,clock})=>{const h=window._handle;
                h.set_time_for_timeline(h.get_active_recording_id(),clock,ns)}''', {'ns':ns,'clock':clock})
            await page.wait_for_function('''({ns,clock})=>{const h=window._handle,s=h.get_ad_scene_state(),p=h.get_playback_state();
                return s.playhead_ns===String(ns)&&s.clock===clock&&s.ego_pose.sample_ns===String(ns)
                    &&p.clock_ready&&!p.pending&&!p.waiting_receipt&&!p.error}''', arg={'ns':ns,'clock':clock}, timeout=40000)
            await page.wait_for_function('ns=>JSON.parse(sessionStorage.getItem("wm_playback_bookmark_v1"))?.time_ns===ns', arg=ns)

        async def inspector(page):
            # Perception, followed by + Topic inspector in the Panel drawer.
            await click(page, 37, 189)
            await click(page, 180, 65)
            await click(page, 37, 189)
            await click(page, 37, 263)
            await page.screenshot(path=str(out/'panel-drawer-before-add.png'))
            await click(page, 167, 113)
            await page.wait_for_function('''()=>window._handle.get_debug_panels_state().some(p=>
                p.kind==='Topic inspector'&&p.has_data&&!p.pending&&!p.error&&p.visible_fields?.length>0)''', timeout=40000)

        page = await new_page()
        extra = None
        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=60000)
            await page.wait_for_timeout(1500)
            await replay(page, 0)
            first = await capture(page, 'opened-run1')
            path1 = first['playback']['mcap']
            recording1 = first['playback']['recording']
            # Hide one static layer and seek into an uncached window while paused.
            await click(page, *first['layers']['nodes']['map/lane_centerlines'])
            await seek(page, 30_000_000_000)
            before = await capture(page, 'before-reload')
            assert not before['playing']
            assert before['bookmark']['layers']['/hdmap/lane_centerlines'] is False
            request_boundary = len(requests)
            await page.reload(wait_until='domcontentloaded')  # No Replay or session-storage mutation.
            await page.wait_for_function(READY, timeout=90000)
            await page.wait_for_function('()=>window._handle.get_ad_scene_state().playhead_ns==="30000000000"', timeout=30000)
            await capture(page, 'reload-ready-before-add-panel')
            await inspector(page)
            await page.wait_for_timeout(6000)  # Let informational toasts leave the screenshot.
            restored = await capture(page, 'reloaded-topic-inspector')
            assert restored['playback']['mcap'] == path1
            assert restored['playback']['recording'] == recording1
            assert restored['scene']['clock'] == 'message_time'
            assert restored['scene']['playhead_ns'] == '30000000000'
            assert not restored['playing']
            assert restored['bookmark']['layers']['/hdmap/lane_centerlines'] is False
            assert restored['hud']['data']['streams']['chassis']['sample']
            assert not any('/api/convert_record' in r['url'] for r in requests[request_boundary:])
            panel = next(p for p in restored['panels'] if p['kind']=='Topic inspector')
            assert panel['at_ns']=='30000000000' and panel['fields']
            print('PASS: plain reload restores source, exact cursor/clock/layers, paused Inspector and chassis HUD', flush=True)
            await click(page, 37, 263)
            await seek(page, 58_000_000_000)
            await page.wait_for_function('()=>window._handle.get_debug_panels_state().every(p=>p.has_data&&!p.pending&&p.at_ns==="58000000000")', timeout=30000)
            await capture(page, 'paused-inspector-seek')
            # Independent context: no inherited localStorage/sessionStorage.
            extra = await new_page()
            await extra.goto(args.url, wait_until='domcontentloaded')
            await extra.wait_for_function(READY, timeout=90000)
            await inspector(extra)
            fresh = await capture(extra, 'new-tab-source-descriptor')
            assert fresh['playback']['mcap'] == path1 and not fresh['playing']
            assert fresh['playback']['recording'] == recording1
            assert fresh['panels'][0]['fields']
            print('PASS: fresh browser tab recovers from recording metadata without Replay', flush=True)
            await click(extra, 37, 263)
            await replay(extra, 1)
            second = await capture(extra, 'second-tab-run2')
            path2 = second['playback']['mcap']
            assert path1 != path2
            await seek(page, 15_000_000_000, 'publish_time')
            await seek(extra, 42_000_000_000)
            a = await capture(page, 'tab1-independent-run1')
            b = await capture(extra, 'tab2-independent-run2')
            assert a['playback']['mcap']==path1 and b['playback']['mcap']==path2
            assert a['playback']['recording']==recording1
            assert a['playback']['recording'] != b['playback']['recording']
            await replay(page, 1)
            await seek(page, 25_000_000_000)
            await page.reload(wait_until='domcontentloaded')
            await page.wait_for_function(READY, timeout=90000)
            final = await capture(page, 'switched-run2-reload')
            assert final['playback']['mcap']==path2 and final['scene']['playhead_ns']=='25000000000'
            assert not final['playing']
            await click(page, 130, 973)
            await page.wait_for_function('()=>Number(window._handle.get_ad_scene_state().playhead_ns)>29000000000', timeout=20000)
            playing = await capture(page, 'recovered-first-click-playing')
            assert playing['playing']
            assert int(playing['scene']['ego_pose']['sample_ns'])>28_000_000_000
            await click(page, 130, 973)
            # Simulate a file replacement at the metadata boundary, without
            # modifying the user's bag. It must fail explicitly before queries.
            async def replaced_source(route):
                response = await route.fetch()
                body = await response.json()
                body['source']['modified_ns'] = str(int(body['source']['modified_ns'])+1)
                await route.fulfill(response=response, json=body)
            await page.route('**/api/mcap_topics', replaced_source)
            await page.reload(wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle?.get_playback_state()?.error?.includes("changed on disk")', timeout=60000)
            failed = await capture(page, 'changed-source-rejected')
            assert not failed['playback']['clock_ready']
            await page.unroute('**/api/mcap_topics', replaced_source)
            await click(page, *failed['playback']['retry'])
            await page.wait_for_function(READY, timeout=90000)
            await inspector(page)
            await capture(page, 'verified-source-retry')
            assert not errors, errors
            print('PASS: independent bags, switched-source reload, first-click play, explicit identity rejection and verified retry', flush=True)
        finally:
            (out/'requests.json').write_text(json.dumps(requests, indent=2))
            (out/'errors.json').write_text(json.dumps(errors, indent=2))
            (out/'console.json').write_text(json.dumps(console, indent=2))
            if page and not page.is_closed():
                await capture(page, 'final')
            if extra and not extra.is_closed():
                await capture(extra, 'final-second-tab')
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
