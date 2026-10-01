#!/usr/bin/env python3
"""Read-only real browser regression: historical task -> complete field evidence."""
import argparse
import asyncio
import json
import traceback
from pathlib import Path

from playwright.async_api import async_playwright
from sim_browser_helpers import open_sim_tasks, sim_click, sim_state


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--job', default='d3ade1846813436c')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width': 1440, 'height': 1000})
        await page.context.grant_permissions(['clipboard-read', 'clipboard-write'],
                                            origin=args.url.split('/?')[0])
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.route('https://tel.rerun.io/**', lambda r: r.fulfill(status=200, body='{}'))
        async def task():
            response = await page.request.post(args.url.split('/?')[0]+'/api/sim', data={'action': 'list'})
            assert response.ok
            return next(j for j in (await response.json())['jobs'] if j['id'] == args.job)
        async def wait_page(offset, complete=False):
            await page.wait_for_function('args => { const s = window._handle.get_simulation_state(); '
                'return !s.pending && s.differences?.offset === args[0] && '
                's.differences?.include_messages === args[1]; }', arg=[offset, complete], timeout=45000)
            state = await sim_state(page)
            assert not state['error'], state['error']
            assert json.loads(state['differences_json']) == state['differences']['diffs']
            diff = state['differences']['diffs'][0]
            assert diff['field_changes']
            checks.append(state['differences'])
            (args.out/f'difference-{offset}-complete-{complete}.json').write_text(
                json.dumps(state['differences']['diffs'], indent=2))
            await page.screenshot(path=str(args.out/f'difference-{offset}-complete-{complete}.png'))
            return diff
        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=60000)
            await page.wait_for_timeout(1500)
            original = await task()
            await open_sim_tasks(page, args.job)
            await sim_click(page, 'inspect_'+args.job)
            await page.wait_for_function('()=>window._handle.get_simulation_state().page === "detail"')
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            await sim_click(page, 'diff_load')
            first = await wait_page(0)
            assert first['stream_index'] == original['analysis']['comparisons'][0]['diffs'][0]['index']
            assert first['left']['topic'] == '/apollo/planning'
            assert first['left']['publish_time'] == '3600000000'
            assert first['field_changes'][0]['left_value'] == 1.0963480266956085e-10
            assert any(d['field'].startswith('trajectory_point[') for d in first['field_changes'])
            assert 'message' not in first['left']
            # Copy button position verified in the deployed 1440x1000 screenshot.
            await page.mouse.click(155, 574)
            await page.wait_for_timeout(350)
            copied = await page.evaluate('()=>navigator.clipboard.readText()')
            assert json.loads(copied) == [first], 'Clipboard JSON must retain every exact field value'
            (args.out/'copied-differences.json').write_text(copied)
            await sim_click(page, 'diff_next')
            second = await wait_page(1)
            assert second['stream_index'] > first['stream_index']
            await sim_click(page, 'diff_previous')
            assert (await wait_page(0)) == first
            await sim_click(page, 'diff_include_messages')
            await sim_click(page, 'diff_load')
            full = await wait_page(0, True)
            assert full['field_changes'] == first['field_changes']
            assert full['left']['message']['header']['timestamp_sec'] == 3.6
            # Jump beyond the legacy first-20 cutoff using the actual input.
            await page.mouse.dblclick(*(await sim_state(page))['diff_offset'])
            await page.keyboard.press('Control+a')
            await page.keyboard.insert_text('21')
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(350)
            await sim_click(page, 'diff_load')
            await wait_page(21, True)
            assert await task() == original, 'Inspecting evidence must never mutate historical tasks'
            assert not errors, errors
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            print('PASS: real task JSON fields, timestamps, next/previous, complete messages, beyond 20, unchanged task')
        except Exception:
            (args.out/'failure.txt').write_text(traceback.format_exc())
            raise
        finally:
            (args.out/'checks.json').write_text(json.dumps(checks, indent=2))
            (args.out/'errors.json').write_text(json.dumps(errors, indent=2))
            try:
                await page.screenshot(path=str(args.out/'final.png'))
            except Exception:
                (args.out/'screenshot-failure.txt').write_text(traceback.format_exc())
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
