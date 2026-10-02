#!/usr/bin/env python3
"""Verify nested inspector topic settings with real MCAP data and browser input."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--staging-assets', type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    evidence, requests, errors = [], [], []
    target_id = None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=[
            '--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width': 1672, 'height': 940})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('request', lambda r: requests.append(json.loads(r.post_data))
                if '/api/debug_query' in r.url and r.post_data else None)
        await page.route('https://tel.rerun.io/**', lambda r: r.fulfill(status=200, body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r: r.fulfill(
                path=str(args.staging_assets / 're_viewer_bg.wasm'), content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r: r.fulfill(
                path=str(args.staging_assets / 're_viewer.js'), content_type='text/javascript'))

        async def panels():
            return await page.evaluate('()=>window._handle.get_debug_panels_state()')

        async def panel():
            return next(p for p in await panels() if p['id'] == target_id)

        async def click(point):
            await page.mouse.click(*point)
            await page.wait_for_timeout(400)

        async def fill(point, value):
            await click(point)
            await page.keyboard.press('Control+a')
            await page.keyboard.press('Backspace')
            if value:
                await page.keyboard.insert_text(value)
            await page.wait_for_timeout(400)

        async def layout_click(key):
            layout = await page.evaluate('()=>window._handle.get_layout_state()')
            await click(layout['controls'][key])

        async def settings():
            if 'picker' not in (await panel())['topic_ui']:
                await click((await panel())['topic_ui']['controls']['settings'])
            assert 'picker' in (await panel())['topic_ui'], 'Settings must remain open'

        async def picker():
            await settings()
            await click((await panel())['topic_ui']['picker'])
            await page.wait_for_timeout(400)
            assert 'search' in (await panel())['topic_ui'], 'Opening the topic list closed its parent settings'

        async def choose(topic, expected_field):
            await picker()
            await fill((await panel())['topic_ui']['search'], topic)
            p = await panel()
            assert topic in p['topic_ui']['rows'], p
            await click(p['topic_ui']['rows'][topic])
            await page.wait_for_function('''arg=>window._handle.get_debug_panels_state().some(p=>
                p.id===arg.id&&p.topic===arg.topic&&p.has_data&&!p.pending&&!p.error&&
                p.fields?.some(f=>f.path===arg.field))''',
                arg={'id': target_id, 'topic': topic, 'field': expected_field}, timeout=90000)
            assert 'picker' in (await panel())['topic_ui'], 'Selecting a topic must preserve its settings container'
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(350)
            assert 'picker' not in (await panel())['topic_ui']

        async def paste(value):
            await settings()
            if 'input' not in (await panel())['topic_ui']:
                await click((await panel())['topic_ui']['controls']['paste_topic'])
            await fill((await panel())['topic_ui']['input'], value)

        async def capture(name):
            await page.mouse.move(800, 20)
            await page.wait_for_timeout(250)
            await page.screenshot(path=str(args.out / f'{name}.png'))
            evidence.append({'name': name, 'panels': await panels()})
            assert not errors, errors
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            print(name, flush=True)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(6000)
            source_ready = '()=>!!window._handle.get_playback_state()?.source_ui?.controls?.["Choose a bag"]'
            if not await page.evaluate(source_ready):
                await click((35, 115))
            await page.wait_for_function(source_ready, timeout=10000)
            await page.wait_for_timeout(600)
            rect = await page.evaluate('()=>window._handle.get_playback_state().source_ui.controls["Choose a bag"]')
            await click(((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2))
            await page.locator('#wm-local-bag-input').set_input_files(args.record)
            await page.wait_for_function('''()=>{
                const h=window._handle,id=h.get_active_recording_id();
                return id&&h.get_active_timeline(id)&&h.get_time_for_timeline(id,h.get_active_timeline(id))!=null;
            }''', timeout=90000)
            await page.wait_for_timeout(1000)
            await click((35, 190))
            await page.wait_for_function('()=>window._handle.get_layout_state()?.controls?.["layout_Planning layout"]')
            await layout_click('layout_Planning layout')
            await layout_click('manage_panels')
            await page.wait_for_function('()=>window._handle.get_debug_panels_state().some(p=>p.kind==="Topic inspector"&&p.has_data&&!p.pending)', timeout=90000)
            # Layout/import notifications overlay the right-hand panel toolbar.
            await page.wait_for_timeout(6000)
            target_id = next(p['id'] for p in await panels() if p['kind'] == 'Topic inspector')
            original_id = target_id
            await choose('/apollo/canbus/chassis', 'speed_mps')
            await capture('01-chassis-selected')
            await choose('/apollo/localization/pose', 'pose.position.x')
            await capture('02-pose-selected')
            await choose('/apollo/planning', 'trajectory_point[0].v')

            # Add a separate inspector and verify per-panel settings are isolated.
            layout = await page.evaluate('()=>window._handle.get_layout_state()')
            await fill(layout['controls']['panel_search'], '消息检查器')
            await layout_click('add_inspector')
            await page.wait_for_function('()=>window._handle.get_debug_panels_state().filter(p=>p.kind==="Topic inspector").length===2')
            target_id = next(p['id'] for p in await panels() if p['kind'] == 'Topic inspector' and p['id'] != original_id)
            await page.wait_for_timeout(500)
            await choose('/apollo/canbus/chassis', 'speed_mps')
            await capture('03-second-inspector')
            assert next(p for p in await panels() if p['id'] == original_id)['topic'] == '/apollo/planning'

            names = (await panel())['topic_ui']['available_topics']
            await picker()
            await fill((await panel())['topic_ui']['search'], 'no-such-filter')
            assert (await panel())['topic_ui'].get('rows', {}) == {}
            assert (await panel())['topic_ui']['available_topics'] == names
            await capture('04-empty-search')
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(400)
            await paste('')
            p = await panel()
            assert p['topic'] == '' and not p['has_data'] and not p['error'], p
            assert 'Select a topic' in p['topic_ui']['notice']
            await paste('/absent/topic')
            p = await panel()
            assert 'Topic not present in this bag' in p['error'] and not p['has_data']
            await capture('05-explicit-invalid-topic')
            await choose('/apollo/localization/pose', 'pose.position.x')
            await page.set_viewport_size({'width': 1050, 'height': 760})
            await page.wait_for_timeout(600)
            await choose('/apollo/canbus/chassis', 'speed_mps')
            await capture('06-narrow-topic-switch')
            await settings()
            assert (await panel())['topic_ui']['picker_rect'][2] <= 1051
            # Clicking within the settings editor must not dismiss it.
            await paste('/apollo/planning')
            await page.wait_for_function('id=>window._handle.get_debug_panels_state().some(p=>p.id===id&&p.topic==="/apollo/planning"&&p.has_data&&!p.pending&&!p.error)', arg=target_id, timeout=90000)
            assert 'input' in (await panel())['topic_ui']
            await capture('07-pasted-topic')
            invalid = [q for q in requests if q.get('mode') == 'snapshot' and q.get('topic') in ('', '/absent/topic')]
            assert not invalid, invalid
            assert all(any(q.get('mode') == 'snapshot' and q.get('topic') == t for q in requests)
                       for t in ('/apollo/planning', '/apollo/canbus/chassis', '/apollo/localization/pose'))
            print('PASS: nested topic search, repeated planning/chassis/pose switching, real refreshed fields, independent inspectors, empty/invalid inputs, paste editor and narrow viewport', flush=True)
        finally:
            await page.screenshot(path=str(args.out / 'final.png'))
            (args.out / 'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            (args.out / 'last-panels.json').write_text(json.dumps(await panels(), ensure_ascii=False, indent=2))
            (args.out / 'requests.json').write_text(json.dumps(requests, indent=2))
            (args.out / 'errors.json').write_text(json.dumps(errors, indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
