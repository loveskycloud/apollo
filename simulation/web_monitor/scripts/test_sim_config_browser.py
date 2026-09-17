#!/usr/bin/env python3
"""Real task detail -> editable configuration -> independent new submissions."""
import argparse
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright
from sim_browser_helpers import open_sim_tasks, sim_click, sim_state

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    parser.add_argument('--job', default='d897596d6b454fe1')
    parser.add_argument('--out', required=True)
    parser.add_argument('--staging-assets', type=Path)
    parser.add_argument('--exercise', action='store_true', help='Submit unchanged and edited copies as two real new tasks')
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    evidence, errors, console, created = [], [], [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chromium', headless=True,
            args=['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
        page = await browser.new_page(viewport={'width':1440, 'height':1000})
        page.on('pageerror', lambda e:errors.append(str(e)))
        page.on('console', lambda m:console.append({'type':m.type, 'text':m.text}))
        await page.route('https://tel.rerun.io/**', lambda r:r.fulfill(status=200,body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
            await page.route('**/re_viewer.js', lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))

        async def jobs():
            r = await page.request.post(args.url.split('/?')[0]+'/api/sim',data={'action':'list'})
            assert r.ok
            return (await r.json())['jobs']

        async def capture(name):
            s=await sim_state(page)
            evidence.append({'name':name, 'state':s})
            (out/'evidence.json').write_text(json.dumps(evidence,indent=2))
            await page.screenshot(path=str(out/f'{name}.png'))
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            r=s['panel_rect']
            assert 70<r[0]<110 and r[1]<30 and r[2]<page.viewport_size['width']-100
            return s

        async def detail(task_id):
            await open_sim_tasks(page,task_id)
            draft=(await sim_state(page))['draft_config']
            await sim_click(page,'inspect_'+task_id)
            await page.wait_for_function('id=>{const s=window._handle.get_simulation_state();return s.page==="detail"&&s.inspected_task.id===id}',arg=task_id)
            s=await capture('detail-'+task_id)
            assert s['draft_config']==draft
            assert 'enqueue' not in s and 'view_config_'+task_id in s
            return s

        async def load_config(task_id, expected):
            await sim_click(page,'view_config_'+task_id)
            await page.wait_for_function('()=>window._handle.get_simulation_state().page==="config"&&window._handle.get_simulation_state().enqueue')
            s=await capture('editable-'+task_id)
            assert s['draft_config']==expected, (s['draft_config'],expected)
            assert s['config_from']==task_id and s['inspected_task'] is None
            for key in ['Scenario','Map','Vehicle','seed','runs']:
                assert key in s

        async def drag_edit(key, value):
            await page.mouse.dblclick(*(await sim_state(page))[key])
            await page.keyboard.press('Control+a')
            await page.keyboard.insert_text(str(value))
            await page.keyboard.press('Enter')
            await page.wait_for_timeout(450)

        async def enqueue(expected):
            previous=(await sim_state(page))['last_enqueued']
            await page.wait_for_function('()=>!window._handle.get_simulation_state().pending')
            await sim_click(page,'enqueue')
            await page.wait_for_function('prev=>{const s=window._handle.get_simulation_state();return s.last_enqueued&&s.last_enqueued!==prev&&s.page==="tasks"}',arg=previous)
            task_id=(await sim_state(page))['last_enqueued']
            created.append(task_id)
            (out/'created-jobs.json').write_text(json.dumps(created))
            actual=next(j for j in await jobs() if j['id']==task_id)
            assert task_id!=args.job and actual['config']==expected,actual
            await capture('new-task-'+task_id)
            return task_id

        try:
            await page.goto(args.url,wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()',timeout=60000)
            await page.wait_for_timeout(1500)
            await open_sim_tasks(page,args.job)
            await page.wait_for_function('id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id)',arg=args.job)
            original=next(j for j in await jobs() if j['id']==args.job)
            if args.exercise:
                assert all(j['stage'] in TERMINAL for j in await jobs()), 'Existing simulation active; do not add test jobs'
            await detail(args.job)
            await sim_click(page,'back_to_tasks')
            assert (await sim_state(page))['page']=='tasks'
            # Explicit config button also works directly from the list.
            await load_config(args.job,original['config'])
            if args.exercise:
                first=await enqueue(original['config'])
                await detail(first)
                await page.wait_for_timeout(2000)
                s=await capture('live-detail-refresh')
                latest=next(j for j in s['jobs'] if j['id']==first)
                assert s['inspected_task']==latest
                await load_config(first,original['config'])
            else:
                await detail(args.job)
                await load_config(args.job,original['config'])
            await drag_edit('seed',original['config']['seed']+7)
            await drag_edit('runs',1)
            expected={**original['config'],'seed':original['config']['seed']+7,'repeat':1}
            assert (await sim_state(page))['draft_config']==expected
            await capture('edited-copy')
            await detail(args.job)
            await sim_click(page,'config_tab')
            await page.wait_for_timeout(450)
            assert (await sim_state(page))['draft_config']==expected
            await page.set_viewport_size({'width':900,'height':850})
            await page.wait_for_timeout(750)
            await capture('narrow-editable-config')
            await page.set_viewport_size({'width':1440,'height':1000})
            await page.wait_for_timeout(750)
            if args.exercise:
                second=await enqueue(expected)
                await detail(second)
                await load_config(second,expected)
                await open_sim_tasks(page)
                await page.wait_for_function('ids=>ids.every(id=>window._handle.get_simulation_state().jobs.some(j=>j.id===id&&["completed","failed","cancelled","interrupted"].includes(j.stage)))',arg=created,timeout=240000)
                actual=[j for j in await jobs() if j['id'] in created]
                (out/'finished-jobs.json').write_text(json.dumps(actual,indent=2))
                print('Actual runtime outcomes (not a determinism certification): '+str([(j['id'],j['stage']) for j in actual]),flush=True)
            unchanged=next(j for j in await jobs() if j['id']==args.job)
            assert unchanged==original, 'Historical task must never be edited'
            assert not errors,errors
            print('PASS: task detail, explicit View config, exact editable clone, edit/tab retention, historical immutability'+('; two real new submissions' if args.exercise else ''),flush=True)
        finally:
            (out/'errors.json').write_text(json.dumps(errors,indent=2))
            (out/'console.json').write_text(json.dumps(console,indent=2))
            await page.screenshot(path=str(out/'final.png'))
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
