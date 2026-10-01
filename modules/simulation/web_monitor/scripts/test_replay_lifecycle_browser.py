#!/usr/bin/env python3
"""Real-mouse replay, first-click playback, run switching and persisted reload."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:9090/')
    parser.add_argument('--job', default='efcce7f9cf4c49c1')
    parser.add_argument('--out', required=True)
    parser.add_argument('--connection-checks', action='store_true',
                        help='Block the stream, retry via visible button, then remove its subscription before replay')
    parser.add_argument('--staging-assets', type=Path,
                        help='Explicit predeployment check of freshly built JS/Wasm; omit for deployed acceptance')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width':1440, 'height':1000})
        if args.staging_assets:
            async def wasm_asset(route):
                await route.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'), content_type='application/wasm')
            async def js_asset(route):
                await route.fulfill(path=str(args.staging_assets/'re_viewer.js'), content_type='text/javascript')
            await page.route('**/re_viewer_bg.wasm', wasm_asset)
            await page.route('**/re_viewer.js', js_asset)
        console, network, samples = [], [], []
        page.on('console', lambda msg: console.append({'type':msg.type, 'text':msg.text}))
        page.on('pageerror', lambda err: console.append({'type':'pageerror', 'text':str(err)}))
        page.on('requestfailed', lambda req: network.append({'url':req.url, 'failure':req.failure}))
        async def response(res):
            if '/api/' in res.url:
                network.append({'url':res.url, 'status':res.status,
                    'request':res.request.post_data, 'body':await res.text()})
        page.on('response', response)
        await page.route('https://tel.rerun.io/**', lambda route: route.fulfill(status=200, body='{}'))
        if args.connection_checks:
            await page.route('http://127.0.0.1:9876/**', lambda route: route.abort())
        async def snapshot(label):
            data = await page.evaluate('''()=>{
                const h=window._handle, out={url:location.href};
                if (!h) return {...out,body:document.body.innerText};
                for(const method of ['get_active_recording_id','get_ad_scene_state','get_playback_state',
                    'get_debug_panels_state','get_vehicle_dashboard_state','get_simulation_state']) {
                    try { out[method]=h[method](); } catch(e) {out[method]=String(e);}
                }
                out.playing=h.get_playing(h.get_active_recording_id());
                return out;
            }''')
            samples.append({'label':label, **data})
            (out/'samples.json').write_text(json.dumps(samples, indent=2))
            await page.screenshot(path=str(out/f'{label}.png'))
            return data
        try:
            await page.goto(args.url)
            for turn, run in enumerate([0, 1, 0, 0]):
                if turn == 3:
                    await page.reload()
                await page.wait_for_function('()=>window._handle && !window._handle.has_panicked()', timeout=60000)
                await page.wait_for_timeout(2000)
                if args.connection_checks and turn == 1:
                    await page.evaluate('()=>window._handle.remove_receiver(window.__web_monitor_proxy_url)')
                    await page.wait_for_timeout(500)
                    await snapshot('subscription-removed')
                await page.mouse.click(35, 340)
                key = f'replay_{args.job}_{run}'
                await page.wait_for_function('key=>window._handle.get_simulation_state()?.[key]', arg=key, timeout=30000)
                state = await page.evaluate('()=>window._handle.get_simulation_state()')
                await page.mouse.click(*state[key], delay=80)
                await page.wait_for_timeout(3000)
                await snapshot(f'{turn}-opened')
                if args.connection_checks and turn == 0:
                    await page.wait_for_function('()=>window._handle.get_playback_state()?.error', timeout=70000)
                    failure = await snapshot('connection-error')
                    assert failure['get_playback_state']['error']
                    await page.unroute('http://127.0.0.1:9876/**')
                    await page.mouse.click(*failure['get_playback_state']['retry'], delay=80)
                await page.wait_for_function('''()=>{try {
                    const h=window._handle,s=h.get_ad_scene_state(),p=h.get_debug_panels_state(),r=h.get_playback_state();
                    return s.ego_pose.count>0 && s.playhead_ns==='1000000000'
                        && r.clock_ready && !r.pending && !r.waiting_receipt && !r.error
                        && r.receivers.includes(r.proxy) && r.recording===h.get_active_recording_id()
                        && p.length>=5 && p.every(p=>p.has_data&&!p.error&&!p.pending);
                } catch {return false}}''', timeout=65000)
                before = await snapshot(f'{turn}-ready')
                assert before['playing'] is False
                await page.mouse.click(130,973,delay=80)
                previous_time = 1_000_000_000
                previous_pose = 1_000_000_000
                for i in range(4):
                    await page.wait_for_timeout(2000)
                    after = await snapshot(f'{turn}-play-{i}')
                    scene = after['get_ad_scene_state']
                    connection = after['get_playback_state']
                    assert connection['receivers'] and not connection['error'], after
                    assert connection['recording'] == after['get_active_recording_id'], after
                    assert after['playing'] and int(scene['playhead_ns'])>1_000_000_000, after
                    assert int(scene['playhead_ns']) > previous_time + 1_000_000_000, after
                    assert int(scene['ego_pose']['sample_ns']) > previous_pose, after
                    previous_time = int(scene['playhead_ns'])
                    previous_pose = int(scene['ego_pose']['sample_ns'])
                    age = int(scene['playhead_ns'])-int(scene['ego_pose']['sample_ns'])
                    assert 0<=age<100_000_000, after
                await page.mouse.click(130,973,delay=80)
                print(f'PASS: turn={turn}, run={run+1}, initial pose + first-click playback', flush=True)
        finally:
            await snapshot('final')
            (out/'console.json').write_text(json.dumps(console,indent=2))
            (out/'network.json').write_text(json.dumps(network,indent=2))
            await browser.close()


asyncio.run(main())
