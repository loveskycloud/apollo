#!/usr/bin/env python3
"""Task workspace interactions with explicit UI fixtures; never launch a simulation."""
import argparse
import asyncio
import copy
import json
from pathlib import Path

from playwright.async_api import async_playwright
from sim_browser_helpers import assert_menu_hover


def fixtures():
    config = dict(kind='world', source='/fixtures/scenario.worldsim.scenario.json',
        map='/fixtures/map', vehicle='/fixtures/vehicle', model='perfect_planning',
        modules=['fake_prediction', 'PLANNING', 'ROUTING'], repeat=1, seed=1, step_ms=10)
    def job(i, stage, suite, name, kind='world'):
        return dict(id=f'fixture-{i:04d}', name=name, stage=stage, suite_id=suite,
            created_at=200 if suite == 'meeting' else 100, suite_index=i,
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
    recent[4]['outputs'] = ['/fixtures/run-1.record', '/fixtures/run-2.record']
    recent[4]['run'] = recent[4]['config']['repeat'] = 2
    recent[2]['analysis'] = {'planning_continuity': {'status': 'FAIL'}}
    recent[3]['analysis'] = {'scenario_expectation': {'status': 'FAIL'}}
    return older + recent


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--live-task', help='Read-only verification of an existing completed task and its second recording')
    parser.add_argument('--pagination-regression', action='store_true', help='Read-only pagination checks against existing task suites')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    jobs = fixtures()
    batch_jobs = copy.deepcopy(jobs)
    (args.out / 'ui-fixtures.json').write_text(json.dumps(jobs, ensure_ascii=False, indent=2))
    errors, requests, evidence, replays = [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True,
            args=['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server'])
        page = await browser.new_page(viewport={'width':1672, 'height':1040})
        page.on('pageerror', lambda error:errors.append(str(error)))
        page.on('request', lambda r: replays.append(r.post_data_json) if '/api/convert_record' in r.url and r.method=='POST' else None)
        page.on('request', lambda request:requests.append({'url':request.url, 'body':request.post_data}) if '/api/sim' in request.url else None)
        await page.add_init_script('''window.simEventSources=[]; const ES=window.EventSource;
            window.EventSource=class extends ES { constructor(...args){super(...args);window.simEventSources.push(this);} };''')
        await page.route('https://tel.rerun.io/**', lambda route:route.fulfill(status=200, body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda route:route.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'), content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda route:route.fulfill(path=str(args.staging_assets/'re_viewer.js'), content_type='text/javascript'))
        async def command(route):
            nonlocal batch_jobs
            payload = route.request.post_data_json
            action = payload.get('action')
            mutations = {'enqueue', 'enqueue_suite', 'cancel', 'delete', 'cancel_suite', 'retry_suite', 'delete_suite'}
            if action not in mutations:
                await route.continue_()
                return
            assert not (args.live_task or args.pagination_regression), 'Read-only verification must never mutate tasks'
            assert action not in ('enqueue', 'enqueue_suite'), 'Browser test must never submit a real task'
            reply = {'status':'ok'}
            if action.endswith('_suite'):
                assert payload == {'action':action, 'suite_id':'meeting'}, payload
                members = [j for j in batch_jobs if j['suite_id']==payload['suite_id']]
                assert len(members)==7
                if action=='cancel_suite':
                    for job in members:
                        if job['stage'] not in ('completed', 'failed', 'cancelled', 'interrupted'):
                            job['stage']='cancelled'
                elif action=='retry_suite':
                    replacements=copy.deepcopy(members)
                    for i,job in enumerate(replacements):
                        job.update(id=f'fixture-retry-{i}', suite_id='meeting-retry',
                                   stage='queued', created_at=300, outputs=[], progress=0)
                    batch_jobs += replacements
                    reply.update(id=replacements[0]['id'], ids=[j['id'] for j in replacements], suite_id='meeting-retry')
                else:
                    reply['deleted_ids']=[j['id'] for j in members]
                    batch_jobs=[j for j in batch_jobs if j['suite_id']!=payload['suite_id']]
                reply['jobs']=batch_jobs
            await route.fulfill(status=200, content_type='application/json', body=json.dumps(reply))
        await page.route('**/api/sim', command)
        async def replay_request(route):
            await route.fulfill(status=200, content_type='application/json',
                body=json.dumps({'status':'error','message':'Intentional fixture replay: no real record opened'}))
        if not args.live_task:
            await page.route('**/api/convert_record', replay_request)

        def same_rect(a,b):
            return all(abs(x-y)<0.5 for x,y in zip(a,b))

        async def state():
            return await page.evaluate('() => window._handle.get_simulation_state()')

        async def click(key):
            if key.startswith('suite_tasks_'):
                for _ in range(8):
                    current = await state()
                    bounds = current['task_list_rect']
                    target = current.get(key)
                    if target and bounds[1] + 20 <= target[1] <= bounds[3] - 20:
                        break
                    await page.mouse.move((bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2)
                    await page.mouse.wheel(0, target[1] - (bounds[1]+bounds[3])/2 if target else 500)
                    await page.wait_for_timeout(250)
                else:
                    raise AssertionError(f'Pagination control outside visible list: {key}')
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
                assert len(value['task_rows']) <= 10 * value['task_pagination']['page_size']
                for suite in value['task_suites']:
                    ids = {j['id'] for j in value['jobs'] if 'suite:' + str(j.get('suite_id')) == suite['key']}
                    assert len(ids.intersection(value['task_rows'])) <= 10
                panel = value['panel_rect']
                for row in value['task_rows'].values():
                    assert row['rect'][0] >= panel[0] and row['rect'][2] <= panel[2]+1, (panel,row)
                for suite in value['task_suites']:
                    ids={j['id'] for j in value['jobs'] if 'suite:'+str(j.get('suite_id'))==suite['key']}
                    for job_id,row in value['task_rows'].items():
                        if job_id in ids:
                            assert row['rect'][1]>=suite['rect'][3]-.5, (suite,row)
                assert 'new_task' not in value
                for bounds in value.get('task_popups', {}).values():
                    assert bounds[0] >= panel[0]-1 and bounds[2] <= panel[2]+1, (panel, bounds)
                assert value['task_footer_rect'][3] <= panel[3]+1
                for row in value['task_rows'].values():
                    assert row['rect'][3]-row['rect'][1] <= 111, row
                    if not args.live_task:
                        assert '会车专项' not in row['metadata'] and 'WorldSim' not in row['metadata']
            a,b=value['config_tab_rect'],value['tasks_tab_rect']
            assert abs((a[2]-a[0])-(b[2]-b[0])) < 1, (a,b)
            assert a[1]==b[1] and a[3]==b[3] and abs(a[2]-b[0])<1, (a,b)
            print(name, flush=True)
            return value

        async def search(text):
            await click('filter')
            await page.keyboard.press('Control+a')
            await page.keyboard.insert_text(text)
            if not text:
                await page.keyboard.press('Backspace')
            await page.keyboard.press('Tab')
            await page.wait_for_timeout(300)

        async def replay_hover(task_id, prefix):
            points = [(await state())[f'replay_{task_id}_{index}'] for index in range(2)]
            await assert_menu_hover(page, points, args.out, prefix)

        try:
            await page.goto(args.url, wait_until='domcontentloaded')
            await page.wait_for_function('() => window._handle && !window._handle.has_panicked()', timeout=90000)
            await page.wait_for_timeout(6000)
            await page.mouse.click(35,190)
            await page.wait_for_function('()=>window._handle.get_layout_state()?.controls?.["layout_Planning layout"]')
            layout = await page.evaluate('()=>window._handle.get_layout_state()')
            await page.mouse.click(*layout['controls']['layout_Planning layout'])
            await page.wait_for_timeout(500)
            await page.mouse.click(35,340)
            await page.wait_for_function('() => window._handle.get_simulation_state()?.catalog_ready && !window._handle.get_simulation_state().pending')
            draft = (await state())['draft_config']
            config = await capture('00-config-tabs')
            views = (await page.evaluate('()=>window._handle.get_layout_state()'))['views']
            assert len(views)==4, views
            await click('tasks_tab')
            if args.pagination_regression:
                await page.wait_for_function('()=>window._handle.get_simulation_state()?.jobs?.length > 0')
                await page.wait_for_timeout(5000)
                actual = await capture('pagination-01-all-suites')
                total = len(actual['jobs'])
                suites = actual['task_suites']
                assert 2 <= len(suites) <= 10, suites
                assert actual['task_pagination']['total'] == total
                assert actual['task_pagination']['pages'] == 1
                first, second = suites[:2]
                first_ids = {j['id'] for j in actual['jobs'] if 'suite:' + str(j.get('suite_id')) == first['key']}
                second_ids = {j['id'] for j in actual['jobs'] if 'suite:' + str(j.get('suite_id')) == second['key']}
                assert len(first_ids) > 10 and len(second_ids) > 10
                assert first_ids.intersection(actual['task_rows']) and second_ids.intersection(actual['task_rows'])
                await click('suite_' + first['key'])
                collapsed = await capture('pagination-02-first-suite-collapsed')
                assert not first_ids.intersection(collapsed['task_rows'])
                assert len(second_ids.intersection(collapsed['task_rows'])) == 10
                assert collapsed['task_pagination']['total'] == total and collapsed['task_pagination']['pages'] == 1
                await click('suite_tasks_' + second['key'] + '_next_page')
                second_page = await capture('pagination-03-second-suite-page-two')
                assert second_page['suite_pagination'][second['key']]['page'] == 2
                assert len(second_ids.intersection(second_page['task_rows'])) == min(10, len(second_ids)-10)
                # Return to the top before expanding the first suite.
                bounds = second_page['task_list_rect']
                await page.mouse.move((bounds[0]+bounds[2])/2, (bounds[1]+bounds[3])/2)
                await page.mouse.wheel(0, -10000)
                await page.wait_for_timeout(300)
                await click('suite_' + first['key'])
                last_page = actual['suite_pagination'][first['key']]['pages']
                await click('suite_tasks_' + first['key'] + '_page_' + str(last_page))
                last = await capture('pagination-04-independent-last-pages')
                assert last['suite_pagination'][first['key']]['page'] == last_page
                assert last['suite_pagination'][second['key']]['page'] == 2
                assert len(first_ids.intersection(last['task_rows'])) == (len(first_ids)-1)%10+1
                assert second_ids.intersection(last['task_rows'])
                await search(next(iter(second_ids)))
                filtered = await capture('pagination-05-filter-resets-pages')
                assert filtered['task_pagination']['total'] == 1 and len(filtered['task_rows']) == 1
                assert filtered['suite_pagination'][second['key']]['page'] == 1
                await search('')
                actions = [json.loads(r['body'])['action'] for r in requests if r['body']]
                assert not set(actions)&{'list','enqueue','enqueue_suite','cancel','delete','cancel_suite','retry_suite','delete_suite'}, actions
                print(f'PASS: {total} existing tasks; all suites, collapse, independent pagination and filter reset; no task mutations', flush=True)
                return
            if args.live_task:
                await page.wait_for_function('id=>window._handle.get_simulation_state()?.jobs?.some(j=>j.id===id)', arg=args.live_task)
                await page.wait_for_timeout(5000)
                actual=await capture('live-01-task-list')
                assert same_rect(actual['panel_rect'],config['panel_rect'])
                assert (await page.evaluate('()=>window._handle.get_layout_state()'))['views']==views
                target=next(j for j in actual['jobs'] if j['id']==args.live_task)
                assert target['stage']=='completed' and len(target['outputs'])>=2
                await search(args.live_task)
                if target.get('suite_id'):
                    suite_id=target['suite_id']
                    await click(f'suite_menu_{suite_id}')
                    menu=await capture('live-02-suite-menu')
                    members=[j for j in actual['jobs'] if j.get('suite_id')==suite_id]
                    unfinished=any(j['stage'] not in ('completed','failed','cancelled','interrupted') for j in members)
                    running=any(j['stage'] not in ('queued','completed','failed','cancelled','interrupted') for j in members)
                    assert menu[f'cancel_suite_{suite_id}_enabled']==unfinished
                    assert menu[f'retry_suite_{suite_id}_enabled']==(not unfinished)
                    assert menu[f'delete_suite_{suite_id}_enabled']==(not running)
                    await page.keyboard.press('Escape')
                    await page.wait_for_function('()=>Object.keys(window._handle.get_simulation_state().task_popups).length===0')
                await click(f'replay_menu_{args.live_task}')
                await capture('live-02-replay-menu')
                await replay_hover(args.live_task, 'live-replay-hover')
                old_mcap=await page.evaluate('()=>window._handle.get_playback_state().mcap')
                await click(f'replay_{args.live_task}_1')
                await page.wait_for_function('old=>window._handle.get_playback_state()?.mcap&&window._handle.get_playback_state().mcap!==old', arg=old_mcap, timeout=120000)
                await page.wait_for_function('()=>window._handle.get_debug_panels_state().length===3&&window._handle.get_debug_panels_state().every(p=>p.has_data&&!p.error&&!p.pending)', timeout=120000)
                assert replays[-1]['path']==target['outputs'][1], replays
                assert (await state())['open'] and (await state())['page']=='tasks'
                assert len((await page.evaluate('()=>window._handle.get_layout_state()'))['views'])==4
                await page.wait_for_timeout(5000)
                await capture('live-03-second-run-playing')
                await click('config_tab')
                assert same_rect((await state())['panel_rect'],actual['panel_rect'])
                await click('tasks_tab')
                assert same_rect((await state())['panel_rect'],actual['panel_rect'])
                await search('')
                await capture('live-04-grouped-list-and-panels')
                actions=[json.loads(r['body'])['action'] for r in requests if r['body']]
                assert not set(actions)&{'list','enqueue','enqueue_suite','cancel','delete','cancel_suite','retry_suite','delete_suite'}, actions
                print('PASS: actual task groups, unchanged tab/sidebar widths and four views, second-run recording loaded with three real debug queries; no task mutations', flush=True)
                return
            await push(jobs, True)
            await page.wait_for_timeout(5000)  # Let normal startup notifications expire.
            s = await capture('01-task-workspace')
            assert same_rect(s['panel_rect'],config['panel_rect']), (s['panel_rect'],config['panel_rect'])
            assert same_rect(s['config_tab_rect'],config['config_tab_rect']) and same_rect(s['tasks_tab_rect'],config['tasks_tab_rect'])
            assert (await page.evaluate('()=>window._handle.get_layout_state()'))['views']==views
            footer=s['task_footer_rect']; filters=s['filter']
            bounds=s['task_list_rect']
            await page.mouse.move((bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2)
            await page.mouse.wheel(0,450)
            await page.wait_for_timeout(400)
            scrolled=await capture('01-scrolled-list')
            assert scrolled['task_footer_rect']==footer and scrolled['filter']==filters
            await page.mouse.move((bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2)
            await page.mouse.wheel(0,-2000)
            await page.wait_for_timeout(400)
            assert s['task_counts'] == dict(all=23, running=2, queued=3, failed=3, completed=13, cancelled=1, interrupted=1), s['task_counts']
            assert s['task_pagination'] == dict(page=1,pages=1,page_size=10,total=23,group_total=2,unit='groups')
            assert s['task_rows']['fixture-0100']['subtitle'] == '68%'
            assert s['task_rows']['fixture-0101']['queue_position'] == 3

            # Filter down to one member: the suite menu still acts on all seven.
            await search('fixture-0102')
            await click('suite_menu_meeting')
            active_menu=await capture('suite-01-running-menu')
            await assert_menu_hover(page, [active_menu['cancel_suite_meeting']], args.out, 'suite-cancel-hover')
            assert active_menu['cancel_suite_meeting_enabled']
            assert not active_menu['retry_suite_meeting_enabled']
            assert not active_menu['delete_suite_meeting_enabled']
            assert not active_menu['task_suites'][0]['collapsed'], 'Menu click must not collapse the group'
            assert active_menu['task_suites'][0]['counts']['all']==7
            await click('cancel_suite_meeting')
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            cancelled=await state()
            assert all(j['stage'] in ('completed','failed','cancelled') for j in cancelled['jobs'] if j['suite_id']=='meeting')
            assert next(j for j in cancelled['jobs'] if j['id']=='fixture-0000')['stage']=='simulation_running'
            await click('suite_menu_meeting')
            stopped_menu=await capture('suite-02-stopped-menu')
            assert not stopped_menu['cancel_suite_meeting_enabled']
            assert stopped_menu['retry_suite_meeting_enabled'] and stopped_menu['delete_suite_meeting_enabled']
            await click('retry_suite_meeting')
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            retried=await capture('suite-03-retried-group')
            assert len(retried['jobs'])==30 and retried['task_suites'][0]['key']=='suite:meeting-retry'
            assert len([j for j in retried['jobs'] if j['suite_id']=='meeting'])==7
            assert retried['draft_config']==draft
            await search('fixture-0102')
            await click('suite_menu_meeting')
            await click('delete_suite_meeting')
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            deleted=await state()
            assert len(deleted['jobs'])==23 and deleted['task_pagination']['total']==0
            assert not any(j['suite_id']=='meeting' for j in deleted['jobs'])
            assert len([j for j in deleted['jobs'] if j['suite_id']=='meeting-retry'])==7
            await push(jobs, True)
            await search('')

            await click('suite_suite:meeting')
            collapsed = await capture('01-collapsed-suite')
            assert not any(k.startswith('fixture-01') for k in collapsed['task_rows'])
            assert len(collapsed['task_rows']) == 10
            assert len(collapsed['task_suites']) == 2
            assert collapsed['task_pagination']['pages'] == 1
            await click('suite_suite:meeting')
            await click('status_chip_failed')
            s = await capture('02-failed-tasks')
            assert s['task_pagination']['total'] == 3
            assert all(row['stage']=='failed' for row in s['task_rows'].values())
            await click('source_filter')
            await capture('03-source-menu')
            await assert_menu_hover(page, [(await state())['source_option_bag']], args.out, 'source-filter-hover')
            await click('source_option_bag')
            assert (await state())['task_pagination']['total'] == 1
            await click('status_filter')
            await capture('04-status-menu')
            await assert_menu_hover(page, [(await state())['status_option_cancelled']], args.out, 'status-filter-hover')
            await click('status_option_cancelled')
            assert (await state())['task_pagination']['total'] == 0
            await capture('05-empty-filter')
            await click('clear_task_filters')

            # Fold the first suite so the older suite's own pagination is visible.
            await click('suite_suite:meeting')
            await click('suite_tasks_suite:traffic_next_page')
            s = await capture('06-page-two')
            assert s['task_pagination']['page']==1 and len(s['task_rows'])==6
            assert s['suite_pagination']['suite:traffic']['page']==2
            updated = copy.deepcopy(jobs[6]); updated['progress']=42
            await push([updated])
            assert (await state())['suite_pagination']['suite:traffic']['page']==2
            await click('suite_tasks_suite:traffic_page_1')
            assert len((await state())['task_rows'])==10
            await click('suite_suite:meeting')
            await search('fixture-0101')
            s = await state()
            assert s['task_pagination']['page']==1 and s['task_pagination']['total']==1
            await click('task_menu_fixture-0101')
            await capture('07-task-actions-menu')
            await assert_menu_hover(page, [(await state())['cancel_fixture-0101']], args.out, 'task-cancel-hover')
            await click('cancel_fixture-0101')
            await page.wait_for_function('() => !window._handle.get_simulation_state().pending')
            changed = copy.deepcopy(jobs[-6]); changed['stage']='cancelled'
            await push([changed])
            assert (await state())['task_rows']['fixture-0101']['status_label']=='已取消'
            await click('task_menu_fixture-0101')
            await click('delete_fixture-0101')
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            await push([j for j in jobs if j['id']!='fixture-0101'], True)
            assert (await state())['task_pagination']['total']==0

            await search('fixture-0102')
            await click('inspect_fixture-0102')
            assert (await state())['page']=='detail'
            assert (await state())['draft_config']==draft
            await click('view_config_fixture-0102')
            s = await capture('08-reuse-config')
            assert s['page']=='config' and s['config_from']=='fixture-0102'
            assert s['draft_config']==jobs[-5]['config']
            reuse_width=s['panel_rect']
            await click('tasks_tab')
            assert same_rect((await state())['panel_rect'],reuse_width)
            await click('config_tab')
            assert (await state())['draft_config']==jobs[-5]['config']
            await click('tasks_tab')
            await search('')
            # Repeated tab changes preserve a manually resized sidebar too.
            before=(await state())['panel_rect']
            edge=before[2]+13
            await page.mouse.move(edge,500)
            await page.mouse.down()
            await page.mouse.move(edge-90,500,steps=15)
            await page.mouse.up()
            await page.wait_for_timeout(400)
            resized=(await state())['panel_rect']
            assert abs(resized[2]-before[2])>30, (before,resized)
            await click('config_tab')
            assert same_rect((await state())['panel_rect'],resized)
            await click('tasks_tab')
            assert same_rect((await state())['panel_rect'],resized)
            await search('')
            await page.set_viewport_size({'width':1050,'height':850})
            await page.wait_for_timeout(600)
            narrow=await capture('10-narrow-workspace')
            await click('config_tab')
            assert same_rect((await state())['panel_rect'],narrow['panel_rect'])
            await click('tasks_tab')
            await click('source_filter')
            await capture('11-narrow-menu')
            await page.keyboard.press('Escape')
            await search('fixture-0104')
            await click('suite_menu_meeting')
            await capture('suite-04-narrow-menu')
            await page.keyboard.press('Escape')
            await page.wait_for_function('()=>Object.keys(window._handle.get_simulation_state().task_popups).length===0')
            await click('replay_menu_fixture-0104')
            s=await capture('09-replay-run-menu')
            assert 'replay_fixture-0104_0' in s and 'replay_fixture-0104_1' in s
            await replay_hover('fixture-0104', 'replay-hover')
            await click('replay_fixture-0104_1')
            await page.wait_for_function('()=>window._handle.get_playback_state()?.convert_error || window._handle.get_simulation_state()?.open')
            await page.wait_for_timeout(1000)
            assert replays[-1]['path']=='/fixtures/run-2.record', replays
            assert (await state())['open'], 'Replay must keep the task sidebar open'
            await page.keyboard.press('Escape')
            await page.wait_for_timeout(300)
            actions = [json.loads(r['body'])['action'] for r in requests if r['body']]
            assert 'list' not in actions, actions
            assert actions.count('cancel')==1, actions
            assert actions.count('delete')==1, actions
            for action in ('cancel_suite','retry_suite','delete_suite'):
                assert actions.count(action)==1, actions
            assert not errors, errors
            print('PASS: task counts, suite collapse, status/source/search filters, pagination, push retention, cancel, suite cancel/retry/delete with filtered members, detail/reuse, equal tabs and stable/resizable sidebar, compact grouped rows, pinned filters/footer, run-2 replay menu, narrow layout; no list polling')
        finally:
            await page.screenshot(path=str(args.out/'final.png'))
            (args.out/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
            (args.out/'final-state.json').write_text(json.dumps(await state(),ensure_ascii=False,indent=2))
            (args.out/'network.json').write_text(json.dumps(requests,indent=2))
            (args.out/'replays.json').write_text(json.dumps(replays,indent=2))
            (args.out/'errors.json').write_text(json.dumps(errors,indent=2))
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
