#!/usr/bin/env python3
"""Exercise the real docked Topic picker with mouse/keyboard, not injected state."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from test_session_recovery_browser import READY, STATE
from sim_browser_helpers import open_sim_tasks


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--job', default='d897596d6b454fe1')
    parser.add_argument('--out', required=True)
    parser.add_argument('--staging-assets', type=Path, help='Predeployment only; omit for acceptance')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, requests, errors = [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width':1440, 'height':1000})
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.on('request', lambda r: requests.append(json.loads(r.post_data))
                if '/api/debug_query' in r.url and r.post_data else None)
        await page.route('https://tel.rerun.io/**', lambda r:r.fulfill(status=200,body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))

        async def panel():
            return await page.evaluate("()=>window._handle.get_debug_panels_state().find(p=>p.kind==='Topic inspector')")

        async def click(x, y):
            await page.mouse.click(x, y)
            await page.wait_for_timeout(450)

        async def text_at(point, value):
            await click(*point)
            await page.keyboard.press('Control+a')
            await page.keyboard.press('Backspace')
            if value:
                await page.keyboard.insert_text(value)
            await page.wait_for_timeout(600)

        async def capture(name):
            state = await page.evaluate(STATE)
            evidence.append({'name':name, **state})
            (out/'evidence.json').write_text(json.dumps(evidence, indent=2))
            await page.screenshot(path=str(out/f'{name}.png'))
            assert not state['panic']
            return state

        async def choose(topic):
            await click(*(await panel())['topic_ui']['picker'])
            await capture('opened-picker-' + topic.rsplit('/',1)[-1])
            await text_at((await panel())['topic_ui']['search'], topic)
            p = await panel()
            assert list(p['topic_ui']['rows']) == [topic], p
            await capture('filtered-' + topic.rsplit('/',1)[-1])
            await click(*p['topic_ui']['rows'][topic])
            await page.wait_for_function("topic=>window._handle.get_debug_panels_state().some(p=>p.kind==='Topic inspector'&&p.topic===topic&&p.has_data&&!p.pending&&!p.error&&p.fields.length>0)", arg=topic)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()', timeout=60000)
            await page.wait_for_timeout(1500)
            await click(35,340)
            await open_sim_tasks(page,args.job)
            key = f'replay_{args.job}_0'
            await page.wait_for_function('k=>window._handle.get_simulation_state()?.[k]', arg=key)
            sim = await page.evaluate('()=>window._handle.get_simulation_state()')
            await click(*sim[key])
            await page.wait_for_function(READY, timeout=90000)
            await click(37,189)
            await click(180,65)
            await click(37,189)
            await click(37,263)
            await click(167,113)
            await page.wait_for_function("()=>window._handle.get_debug_panels_state().some(p=>p.kind==='Topic inspector'&&p.has_data&&!p.pending)", timeout=30000)
            await page.wait_for_timeout(6500)  # Let informational layout toasts expire.
            p = await panel()
            state = await capture('initial-narrow-panel')
            response = await page.request.post(args.url.split('/?')[0]+'/api/mcap_topics', data=state['playback']['mcap'])
            catalog = await response.json()
            (out/'catalog.json').write_text(json.dumps(catalog, indent=2))
            names = catalog['topics']
            assert sorted(p['topic_ui']['available_topics']) == sorted(names)
            assert len(names) >= 18
            assert p['topic_ui']['picker_rect'][2] <= 1441  # egui fractional pixel rounding

            await text_at(p['topic_ui']['input'], '')
            boundary = len(requests)
            await page.wait_for_timeout(1500)
            p = await panel()
            assert p['topic']=='' and not p['has_data'] and not p['error']
            assert 'Select a topic' in p['topic_ui']['notice']
            assert not any(q.get('topic')=='' for q in requests[boundary:])
            await capture('empty-input-visible-picker')
            await click(*p['topic_ui']['picker'])
            await text_at((await panel())['topic_ui']['search'], 'no-such-filter')
            p = await panel()
            assert p['topic_ui'].get('rows', {}) == {}
            assert p['topic_ui']['available_topics'] == names
            await capture('no-search-matches-not-empty-catalog')
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(450)
            await choose('/apollo/localization/pose')
            p = await panel()
            assert any('pose.position.x' in str(f) for f in p['fields']), p
            await capture('pose-real-fields')

            await text_at(p['topic_ui']['input'], '/absent/topic')
            p = await panel()
            assert 'Topic not present in this bag' in p['error'] and not p['has_data']
            assert not any(q.get('topic')=='/absent/topic' for q in requests)
            await capture('invalid-topic-explicit-notice')
            await choose('/apollo/control')
            await capture('control-recovered')

            await page.set_viewport_size({'width':1100, 'height':900})
            await page.wait_for_timeout(1000)
            p = await panel()
            assert p['topic_ui']['picker_rect'][2] <= 1101, p
            await choose('/apollo/localization/pose')
            await page.evaluate("()=>{const h=window._handle;h.set_time_for_timeline(h.get_active_recording_id(),'publish_time',30000000000)}")
            await page.wait_for_function("()=>window._handle.get_debug_panels_state().some(p=>p.kind==='Topic inspector'&&p.at_ns==='30000000000'&&p.has_data&&!p.pending&&!p.error)", timeout=40000)
            state = await capture('narrow-paused-seek-pose')
            assert not state['playing']
            p = await panel()
            assert p['sample_ns'] == '30000000000'
            values = {f['path']:f['value'] for f in p['fields']}
            assert values['header.timestamp_sec'] == 30
            assert values['header.sequence_num'] > 0
            assert not errors, errors
            assert not any(q.get('topic')=='' for q in requests), requests
            print('PASS: real catalog, visible narrow picker, empty/invalid query suppression, mouse-filtered pose/control, paused seek', flush=True)
        finally:
            await capture('final')
            (out/'requests.json').write_text(json.dumps(requests, indent=2))
            (out/'errors.json').write_text(json.dumps(errors, indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
