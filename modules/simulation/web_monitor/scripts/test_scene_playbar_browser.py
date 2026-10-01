#!/usr/bin/env python3
"""Real mouse acceptance of scene-editor-style transport, including exact seek."""
import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path

from playwright.async_api import async_playwright
from test_session_recovery_browser import READY, STATE
from sim_browser_helpers import open_sim_tasks


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--reference', default='http://127.0.0.1:5173/')
    parser.add_argument('--job', default='d897596d6b454fe1')
    parser.add_argument('--out', required=True)
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--source-hud', action='store_true', help='Verify Source controls and dashboard speed units')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, errors, requests, console, failed_requests = [], [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.on('console', lambda m:console.append({'type':m.type,'text':m.text}))
        page.on('requestfailed', lambda r:failed_requests.append({'url':r.url,'error':r.failure}))
        page.on('request', lambda r:requests.append(json.loads(r.post_data))
            if '/api/debug_query' in r.url and r.post_data else None)
        await page.route('https://tel.rerun.io/**', lambda r:r.fulfill(status=200,body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))

        async def state():
            return await page.evaluate(STATE)

        async def click(x,y):
            await page.mouse.click(x,y)
            await page.wait_for_timeout(450)

        async def button(name):
            rect = (await state())['playback']['media_bar'][name]
            await click((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)

        async def capture(name):
            s = await state()
            widgets = s['playback']['media_bar']
            timestamp = widgets['seek_input']
            assert widgets['slider'][2] <= widgets['time'][0]
            assert widgets['time'][2] <= timestamp[0]
            assert timestamp[2] - timestamp[0] < 176
            assert abs(timestamp[2] - widgets['bar'][2]) <= 2
            evidence.append({'name':name,**s})
            (out/'evidence.json').write_text(json.dumps(evidence,indent=2))
            await page.screenshot(path=str(out/f'{name}.png'))
            rect = s['playback']['media_bar']['bar']
            size = page.viewport_size
            await page.screenshot(path=str(out/f'{name}-bar.png'),clip={
                'x':max(0,rect[0]-12),'y':rect[1]-8,
                'width':min(size['width'],rect[2]+12)-max(0,rect[0]-12),
                'height':size['height']-(rect[1]-8)})
            assert not s['panic']
            return s

        async def settled(ns=None):
            await page.wait_for_function(READY, timeout=60000)
            await page.wait_for_function("""ns=>{const h=window._handle,s=h.get_ad_scene_state(),p=h.get_debug_panels_state();
                const age=Number(s.playhead_ns)-Number(s.ego_pose.sample_ns);
                return (!ns||s.playhead_ns===String(ns))&&age>=0&&age<=10000000
                    &&p.length>=5&&p.every(x=>x.has_data&&!x.pending&&!x.error)
                    &&p.every(x=>Math.abs(Number(x.at_ns)-Number(s.playhead_ns))<2000000000)}""", arg=ns,timeout=40000)
            s = await state()
            assert not s['playing']
            return s

        async def edit(value):
            await button('seek_input')
            await page.keyboard.press('Control+a')
            await page.keyboard.insert_text(value)
            await page.wait_for_timeout(450)

        async def slider(fraction,drag=False):
            rect = (await state())['playback']['media_bar']['slider']
            x = rect[0]+6+fraction*(rect[2]-rect[0]-12)
            y = (rect[1]+rect[3])/2
            if drag:
                await page.mouse.move(rect[0]+6,y)
                await page.mouse.down()
                await page.mouse.move(x,y,steps=12)
                await page.mouse.up()
                await page.wait_for_timeout(450)
            else:
                await click(x,y)
            await settled()

        try:
            ref = await browser.new_page(viewport={'width':1440,'height':1000})
            await ref.goto(args.reference,wait_until='domcontentloaded')
            await ref.locator('.playback-row').wait_for()
            await ref.locator('.playback-row').screenshot(path=str(out/'scene-editor-reference-bar.png'))
            await ref.screenshot(path=str(out/'scene-editor-reference.png'))
            await ref.close()
            await page.goto(args.url,wait_until='domcontentloaded')
            if args.staging_assets:
                # Keep the real HTTP document's network origin for the local gRPC proxy.
                styles=(args.staging_assets/'index.html').read_text().split('<style>',1)[1].split('</style>',1)[0]
                await page.add_style_tag(content=styles)
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()',timeout=60000)
            await page.wait_for_timeout(1500)
            await click(35,340)
            await open_sim_tasks(page,args.job)
            key=f'replay_{args.job}_0'
            await page.wait_for_function('k=>window._handle.get_simulation_state()?.[k]',arg=key)
            sim=await page.evaluate('()=>window._handle.get_simulation_state()')
            await click(*sim[key])
            await settled()
            await page.wait_for_timeout(6500)
            await capture('initial-paused')
            if args.source_hud:
                from PIL import Image
                with Image.open(out/'initial-paused-bar.png') as bar_image:
                    colors=bar_image.convert('RGB').getdata()
                    assert sum(c==(159,122,234) for c in colors)>5
                    assert not any(c==(59,130,246) for c in colors)
            assert not {'stop','settings','end','seek_apply'} & (await state())['playback']['media_bar'].keys()
            await settled(1_000_000_000)
            await button('forward')
            await settled(1_010_000_000)
            await button('backward')
            await settled(1_000_000_000)
            await capture('step-forward-back')
            await slider(.5)
            s=await capture('paused-slider-middle')
            assert abs(int(s['scene']['playhead_ns'])-31_000_000_000)<80_000_000
            await slider(.8,drag=True)
            s=await capture('paused-drag-forward')
            assert abs(int(s['scene']['playhead_ns'])-49_000_000_000)<80_000_000
            before=(await state())['scene']['playhead_ns']
            await edit('30.050000001')
            assert (await state())['scene']['playhead_ns']==before
            await page.keyboard.press('Enter')
            await settled(30_050_000_001)
            await page.wait_for_timeout(450)
            assert (await state())['playback']['media_timestamp']['text']=='30.050000001'
            await capture('exact-decimal-seek')
            await edit('58.0')
            await button('time')  # Blur commits an edited absolute timestamp.
            await settled(58_000_000_000)
            await capture('blur-commits-timestamp')
            await edit('99999')
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(450)
            assert 'outside' in (await state())['playback']['media_timestamp']['error']
            assert (await state())['scene']['playhead_ns']=='58000000000'
            await capture('outside-bag-rejected')
            await edit('bad-time')
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(450)
            assert 'Invalid' in (await state())['playback']['media_timestamp']['error']
            assert (await state())['scene']['playhead_ns']=='58000000000'
            await capture('invalid-time-rejected')
            await button('seek_input')
            await capture('refocus-invalid')
            await page.wait_for_function('()=>window._handle.get_playback_state().media_timestamp.editing',timeout=10000)
            await page.keyboard.press('Escape')
            await page.wait_for_function('()=>!window._handle.get_playback_state().media_timestamp.error',timeout=10000)
            assert not (await state())['playback']['media_timestamp']['error']
            await button('clock')
            await capture('clock-dropdown')
            anchor=(await state())['scene']['playhead_ns']
            await button('message_time')
            await settled(int(anchor))
            assert (await state())['scene']['clock']=='message_time'
            await capture('clock-preserves-time')
            await button('clock')
            await button('publish_time')
            await settled(int(anchor))
            await button('clock')
            await button('message_time')
            await settled(int(anchor))
            await edit('4.00')
            await page.keyboard.press('Enter')
            await settled(4_000_000_000)
            await button('step')
            await capture('step-dropdown')
            await button('step_1000')
            await page.wait_for_timeout(450)
            await button('forward')
            await settled(5_000_000_000)
            if args.source_hud:
                await click(109,40) # Close the Layers popover covering the narrow HUD.
                for expected_unit in ['m/s','km/h']:
                    s=await state()
                    view=s['hud']['views'][0]
                    rect=view['speed']['rect']
                    await click((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)
                    await page.wait_for_function('unit=>window._handle.get_vehicle_dashboard_state().views[0].speed.unit===unit',arg=expected_unit)
                    s=await capture('dashboard-'+expected_unit.replace('/','-'))
                    shown=s['hud']['views'][0]['speed']
                    raw=s['hud']['data']['streams']['chassis']['sample']['speed_mps']
                    assert raw is not None and raw!=0
                    assert abs(float(shown['text'])-raw*(3.6 if expected_unit=='km/h' else 1))<=0.051
                    assert s['scene']['playhead_ns']=='5000000000' and not s['playing']
            await button('backward')
            await settled(4_000_000_000)
            await button('speed')
            await button('speed_2')
            await button('play')
            await page.wait_for_function('()=>Number(window._handle.get_ad_scene_state().playhead_ns)>7000000000',timeout=30000)
            s=await capture('playing-2x-message-time')
            assert s['playing']
            assert s['scene']['playback_speed']==2
            stamp=int(Decimal(s['playback']['media_timestamp']['text'])*1_000_000_000)
            assert abs(stamp-int(s['scene']['playhead_ns']))<250_000_000
            await edit('4.125000001')
            await page.wait_for_timeout(750)
            s=await capture('playing-does-not-overwrite-edit')
            assert s['playback']['media_timestamp']['text']=='4.125000001'
            assert s['playing'] and int(s['scene']['playhead_ns'])>7_000_000_000
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(450)
            assert (await state())['playing']
            await button('play')
            await settled()
            await capture('paused-without-reset')
            assert int((await state())['scene']['playhead_ns'])>7_000_000_000
            await page.set_viewport_size({'width':900,'height':700})
            await page.wait_for_timeout(1000)
            s=await capture('narrow-900')
            positions=s['playback']['media_bar']
            for name in ['backward','play','forward','speed','clock','step','slider','seek_input','time']:
                r=positions[name]
                assert 0<=r[0]<r[2]<=901 and 0<=r[1]<r[3]<=701,(name,r)
            await slider(.2)
            await capture('narrow-paused-seek')
            await click(37,263)
            await page.wait_for_timeout(750)
            s=await capture('drawer-open-two-rows')
            positions=s['playback']['media_bar']
            assert positions['clock'][1] < positions['slider'][1]-10
            for r in positions.values():
                assert r[2]<=901,r
            assert not errors,errors
            assert set(q['clock'] for q in requests)=={'publish_time','message_time'}
            assert not {'stop','settings','end','seek_apply'} & positions.keys()
            if args.source_hud:
                await click(37,263) # Close the Panel drawer.
                await page.set_viewport_size({'width':1440,'height':1000})
                await page.wait_for_timeout(750)
                await click(37,113)

                async def source_button(name):
                    r=(await state())['playback']['source_ui']['controls'][name]
                    await click((r[0]+r[2])/2,(r[1]+r[3])/2)

                await capture('source-local-dropdown')
                for choice in ['From scenario ID','Trip segment','From local data']:
                    await source_button('mode')
                    await capture('source-mode-menu-'+choice.split()[-1])
                    await source_button(choice)
                    await capture('source-mode-'+choice.split()[-1])
                s=await state()
                fields={p['label'] for p in s['playback']['source_ui']['properties']}
                assert not fields & {'Current recording','Application','Recording','Started'}
                assert {'Start time','End time','Local bag'} <= fields
                assert 'Open Path' in s['playback']['source_ui']['controls']
                # Open the actual Apollo record through the single path action.
                expected_mcap=s['playback']['mcap']
                jobs=await (await page.request.post(args.url.split('/?')[0]+'/api/sim',data={'action':'list'})).json()
                target=next(j for j in jobs['jobs'] if j['id']==args.job)['outputs'][0]
                await source_button('path')
                await page.keyboard.press('Control+a')
                await page.keyboard.insert_text(target)
                await page.wait_for_timeout(450)
                await source_button('Open Path')
                await settled()
                await page.wait_for_timeout(6500)
                await capture('source-open-path-loaded')
                await page.mouse.move(250,660)
                await page.mouse.wheel(0,450)
                await page.wait_for_timeout(600)
                await capture('source-clean-properties')
                s=await state()
                assert s['playback']['mcap']==expected_mcap
                fields={p['label']:p['value'] for p in s['playback']['source_ui']['properties']}
                assert fields['Start time']!=fields['End time']
            print('PASS: flat clock/step dropdowns, live absolute timestamp, Enter/blur/Escape/validation, nanosecond seek, playback and responsive rows',flush=True)
        finally:
            (out/'console.json').write_text(json.dumps(console,indent=2))
            (out/'failed-requests.json').write_text(json.dumps(failed_requests,indent=2))
            (out/'errors.json').write_text(json.dumps(errors,indent=2))
            (out/'requests.json').write_text(json.dumps(requests,indent=2))
            await page.screenshot(path=str(out/'final.png'))
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
