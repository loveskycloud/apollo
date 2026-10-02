#!/usr/bin/env python3
"""Exercise styled debug panels with real MCAP queries and browser input."""
import argparse
import asyncio
import json
from pathlib import Path

from PIL import Image

from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--record', type=Path, help='Open this real local recording through the file picker')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    errors, evidence = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        context = await browser.new_context(viewport={'width': 1672, 'height': 940},
                                            permissions=['clipboard-read', 'clipboard-write'])
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.route('https://tel.rerun.io/**', lambda r: r.fulfill(status=200, body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r: r.fulfill(
                path=str(args.staging_assets / 're_viewer_bg.wasm'), content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r: r.fulfill(
                path=str(args.staging_assets / 're_viewer.js'), content_type='text/javascript'))

        async def panels():
            return await page.evaluate('()=>window._handle.get_debug_panels_state()')

        async def panel(kind):
            return next(p for p in await panels() if p['kind'] == kind)

        async def click(kind, control):
            await page.mouse.click(*(await panel(kind))['topic_ui']['controls'][control])
            await page.wait_for_timeout(350)

        async def header_click(kind, control):
            rect = (await panel(kind))['topic_ui']['header'][control]
            await page.mouse.click((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
            await page.wait_for_timeout(450)

        async def check_headers(screenshot):
            pixels = Image.open(screenshot).convert('RGB')
            for p in await panels():
                header = p['topic_ui']['header']
                bounds, more = header['header_rect'], header['more']
                assert more[0] >= bounds[0] and more[2] <= bounds[2] - 7, (p['kind'], header)
                for key in ('more', 'maximize'):
                    rect = header[key]
                    assert rect[1] >= bounds[1] - 1 and rect[3] <= bounds[3] + 1, (key, rect, bounds)
                center_y = (more[1] + more[3]) / 2
                assert abs(center_y - p['topic_ui']['controls']['settings'][1]) < 1
                # Each dots icon must contain three separated, centered bright groups.
                xs = []
                for x in range(round(more[0]) + 2, round(more[2]) - 2):
                    if any(min(pixels.getpixel((x, y))) > 160
                           for y in range(round(center_y) - 3, round(center_y) + 4)):
                        xs.append(x)
                groups = sum(i == 0 or x > xs[i - 1] + 1 for i, x in enumerate(xs))
                assert groups == 3, (p['kind'], xs)

        async def ready():
            await page.wait_for_function('''()=>{
                const ps=window._handle.get_debug_panels_state();
                return ps.length===3&&ps.every(p=>p.has_data&&!p.pending&&!p.error);
            }''', timeout=90000)

        async def scene():
            state = await page.evaluate('()=>window._handle.get_ad_scene_state()')
            return {k: v for k, v in state.items() if k != 'layout'}

        async def capture(name):
            await page.mouse.move(1600, 20)
            await page.wait_for_timeout(300)
            await page.screenshot(path=str(args.out / f'{name}.png'))
            evidence.append({'name': name, 'panels': await panels()})
            assert not errors, errors
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            print(name, flush=True)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(6000)
            if args.record:
                source_ready = '()=>!!window._handle.get_playback_state()?.source_ui?.controls?.["Choose a bag"]'
                if not await page.evaluate(source_ready):
                    await page.mouse.click(35, 115)
                await page.wait_for_function(source_ready, timeout=10000)
                await page.wait_for_timeout(600)
                rect = await page.evaluate('()=>window._handle.get_playback_state().source_ui.controls["Choose a bag"]')
                await page.mouse.click((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                await page.locator('#wm-local-bag-input').set_input_files(args.record)
                await page.wait_for_function('''()=>{
                    const h=window._handle,id=h.get_active_recording_id();
                    return id&&h.get_active_timeline(id)&&h.get_time_for_timeline(id,h.get_active_timeline(id))!=null;
                }''', timeout=90000)
                await page.wait_for_timeout(1500)
            await page.mouse.click(35, 190)
            await page.wait_for_function('()=>window._handle.get_layout_state()?.controls?.["layout_Planning layout"]', timeout=30000)
            layout = await page.evaluate('()=>window._handle.get_layout_state()')
            await page.mouse.click(*layout['controls']['layout_Planning layout'])
            await page.wait_for_function('()=>window._handle.get_layout_state().active==="Planning"')
            layout = await page.evaluate('()=>window._handle.get_layout_state()')
            await page.mouse.click(*layout['controls']['manage_panels'])
            await ready()
            # Close the existing Layers popover without changing any layer switches.
            await page.mouse.click(375, 92)
            # Seek into the moving part of this recording using its playback bar.
            await page.mouse.click(1000, 920)
            await page.wait_for_timeout(500)
            await ready()
            await page.wait_for_timeout(5000)
            await capture('01-planning-panels')
            await check_headers(args.out / '01-planning-panels.png')
            trajectory = await panel('Trajectory XY')
            assert trajectory['planned_count'] > 0 and trajectory['actual_count'] > 0
            before = await scene()
            await header_click('Topic inspector', 'more')
            await capture('01b-more-menu')
            menu = (await panel('Topic inspector'))['topic_ui']['header']
            assert 'hide' in menu and 'delete' in menu
            for key in ('hide', 'delete'):
                rect = menu[key]
                assert 0 <= rect[0] < rect[2] <= 1672 and 0 <= rect[1] < rect[3] <= 940
            await header_click('Topic inspector', 'hide')
            await page.wait_for_function('()=>window._handle.get_debug_panels_state().length===2')
            layout = await page.evaluate('()=>window._handle.get_layout_state()')
            message = next(v for v in layout['views'] if v['origin'] == '/debug/planning')
            assert not message['visible']
            await page.mouse.click(*layout['controls']['visible_' + message['id']])
            await ready()
            await header_click('Topic inspector', 'maximize')
            await page.wait_for_function('()=>window._handle.get_debug_panels_state().length===1')
            await capture('01c-maximized')
            await header_click('Topic inspector', 'maximize')
            await ready()
            assert await scene() == before, 'Header actions changed the 3D scene after restore'
            await click('Topic inspector', 'settings')
            await capture('02-panel-settings')
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(350)
            assert await scene() == before, 'Panel settings changed the 3D scene'
            await click('Topic inspector', 'copy')
            copied = json.loads(await page.evaluate('()=>navigator.clipboard.readText()'))
            fields = (await panel('Topic inspector'))['fields']
            timestamp = next(f['value'] for f in fields if f['path'] == 'header.timestamp_sec')
            assert copied['header']['timestamp_sec'] == timestamp
            # Expanded rows must preserve the full indexed field path.
            for _ in range(8):
                p = await panel('Topic inspector')
                if 'expand_trajectory_point' in p['topic_ui']['controls']:
                    break
                await page.mouse.move(1600, 790)
                await page.mouse.wheel(0, 200)
                await page.wait_for_timeout(250)
            await click('Topic inspector', 'expand_trajectory_point')
            assert 'trajectory_point[0]' in (await panel('Topic inspector'))['topic_ui']['displayed_rows']
            await click('Topic inspector', 'filter')
            await page.keyboard.insert_text('trajectory_point[0].v')
            await page.keyboard.press('Tab')
            await page.wait_for_timeout(400)
            p = await panel('Topic inspector')
            assert p['topic_ui']['displayed_rows'] == ['trajectory_point[0].v']
            await click('Topic inspector', 'pin_trajectory_point[0].v')
            await click('Topic inspector', 'watch')
            await ready()
            assert (await panel('Value watch'))['visible_fields'] == ['trajectory_point[0].v']
            assert await scene() == before, 'Inspecting fields changed the 3D scene'
            await capture('03-pinned-field')
            await page.set_viewport_size({'width': 1050, 'height': 760})
            await page.wait_for_timeout(600)
            await capture('04-narrow-panels')
            await check_headers(args.out / '04-narrow-panels.png')
            await header_click('Value watch', 'more')
            await capture('04b-narrow-menu')
            assert (await panel('Value watch'))['topic_ui']['header']['delete'][2] <= 1050
            await page.keyboard.press('Escape')
            for key in ('filter', 'watch', 'plot', 'copy'):
                x, y = (await panel('Value watch'))['topic_ui']['controls'][key]
                assert 0 < x < 1050 and 0 < y < 720, (key, x, y)
            await page.set_viewport_size({'width': 1672, 'height': 940})
            await page.wait_for_timeout(600)
            await click('Value watch', 'watch')
            await ready()
            old = await panel('Topic inspector')
            await click('Topic inspector', 'next')
            await page.wait_for_function('old=>window._handle.get_debug_panels_state().some(p=>p.kind==="Topic inspector"&&p.has_data&&!p.pending&&p.sample_ns!==old)', arg=old['sample_ns'], timeout=90000)
            assert (await panel('Topic inspector'))['sample_ns'] == old['next_ns']
            await click('Topic inspector', 'plot')
            await ready()
            p = await panel('Signal plot')
            assert len(p['series']) == 1 and p['series'][0]['count'] > 0
            assert p['series'][0]['field'] == 'trajectory_point[0].v'
            await capture('05-field-plot')
            print('PASS: centered unclipped three-dot icons, menus/hide/restore/maximize, real queries, XY data, settings, JSON copy, array expansion, deep filter, pin/watch/plot, message step, narrow controls, unchanged 3D during inspection', flush=True)
        finally:
            await page.screenshot(path=str(args.out / 'final.png'))
            (args.out / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            (args.out / 'last-panels.json').write_text(json.dumps(await panels(), ensure_ascii=False, indent=2))
            (args.out / 'errors.json').write_text(json.dumps(errors, indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
