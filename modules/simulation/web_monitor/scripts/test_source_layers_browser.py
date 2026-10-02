#!/usr/bin/env python3
"""Source and Layers reference layout, using a real MCAP and browser interactions."""
import argparse
import asyncio
import json
from pathlib import Path

from mcap.reader import make_reader
from PIL import Image
from playwright.async_api import async_playwright


async def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--url',default='http://127.0.0.1:9090/?renderer=webgl&theme=dark')
    ap.add_argument('--mcap',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--staging-assets',type=Path)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    with args.mcap.open('rb') as stream:
        summary=make_reader(stream).get_summary()
        raw_topics={c.topic for c in summary.channels.values() if c.topic.startswith('/apollo/')}
        expected_duration=f'{(summary.statistics.message_end_time-summary.statistics.message_start_time)/1e9:.2f} s'
    errors,evidence=[],[]
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True,args=['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
        page=await browser.new_page(viewport={'width':1728,'height':910})
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.route('https://tel.rerun.io/**',lambda r:r.fulfill(status=200,body='{}'))
        if args.staging_assets:
            await page.route('**/re_viewer_bg.wasm',lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer_bg.wasm'),content_type='application/wasm'))
            await page.route('**/re_viewer.js',lambda r:r.fulfill(path=str(args.staging_assets/'re_viewer.js'),content_type='text/javascript'))
        async def source():return await page.evaluate('()=>window._handle.get_playback_state().source_ui')
        async def layers():return await page.evaluate('()=>window._handle.get_display_layers_state()')
        async def click(point):
            await page.mouse.click(*point);await page.wait_for_timeout(300)
        async def source_click(key):
            r=(await source())['controls'][key];await click([(r[0]+r[2])/2,(r[1]+r[3])/2])
        async def layer_click(key):await click((await layers())['controls'][key])
        async def search(query):
            await layer_click('search');await page.keyboard.press('Control+a')
            if query:await page.keyboard.insert_text(query)
            else:await page.keyboard.press('Backspace')
            await page.keyboard.press('Tab');await page.wait_for_timeout(300)
        async def settled():
            await page.wait_for_function('''()=>{const p=window._handle.get_playback_state();return p.clock_ready&&!p.pending&&!p.waiting_receipt&&!p.error}''',timeout=90000)
        async def scene():return await page.evaluate('()=>window._handle.get_ad_scene_state()')
        async def visibility(prefix,on):
            await page.wait_for_function('''([prefix,on])=>{
                const s=window._handle.get_display_layers_state();
                const ls=s.layers.filter(l=>l.path===prefix||l.path.startsWith(prefix+'/'));
                return ls.length&&ls.every(l=>l.enabled===on&&s.views.every(v=>v.entities.filter(e=>l.entities.some(root=>e.path===root||e.path.startsWith(root+'/'))).every(e=>e.visible===on)));
            }''',arg=[prefix,on],timeout=15000)
            await settled()
        async def capture(name):
            await page.mouse.move(1700,900);await page.wait_for_timeout(250)
            s,l=await source(),await layers()
            evidence.append({'name':name,'source':s,'layers':l})
            await page.screenshot(path=str(args.out/f'{name}.png'))
            assert not errors,errors
            assert not await page.evaluate('()=>window._handle.has_panicked()')
            if l['panel_rect']:
                r=l['panel_rect'];size=page.viewport_size
                assert r[0]>=0 and r[2]<=size['width'] and r[3]<=size['height'],r
                for row in l['rows'].values():assert abs(row['rect'][3]-row['rect'][1]-28)<.5,row
            print(name,flush=True)
            return s,l
        try:
            await page.goto(args.url,wait_until='domcontentloaded')
            await page.wait_for_function('()=>window._handle&&!window._handle.has_panicked()',timeout=90000)
            await page.wait_for_timeout(5000)
            await click([35,190])
            await page.wait_for_function('()=>window._handle.get_layout_state()?.controls?.["layout_Planning layout"]')
            layout=await page.evaluate('()=>window._handle.get_layout_state()')
            await click(layout['controls']['layout_Planning layout'])
            await click([35,115])
            await page.wait_for_function('()=>window._handle.get_playback_state()?.source_ui?.open')
            s,_=await capture('01-source-empty')
            controls=s['controls'];panel=controls['panel']
            assert abs(panel[2]-panel[0]-394)<2,panel
            for label in ('数据源','数据加载','录制文件','地图','数据概览'):
                assert abs(controls['label_'+label][0]-panel[0])<1,(label,controls)
            for key in ('Mode','Choose a bag','Map'):
                r=controls[key];assert abs(r[3]-r[1]-38)<.5,(key,r)
                assert r[0]>=panel[0] and r[2]<=panel[2]+.5,(key,r,panel)
            await source_click('Mode');await capture('02-source-mode')
            await page.keyboard.press('Escape');await page.wait_for_timeout(300)
            await source_click('Map');await capture('03-source-map-menu')
            await source_click('map_none')
            await source_click('Choose a bag')
            await page.locator('#wm-local-bag-input').set_input_files(args.mcap)
            await settled();await page.wait_for_timeout(3000)
            await layer_click('all');await settled()
            # Seek to the reference recording's moving segment through the media input.
            p=await page.evaluate('()=>window._handle.get_playback_state()')
            r=p['media_bar']['seek_input'];await click([(r[0]+r[2])/2,(r[1]+r[3])/2])
            await page.keyboard.press('Control+a');await page.keyboard.insert_text('3.100000000')
            await page.keyboard.press('Enter');await settled()
            await page.wait_for_function('''()=>{
                const panels=window._handle.get_debug_panels_state();
                return panels.length===3&&panels.every(p=>p.has_data&&!p.pending&&!p.error);
            }''',timeout=90000)
            await page.wait_for_timeout(500)
            s,l=await capture('04-source-and-layers')
            props={r['label']:r['value'] for r in s['properties']}
            assert list(props)==['时长','Topic 数量','起始时间','结束时间','车辆 ID','场景 ID','行程 ID'],props
            assert props['时长']==expected_duration and props['Topic 数量']==str(len(raw_topics)),props
            assert props['起始时间']=='1970-01-01 00:00:01.000' and props['结束时间']=='1970-01-01 00:00:16.610',props
            assert abs(l['panel_rect'][2]-l['panel_rect'][0]-320)<2,l['panel_rect']
            assert 420<=l['panel_rect'][3]-l['panel_rect'][1]<=432,l['panel_rect']
            assert len(l['layers'])==7 and all(layer['checkbox'] for layer in l['layers']),l
            assert max(r['rect'][3] for r in l['rows'].values())<=l['panel_rect'][3]-9,l
            assert [l['rows'][p]['label'] for p in ('map','planning','localization','perception')]==['地图','规划','定位','感知']
            image=Image.open(args.out/'04-source-and-layers.png').convert('RGB')
            assert image.getpixel((round(panel[0]+10),800))==(24,24,38)
            before=await scene()
            await click(l['nodes']['map/lane_boundaries']);await visibility('map/lane_boundaries',False)
            _,off=await capture('05-layer-partial')
            assert off['rows']['map']['partial']
            after=await scene();assert before['playhead_ns']==after['playhead_ns']
            assert before['map']==after['map'],'Visibility must retain cached map geometry'
            await click(off['nodes']['map']);await visibility('map',True)
            await layer_click('collapse_map')
            assert 'map/lane_boundaries' not in (await layers())['nodes']
            await search('停车')
            _,filtered=await capture('06-layer-search')
            assert set(filtered['rows'])=={'map','map/parking_spaces'}
            await search('/PLANNING/TRAJECTORY')
            assert set((await layers())['rows'])=={'planning','planning/trajectory'}
            await search('不存在');assert not (await layers())['rows']
            await search('停车');await layer_click('none');await settled()
            assert all(not l['enabled'] for l in (await layers())['layers'])
            await layer_click('all');await settled()
            assert all(l['enabled'] for l in (await layers())['layers'])
            await search('')
            assert 'map/lane_boundaries' not in (await layers())['nodes'],'Search must preserve collapse state'
            await layer_click('collapse_map')
            await layer_click('close');await page.wait_for_timeout(300)
            assert not (await layers())['expanded']
            await layer_click('toggle')
            await source_click('more');await capture('07-source-more-menu')
            await page.keyboard.press('Escape');await page.wait_for_timeout(300)
            await page.set_viewport_size({'width':1050,'height':750});await page.wait_for_timeout(600)
            await capture('08-narrow-source-and-layers')
            await page.set_viewport_size({'width':1728,'height':910});await page.wait_for_timeout(500)
            await capture('09-final-reference-layout')
            print('PASS: actual file picker, map and source menus, seven live metadata rows, reference geometry/colors, Chinese layers, search/fold/tri-state/all/none/close, real cached visibility, stable clock, narrow bounds',flush=True)
        finally:
            await page.screenshot(path=str(args.out/'final.png'))
            (args.out/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
            (args.out/'errors.json').write_text(json.dumps(errors,indent=2))
            (args.out/'debug-panels.json').write_text(json.dumps(await page.evaluate('()=>window._handle.get_debug_panels_state()'),ensure_ascii=False,indent=2))
            await browser.close()

if __name__=='__main__':asyncio.run(main())
