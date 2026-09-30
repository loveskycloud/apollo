#!/usr/bin/env python3
"""Exercise the live SSE connection and inject explicit UI-only task/submit fixtures."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    requests, errors = [], []
    release_submit = asyncio.Event()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True,
            args=['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'])
        page = await browser.new_page(viewport={'width': 1440, 'height': 1100})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: requests.append((request.method, request.url, request.post_data))
                if '/api/sim' in request.url else None)
        await page.add_init_script('''
            window.simEventSources = [];
            const NativeEventSource = window.EventSource;
            window.EventSource = class extends NativeEventSource {
                constructor(...args) { super(...args); window.simEventSources.push(this); }
            };
        ''')
        await page.route('https://tel.rerun.io/**', lambda route: route.fulfill(status=200, body='{}'))

        async def submit_fixture(route):
            if route.request.post_data_json.get('action') in ('enqueue', 'enqueue_suite'):
                await release_submit.wait()
                await route.fulfill(status=200, content_type='application/json',
                    body=json.dumps({'status': 'error', 'message': 'Intentional submission test error'}))
            else:
                await route.continue_()

        await page.route('**/api/sim', submit_fixture)

        async def click(key):
            await page.wait_for_function('key => window._handle.get_simulation_state()?.[key]', arg=key)
            point = await page.evaluate('key => window._handle.get_simulation_state()[key]', key)
            await page.mouse.click(*point)
            await page.wait_for_timeout(150)

        async def push(stage, progress):
            await page.evaluate('''([stage, progress]) => {
                const state = window._handle.get_simulation_state();
                const job = {id:'sse-browser-fixture', stage, progress, config:state.draft_config,
                    outputs:[], history:[], error:null, analysis:null};
                window.simEventSources.at(-1).dispatchEvent(new MessageEvent('message', {
                    data:JSON.stringify({status:'ok', snapshot:false, jobs:[job]})
                }));
            }''', [stage, progress])
            await page.wait_for_timeout(200)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('() => window._handle && !window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(1500)  # The handle exists before the first painted rail.
            await page.mouse.click(35, 340)
            await page.wait_for_function('''() => {
                const s=window._handle.get_simulation_state();
                return s?.open && s.catalog_ready && !s.pending && s.jobs.length && !s.stream_error;
            }''', timeout=60000)
            draft = await page.evaluate('() => window._handle.get_simulation_state().draft_config')
            for _ in range(12):
                assert await page.evaluate('''() => {
                    const s=window._handle.get_simulation_state();
                    return s.start_label==='Start simulation' && s.start_enabled && !s.starting;
                }''')
                await page.wait_for_timeout(300)
            await page.screenshot(path=str(args.out / 'stable-start.png'))

            await push('queued', 0)
            await click('tasks_tab')
            await click('filter')
            await page.keyboard.insert_text('sse-browser-fixture')
            await page.keyboard.press('Tab')
            await click('inspect_sse-browser-fixture')
            await push('simulation_running', 0.75)
            assert await page.evaluate('''draft => {
                const s=window._handle.get_simulation_state();
                return s.page==='detail' && s.inspected_task.progress===0.75 &&
                    s.filter_text==='sse-browser-fixture' && JSON.stringify(s.draft_config)===JSON.stringify(draft);
            }''', draft)
            await click('config_tab')
            await click('enqueue')
            await page.wait_for_function('() => window._handle.get_simulation_state().starting')
            await push('completed', 1)
            assert await page.evaluate('''() => {
                const s=window._handle.get_simulation_state();
                return s.pending && s.starting && !s.start_enabled && s.start_label==='Starting…';
            }''')
            release_submit.set()
            await page.wait_for_function('() => window._handle.get_simulation_state().error === "Intentional submission test error"')
            await push('failed', 1)
            assert await page.evaluate('''() => {
                const s=window._handle.get_simulation_state();
                return !s.pending && !s.starting && s.start_enabled &&
                    s.start_label==='Start simulation' && s.error==='Intentional submission test error';
            }''')
            # A transport error must be visible, and the next valid event clears it.
            await page.evaluate("() => window.simEventSources.at(-1).dispatchEvent(new Event('error'))")
            await page.wait_for_function('() => window._handle.get_simulation_state().stream_error')
            await push('completed', 1)
            await page.wait_for_function('() => !window._handle.get_simulation_state().stream_error')
            await page.mouse.click(35, 340)
            await page.wait_for_function('() => window.simEventSources.at(-1).readyState===EventSource.CLOSED')
            await page.mouse.click(35, 340)
            await page.wait_for_function('''() => {
                const s=window._handle.get_simulation_state();
                return window.simEventSources.length===2 && !s.jobs.some(j=>j.id==='sse-browser-fixture') && !s.stream_error;
            }''', timeout=60000)
            actions = [json.loads(body)['action'] for method, url, body in requests if method == 'POST']
            assert actions.count('catalog') == 1, actions
            assert actions.count('enqueue') == 1, actions
            assert 'list' not in actions, actions
            assert not errors, errors
            await page.screenshot(path=str(args.out / 'reconnected.png'))
            print('PASS: live SSE snapshot/reconnect, no list polling, stable Start, delta detail/draft retention, isolated submit/error states')
        finally:
            release_submit.set()
            (args.out / 'network.json').write_text(json.dumps(requests, indent=2))
            (args.out / 'errors.json').write_text(json.dumps(errors, indent=2))
            await page.screenshot(path=str(args.out / 'final.png'))
            state = await page.evaluate('() => window._handle?.get_simulation_state()')
            (args.out / 'final-state.json').write_text(json.dumps(state, indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
