#!/usr/bin/env python3
"""Exercise configuration controls and intercepted submissions; never enqueue jobs."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright
from sim_browser_helpers import assert_menu_hover, sim_click, sim_state


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--staging-assets', type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    submitted, errors, evidence = [], [], []
    catalog = {'worlds': ['/fixtures/会车.scenario.json'], 'suites': ['/fixtures/会车场景集合.json'],
               'bags': ['/fixtures/test.record'], 'maps': ['/fixtures/测试地图'], 'vehicles': ['/fixtures/测试车辆']}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.on('pageerror', lambda e: errors.append(str(e)))
        await page.route('https://tel.rerun.io/**', lambda r: r.fulfill(status=200, body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r: r.fulfill(
                path=str(args.staging_assets / 're_viewer_bg.wasm'), content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r: r.fulfill(
                path=str(args.staging_assets / 're_viewer.js'), content_type='text/javascript'))

        async def command(route):
            request = route.request.post_data_json
            if request['action'] == 'catalog':
                result = {'status': 'ok', 'catalog': catalog}
            elif request['action'] in ('enqueue', 'enqueue_suite'):
                submitted.append(request)
                result = {'status': 'ok'}
            elif request['action'] == 'list':
                result = {'status': 'ok', 'jobs': []}
            else:
                raise AssertionError(request)
            await route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
        await page.route('**/api/sim', command)

        async def capture(name):
            await page.mouse.move(1300, 100)
            await page.wait_for_timeout(300)
            state = await sim_state(page)
            evidence.append({'name': name, 'state': state})
            await page.screenshot(path=str(args.out / f'{name}.png'))
            assert not errors, errors
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            return state

        async def choose_source(expected):
            await sim_click(page, 'Scenario')
            x, y = (await sim_state(page))['Scenario']
            await assert_menu_hover(page, [(x, y + 76)], args.out,
                'suite-choice' if expected in catalog['suites'] else 'scene-choice', sample_offset=80, sample_y_offset=0)
            await page.mouse.click(x, y + 76)
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(300)
            config = (await sim_state(page))['draft_config']
            assert expected in (config['source'], config.get('suite')), config

        async def submit(action, repeat):
            await sim_click(page, 'enqueue')
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            request = submitted[-1]
            assert request['action'] == action, request
            assert request['config']['repeat'] == repeat, request
            assert request['config']['step_ms'] == 10, request

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(1500)
            await page.mouse.click(35, 340)
            await page.wait_for_function('()=>window._handle.get_simulation_state()?.catalog_ready')
            await sim_click(page, 'world')
            s = await capture('01-default-world')
            assert s['draft_config']['repeat'] == 1 and s['draft_config']['step_ms'] == 10
            assert 'ego_model' in s and 'ML_PLANNING' in s
            assert all(k not in s for k in ('planner', 'step_ms', 'runs', 'seed'))
            for key, field, values in [('Map', 'map', catalog['maps']), ('Vehicle', 'vehicle', catalog['vehicles'])]:
                await sim_click(page, key)
                x, y = (await sim_state(page))[key]
                point = (x, y + 108)
                await assert_menu_hover(page, [point], args.out, key.lower() + '-choice', sample_offset=30, sample_y_offset=0)
                await page.mouse.click(*point)
                await page.wait_for_timeout(300)
                assert (await sim_state(page))['draft_config'][field] == values[0]
                await sim_click(page, key)
                await assert_menu_hover(page, [(x, y + 76)], args.out, key.lower() + '-follow-scene', sample_offset=30, sample_y_offset=0)
                await page.keyboard.press('Escape')
                assert (await sim_state(page))['draft_config'][field] == values[0]
            await sim_click(page, 'ego_model')
            x, y = (await sim_state(page))['ego_model']
            await assert_menu_hover(page, [(x, y + 80)], args.out, 'ego-model-choice', sample_offset=80, sample_y_offset=0)
            await page.keyboard.press('Escape')
            assert (await sim_state(page))['draft_config']['model'] == 'perfect_planning'
            await choose_source(catalog['worlds'][0])
            await capture('02-scene-selected')
            await sim_click(page, 'ML_PLANNING')
            modules = (await sim_state(page))['draft_config']['modules']
            assert 'ML_PLANNING' in modules and 'PLANNING' not in modules
            await submit('enqueue', 1)
            await sim_click(page, 'PLANNING')
            modules = (await sim_state(page))['draft_config']['modules']
            assert 'PLANNING' in modules and 'ML_PLANNING' not in modules
            await sim_click(page, 'PREDICTION')
            assert 'fake_prediction' not in (await sim_state(page))['draft_config']['modules']
            await sim_click(page, 'fake_prediction')
            assert 'PREDICTION' not in (await sim_state(page))['draft_config']['modules']
            await sim_click(page, 'determinism')
            assert (await sim_state(page))['draft_config']['repeat'] == 2
            await sim_click(page, 'runs_plus')
            await submit('enqueue', 3)
            await capture('03-determinism-enabled')
            await sim_click(page, 'determinism')
            await sim_click(page, 'source_type')
            await sim_click(page, 'suite_mode')
            await choose_source(catalog['suites'][0])
            await submit('enqueue_suite', 1)
            await capture('04-suite-selected')
            await sim_click(page, 'source_type')
            await sim_click(page, 'single_mode')
            assert 'suite' not in (await sim_state(page))['draft_config']
            await page.set_viewport_size({'width': 900, 'height': 850})
            await page.wait_for_timeout(500)
            s = await capture('05-narrow')
            rect = s['panel_rect']
            for key in ('source_type', 'Scenario', 'ML_PLANNING'):
                assert rect[0] <= s[key][0] <= rect[2], (key, s[key], rect)
            await page.mouse.move((rect[0] + rect[2]) / 2, 600)
            await page.mouse.wheel(0, 650)
            await page.wait_for_timeout(400)
            s = await sim_state(page)
            assert rect[0] <= s['ego_model'][0] <= rect[2], s
            await sim_click(page, 'determinism')
            assert (await sim_state(page))['draft_config']['repeat'] == 2
            await capture('06-narrow-scrolled')
            print('PASS: default 1×10ms, both module exclusions, scene/suite selection, opt-in 2/3 runs, intercepted payloads, narrow layout')
        finally:
            await page.screenshot(path=str(args.out / 'final.png'))
            (args.out / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            (args.out / 'submitted.json').write_text(json.dumps(submitted, ensure_ascii=False, indent=2))
            (args.out / 'errors.json').write_text(json.dumps(errors, indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
