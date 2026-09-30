#!/usr/bin/env python3
"""Task workspace interactions with explicit UI fixtures; never launch a simulation."""
import argparse
import asyncio
import copy
import json
from pathlib import Path

from playwright.async_api import async_playwright


def fixtures():
    config = dict(kind='world', source='/fixtures/scenario.worldsim.scenario.json',
        map='/fixtures/map', vehicle='/fixtures/vehicle', model='perfect_planning',
        modules=['fake_prediction', 'PLANNING', 'ROUTING'], repeat=1, seed=1, step_ms=10)
    def job(i, stage, suite, name, kind='world'):
        return dict(id=f'fixture-{i:04d}', name=name, stage=stage, suite_id=suite,
            suite_name='会车专项' if suite == 'meeting' else '轨迹与混合交通',
            config={**config, 'kind':kind, 'source':f'/fixtures/{name}.worldsim.scenario.json'},
            run=1 if stage != 'queued' else None, progress=68 if stage == 'simulation_running' else 100 if stage == 'completed' else 0,
            outputs=[], error='Fixture validation failure' if stage == 'failed' else None, analysis=None,
            history=[dict(stage='queued', wall_time=100), dict(stage='simulation_start', wall_time=110)] +
                ([dict(stage=stage, wall_time=866)] if stage in ('completed', 'failed', 'cancelled', 'interrupted') else []))
    older = [job(i, stage, 'traffic', f'混合交通场景 {i:02d}', 'bag' if i % 2 == 0 else 'world')
        for i, stage in enumerate(['simulation_running', 'queued', 'queued', 'interrupted', 'failed'] + ['completed'] * 11)]
    recent = [job(100 + i, stage, 'meeting', name) for i, (stage, name) in enumerate([
        ('simulation_running', '对向会车回归'), ('queued', '轨迹连续性校验'),
        ('failed', '场景结果分析'), ('failed', '混合交通回归'),
        ('completed', '对向会车回归（夜间）'), ('completed', '轨迹连续性校验（速度扰动）'),
        ('cancelled', '人工取消的场景'),
    ])]
    recent[2]['analysis'] = {'planning_continuity': {'status': 'FAIL'}}
    recent[3]['analysis'] = {'scenario_expectation': {'status': 'FAIL'}}
    return older + recent


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9091/?renderer=webgl&theme=dark')
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = fixtures()
    (args.out / 'ui-fixtures.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    errors, requests, evidence = [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True,
            args=['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'])
        page = await browser.new_page(viewport={'width':1440, 'height':1100})
        page.on('pageerror', lambda error:errors.append(str(error)))
        page.on('request', lambda request:requests.append({'url':request.url, 'body':request.post_data}) if '/api/sim' in request.url else None)
        await page.add_init_script('''window.simEventSources=[]; const ES=window.EventSource;
            window.EventSource=class extends ES { constructor(...args){super(...args);window.simEventSources.push(this);} };''')
        await page.route('https://tel.rerun.io/**', lambda route:route.fulfill(status=200, body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda route:route.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'), content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda route:route.fulfill(path=str(args.staging_assets/'re_viewer.js'), content_type='text/javascript'))
        async def command(route):
            if route.request.post_data_json.get('action') in ('enqueue','enqueue_suite','cancel'):
                assert route.request.post_data_json['action'] == 'cancel', 'Browser test must never submit a real task'
                await route.fulfill(status=200, content_type='application/json', body='{"status":"ok"}')
            else:
                await route.continue_()
        await page.route('**/api/sim', command)

        async def state():
            return await page.evaluate('() => window._handle.get_simulation_state()')

        async def click(key):
            await page.wait_for_function('key=>window._handle.get_simulation_state()?.[key]', arg=key)
            await page.mouse.click(*(await state())[key])
            await page.wait_for_timeout(250)

        async def push(jobs, snapshot=False):
            await page.evaluate('value=>window.simEventSources.at(-1).dispatchEvent(new MessageEvent("message",{data:JSON.stringify(value)}))',
                {'status':'ok', 'snapshot':snapshot, 'jobs':jobs})
            await page.wait_for_timeout(300)

        async def capture(name):
            await page.mouse.move(1200, 1080)
            await page.wait_for_timeout(200)
            value = await state()
            evidence.append({'name':name, 'state':value})
            await page.screenshot(path=str(args.out/f'{name}.png'))
            assert not errors, errors
            assert not await page.evaluate('() => window._handle.has_panicked()')
            if value['page'] == 'tasks':
                assert len(value['task_rows']) <= 10
                panel = value['panel_rect']
                for row in value['task_rows'].values():
                    assert row['rect'][0] >= panel[0] and row['rect'][2] <= panel[2]+1, (panel,row)
            return value

        async def search(text):
            await click('filter')
            await page.keyboard.press('Control+a')
            await page.keyboard.insert_text(text)
            if not text:
                await page.keyboard.press('Backspace')
            await page.keyboard.press('Tab')
            await page.wait_for_timeout(300)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('() => window._handle && !window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(1500)
            await page.mouse.click(35,340)
            await page.wait_for_function('() => window._handle.get_simulation_state()?.catalog_ready && !window._handle.get_simulation_state().pending')
            draft = (await state())['draft_config']
            await click('tasks_tab')
            await push(jobs, True)
            await page.wait_for_timeout(5000)  # Let normal startup notifications expire.
            s = await capture('01-task-workspace')
            assert s['task_counts'] == dict(all=23, running=2, queued=3, failed=3, completed=13, cancelled=1, interrupted=1), s['task_counts']
            assert s['task_pagination'] == dict(page=1,pages=3,page_size=10,total=23)
            assert s['task_rows']['fixture-0100']['subtitle'] == '68%'
            assert s['task_rows']['fixture-0101']['queue_position'] == 3

            await click('suite_suite:meeting')
            assert not any(k.startswith('fixture-01') for k in (await state())['task_rows'])
            await click('suite_suite:meeting')
            await click('status_chip_failed')
            s = await capture('02-failed-tasks')
            assert s['task_pagination']['total'] == 3
            assert all(row['stage']=='failed' for row in s['task_rows'].values())
            await click('source_filter')
            await capture('03-source-menu')
            await click('source_option_bag')
            assert (await state())['task_pagination']['total'] == 1
            await click('status_filter')
            await capture('04-status-menu')
            await click('status_option_cancelled')
            assert (await state())['task_pagination']['total'] == 0
            await capture('05-empty-filter')
            await click('clear_task_filters')

            await click('tasks_next_page')
            s = await capture('06-page-two')
            assert s['task_pagination']['page']==2 and len(s['task_rows'])==10
            updated = copy.deepcopy(jobs[6]); updated['progress']=42
            await push([updated])
            assert (await state())['task_pagination']['page']==2
            await click('tasks_page_3')
            assert len((await state())['task_rows'])==3
            await search('fixture-0101')
            s = await state()
            assert s['task_pagination']['page']==1 and s['task_pagination']['total']==1
            await click('task_menu_fixture-0101')
            await capture('07-task-actions-menu')
            await click('cancel_fixture-0101')
            await page.wait_for_function('() => !window._handle.get_simulation_state().pending')
            changed = copy.deepcopy(jobs[-6]); changed['stage']='cancelled'
            await push([changed])
            assert (await state())['task_rows']['fixture-0101']['status_label']=='已取消'

            await search('fixture-0102')
            await click('inspect_fixture-0102')
            assert (await state())['page']=='detail'
            assert (await state())['draft_config']==draft
            await click('view_config_fixture-0102')
            s = await capture('08-reuse-config')
            assert s['page']=='config' and s['config_from']=='fixture-0102'
            assert s['draft_config']==jobs[-5]['config']
            await click('tasks_tab')
            await click('new_task')
            s = await state()
            assert s['page']=='config' and s['config_from'] is None and s['draft_config']['source']==''
            await click('tasks_tab')
            await search('')
            await page.set_viewport_size({'width':900,'height':850})
            await page.wait_for_timeout(600)
            await capture('09-narrow-workspace')
            await click('source_filter')
            await capture('10-narrow-menu')
            await page.keyboard.press('Escape')
            actions = [json.loads(r['body'])['action'] for r in requests if r['body']]
            assert 'list' not in actions, actions
            assert actions.count('cancel')==1, actions
            assert not errors, errors
            print('PASS: task counts, suite collapse, status/source/search filters, pagination, push retention, cancel, detail/reuse/new task, narrow layout; no list polling')
        finally:
            await page.screenshot(path=str(args.out/'final.png'))
            (args.out/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
            (args.out/'final-state.json').write_text(json.dumps(await state(),ensure_ascii=False,indent=2))
            (args.out/'network.json').write_text(json.dumps(requests,indent=2))
            (args.out/'errors.json').write_text(json.dumps(errors,indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
