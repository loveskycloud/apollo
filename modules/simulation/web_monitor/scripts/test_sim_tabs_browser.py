#!/usr/bin/env python3
"""Real docked Sim tab navigation, FIFO task states, immutable config and replay."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright
from sim_browser_helpers import open_sim_tasks, sim_click, sim_state
from test_session_recovery_browser import READY

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--template-job', default='d897596d6b454fe1')
    parser.add_argument('--out', required=True)
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--exercise', action='store_true', help='Create two real jobs; cancel only the second test job')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, errors, created, console = [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.on('console', lambda m:console.append({'type':m.type,'text':m.text}))
        await page.route('https://tel.rerun.io/**', lambda r:r.fulfill(status=200,body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))

        async def jobs():
            r = await page.request.post(args.url.split('/?')[0]+'/api/sim', data={'action':'list'})
            return (await r.json())['jobs']

        async def capture(name):
            s = await sim_state(page)
            evidence.append({'name':name, 'simulation':s})
            (out/'evidence.json').write_text(json.dumps(evidence,indent=2))
            await page.screenshot(path=str(out/f'{name}.png'))
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            if s['open']:
                r=s['panel_rect']
                assert 70<r[0]<110 and r[1]<30 and r[2]<page.viewport_size['width']-100, r
                # A docked panel reserves space; it cannot cover the viewport.
                views=await page.evaluate('()=>window._handle.get_vehicle_dashboard_state().views')
                if views:
                    assert min(v['pane'][0] for v in views)>=r[2]-2
            return s

        async def fill(key,value):
            await sim_click(page,key)
            await page.keyboard.press('Control+a')
            await page.keyboard.insert_text(value)
            await page.keyboard.press('Tab')
            await page.wait_for_timeout(350)

        async def enqueue():
            previous=(await sim_state(page))['last_enqueued']
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            await sim_click(page,'enqueue')
            await page.wait_for_function('prev=>{const s=window._handle.get_simulation_state();return s.last_enqueued&&s.last_enqueued!==prev&&s.tab==="Simulation Tasks"}',arg=previous)
            task_id=(await sim_state(page))['last_enqueued']
            created.append(task_id)
            (out/'created-jobs.json').write_text(json.dumps(created))
            await page.wait_for_function('id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id)',arg=task_id)
            return task_id

        async def inspect(task_id):
            await open_sim_tasks(page,task_id)
            expected=next(j for j in (await sim_state(page))['jobs'] if j['id']==task_id)['config']
            draft=(await sim_state(page))['draft_config']
            await sim_click(page,'inspect_'+task_id)
            await page.wait_for_function('id=>{const s=window._handle.get_simulation_state();return s.page==="detail"&&s.inspected_task?.id===id}',arg=task_id)
            s=await capture('inspect-'+task_id)
            assert s['inspected_task']['config']==expected
            assert s['draft_config']==draft and 'enqueue' not in s
            await sim_click(page,'config_tab')
            await page.wait_for_function('()=>window._handle.get_simulation_state().enqueue')
            assert (await sim_state(page))['draft_config']==draft

        try:
            await page.goto(args.url,wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()',timeout=60000)
            await page.wait_for_timeout(1500)
            await page.mouse.click(35,340)
            await page.wait_for_function('()=>window._handle.get_simulation_state()?.catalog_ready')
            await capture('docked-config')
            await open_sim_tasks(page)
            await page.wait_for_function('()=>window._handle.get_simulation_state().jobs.length>0')
            s=await capture('three-task-sections')
            assert s['task_counts']['all']==len(s['jobs'])
            assert s['task_counts']['running']==len(s['groups']['running'])
            assert s['task_counts']['queued']==len(s['groups']['queued'])
            assert len(s['task_rows'])<=s['task_pagination']['page_size']**2
            assert set(s['groups']['finished'])=={j['id'] for j in s['jobs'] if j['stage'] in TERMINAL}
            await inspect(args.template_job)
            replay_id=args.template_job
            if args.exercise:
                current=await jobs()
                assert all(j['stage'] in TERMINAL for j in current), 'Do not interfere with an existing active simulation'
                template=next(j for j in current if j['id']==args.template_job)['config']
                await sim_click(page,template['kind'])
                for key,field in [('Scenario','source'),('Map','map')]:
                    await fill(key,template[field])
                vehicle = template.get('profile') or template['vehicle'].removesuffix(
                    '/modules/common/data/vehicle_param.pb.txt')
                await fill('Vehicle', vehicle)
                for module in ['PREDICTION','PLANNING','CONTROL','ROUTING']:
                    selected=(await sim_state(page))['draft_config']['modules']
                    if (module in selected)!=(module in template['modules']):
                        await sim_click(page,module)
                # Three runs keep the first real task active while queue navigation is tested.
                await page.mouse.dblclick(*(await sim_state(page))['runs'])
                await page.keyboard.press('Control+a')
                await page.keyboard.insert_text('3')
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(450)
                assert (await sim_state(page))['draft_config']['repeat']==3
                await capture('configured-real-task')
                first=await enqueue()
                await page.wait_for_function('id=>window._handle.get_simulation_state().groups.running.includes(id)',arg=first)
                await capture('start-opens-running-tasks')
                await inspect(first)
                second=await enqueue()
                await page.wait_for_function('ids=>{const g=window._handle.get_simulation_state().groups;return g.running.includes(ids[0])&&g.queued.includes(ids[1])&&g.finished.length>0}',arg=[first,second])
                await capture('running-queued-finished')
                await inspect(second)
                await open_sim_tasks(page,second)
                await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
                await sim_click(page,'cancel_'+second)
                await page.wait_for_function('id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id&&j.stage==="cancelled")',arg=second)
                await capture('cancelled-remains-explicit')
                # Closing/switching the sidebar never cancels the running task.
                await page.mouse.click(35,113)
                await page.wait_for_function('()=>!window._handle.get_simulation_state().open')
                await capture('source-replaces-sim-panel')
                assert next(j for j in await jobs() if j['id']==first)['stage']!='cancelled'
                await open_sim_tasks(page)
                await page.set_viewport_size({'width':900,'height':700})
                await page.wait_for_timeout(750)
                await capture('narrow-docked-tasks')
                await page.wait_for_function('id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id&&["completed","failed","cancelled","interrupted"].includes(j.stage))',arg=first,timeout=180000)
                actual=next(j for j in await jobs() if j['id']==first)
                (out/'finished-test-job.json').write_text(json.dumps(actual,indent=2))
                # This is a UI workflow test, not a determinism certification.
                # Require all three actual runs to finish and preserve the service's
                # result, including a reported comparison failure (never relabel it).
                assert actual['stage'] in {'completed','failed'},actual
                assert len(actual['outputs'])==3,actual
                assert sum(h['stage']=='simulation_end' for h in actual['history'])==3
                if actual['stage']=='failed':
                    assert actual.get('analysis',{}).get('determinism')=='FAIL',actual
                    assert actual['error']=='Determinism comparison failed; output bags and first differences are retained'
                await open_sim_tasks(page,first)
                s=await capture('finished-task-actual-status')
                assert first in s['groups']['finished']
                assert next(j for j in s['jobs'] if j['id']==first)['stage']==actual['stage']
                print('Actual simulation result: '+actual['stage']+'; determinism='+actual['analysis']['determinism'],flush=True)
                await page.set_viewport_size({'width':1440,'height':1000})
                await page.wait_for_timeout(750)
                await inspect(first)
                replay_id=first
            await open_sim_tasks(page,replay_id)
            await capture('finished-task-replay')
            await sim_click(page,'replay_'+replay_id+'_0')
            await page.wait_for_function(READY,timeout=90000)
            await page.wait_for_function('()=>!window._handle.get_simulation_state().open')
            await page.wait_for_function('()=>{const p=window._handle.get_debug_panels_state();return p.length>=5&&p.every(x=>x.has_data&&!x.pending&&!x.error)}')
            await page.wait_for_function('()=>window._handle.get_vehicle_dashboard_state().data?.streams?.chassis?.sample')
            await page.mouse.click(110,40)  # Collapse the default Layers popover.
            hud=await page.evaluate('()=>window._handle.get_vehicle_dashboard_state()')
            await page.mouse.click(*hud['views'][0]['camera']['locate'])
            await page.mouse.move(70,900)
            await page.wait_for_timeout(6500)  # Let normal loading notifications expire.
            await capture('replay-closes-sidebar')
            replay=await page.evaluate('()=>({playback:window._handle.get_playback_state(),panels:window._handle.get_debug_panels_state(),hud:window._handle.get_vehicle_dashboard_state()})')
            (out/'replay-state.json').write_text(json.dumps(replay,indent=2))
            assert not errors,errors
            print('PASS: docked tabs, three task sections, exact read-only snapshots, draft preservation and replay'+('; real enqueue/FIFO/cancel/terminal-status parity' if args.exercise else ''),flush=True)
        finally:
            (out/'errors.json').write_text(json.dumps(errors,indent=2))
            (out/'console.json').write_text(json.dumps(console,indent=2))
            await page.screenshot(path=str(out/'final.png'))
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
