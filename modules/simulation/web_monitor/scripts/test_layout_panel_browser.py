#!/usr/bin/env python3
"""Verify the layout/panel drawers against real blueprints in an isolated browser."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    evidence, errors = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width': 1672, 'height': 1000}, accept_downloads=True)
        page.on('pageerror', lambda e: errors.append(str(e)))
        await page.route('https://tel.rerun.io/**', lambda r: r.fulfill(status=200, body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r: r.fulfill(path=str(args.staging_assets / 're_viewer_bg.wasm'), content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r: r.fulfill(path=str(args.staging_assets / 're_viewer.js'), content_type='text/javascript'))

        async def state():
            return await page.evaluate('()=>window._handle.get_layout_state()')

        async def click(key):
            for _ in range(5):
                s = await state()
                if key in s.get('controls', {}):
                    await page.mouse.click(*s['controls'][key])
                    await page.wait_for_timeout(500)
                    return
                await page.mouse.move(200, 650)
                await page.mouse.wheel(0, 220)
                await page.wait_for_timeout(250)
            raise AssertionError((key, await state()))

        async def capture(name):
            await page.mouse.move(1600, 40)
            await page.wait_for_timeout(300)
            s = await state()
            evidence.append({'name': name, 'state': s})
            await page.screenshot(path=str(args.out / f'{name}.png'))
            assert not errors, errors
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            print(name, len(s['views']), flush=True)
            return s

        async def search(value):
            await click('panel_search')
            await page.keyboard.press('Control+a')
            await page.keyboard.press('Backspace')
            if value:
                await page.keyboard.insert_text(value)
            await page.keyboard.press('Tab')
            await page.wait_for_timeout(400)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(6000)
            await page.mouse.click(35, 190)
            await page.wait_for_function('()=>window._handle.get_layout_state()?.layout_open')
            await capture('01-layout-library')
            await click('layout_Planning layout')
            await page.wait_for_function('()=>{const s=window._handle.get_layout_state();return s.active==="Planning"&&s.views.length===4}')
            await click('pin_Planning layout')
            assert (await state())['default'] == 'Planning'
            await click('manage_panels')
            s = await capture('02-panel-library')
            assert s['panel_open'] and not s['layout_open']
            original = {v['id']: v for v in s['views']}
            scene = next(v for v in s['views'] if not v['origin'].startswith('/debug/'))
            await search('值监视')
            s = await capture('03-search')
            assert 'add_watch' in s['controls'] and 'add_profile' not in s['controls']
            await click('add_watch')
            await page.wait_for_function('()=>window._handle.get_layout_state().views.length===5')
            watch = next(v for v in (await state())['views'] if v['id'] not in original)
            assert watch['origin'] == '/debug/watch'
            await click('visible_' + watch['id'])
            await page.wait_for_function('id=>window._handle.get_layout_state().views.find(v=>v.id===id).visible===false', arg=watch['id'])
            await capture('04-hidden-panel')
            await click('visible_' + watch['id'])
            assert next(v for v in (await state())['views'] if v['id'] == watch['id'])['visible']
            await click('menu_' + watch['id'])
            await click('remove_' + watch['id'])
            await page.wait_for_function('()=>window._handle.get_layout_state().views.length===4')
            # Existing 3D view identity, origin and visibility are not edited.
            assert next(v for v in (await state())['views'] if v['id'] == scene['id']) == scene
            await search('规划消息')
            s = await state()
            profile = next(v for v in s['views'] if v['origin'] == '/debug/profile')
            message = next(v for v in s['views'] if v['origin'] == '/debug/planning')
            source = s['controls']['drag_' + message['id']]
            target = s['controls']['drag_' + profile['id']]
            assert source[1] > target[1]
            await page.mouse.move(*source)
            await page.mouse.down()
            await page.mouse.move(*target, steps=12)
            await page.mouse.up()
            await page.wait_for_timeout(600)
            s = await state()
            assert s['controls']['drag_' + message['id']][1] < s['controls']['drag_' + profile['id']][1]
            assert next(v for v in s['views'] if v['id'] == scene['id']) == scene
            await search('')
            await page.mouse.move(200, 400)
            await page.mouse.wheel(0, -2000)
            await page.wait_for_timeout(300)
            await click('save_layout')
            s = await capture('05-saved-custom')
            assert s['active'] == s['default'] == 'Custom'
            await click('export_layout')
            async with page.expect_download(timeout=20000) as downloaded:
                await page.get_by_role('link', name='click here to download your file').click()
            download = await downloaded.value
            await download.save_as(args.out / 'verified-layout.rbl')
            assert (args.out / 'verified-layout.rbl').stat().st_size > 100
            await page.get_by_role('link', name='click here to download your file').wait_for(state='hidden')
            await page.wait_for_timeout(400)
            await click('layout_selector')
            await click('select_Control layout')
            await page.wait_for_function('()=>window._handle.get_layout_state().active==="Control"')
            await click('layout_selector')
            await click('select_Custom layout')
            await page.wait_for_function('()=>{const s=window._handle.get_layout_state();return s.active==="Custom"&&s.views.length===4}')
            assert sorted(v['origin'] for v in (await state())['views']) == sorted(v['origin'] for v in original.values())
            await page.set_viewport_size({'width': 1050, 'height': 760})
            await page.wait_for_timeout(500)
            await capture('06-narrow')
            s = await state()
            for key in ('save_layout', 'export_layout'):
                assert s['controls'][key][1] < 760
            await page.mouse.click(35, 340)
            await page.wait_for_function('()=>window._handle.get_simulation_state()?.catalog_ready')
            simulation = await page.evaluate('()=>window._handle.get_simulation_state()')
            assert simulation['draft_config']['repeat'] == 1
            assert simulation['draft_config']['step_ms'] == 10
            assert 'layout_selector' not in (await state())['controls']
            print('PASS: presets, pin, search/add, visibility, removal, drag reorder, custom save/restore, .rbl export, narrow fixed footer; original 3D view unchanged', flush=True)
        finally:
            await page.screenshot(path=str(args.out / 'final.png'))
            (args.out / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            (args.out / 'errors.json').write_text(json.dumps(errors, indent=2))
            (args.out / 'last-state.json').write_text(json.dumps(await state(), ensure_ascii=False, indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
