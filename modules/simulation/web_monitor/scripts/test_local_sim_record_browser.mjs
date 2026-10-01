// Requires Node 22+ and /usr/bin/google-chrome. Fixture: sim_20260909_140108 record.
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,copyFile,rm} from 'node:fs/promises';
import assert from 'node:assert/strict';
const profile=await mkdtemp('/tmp/wm-local-open-');
const chrome=spawn('/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-dev-shm-usage','--enable-unsafe-swiftshader','--use-angle=swiftshader','--no-first-run','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'],{stdio:['ignore','ignore','pipe']});
let socket, chooser;
const localDir=await mkdtemp("/tmp/wm-client-only-bag-");
const selectedFile=localDir+"/client_only_sim.record.00000.20260909140108";
const bag=process.argv[3]||"/home/wangsheng/test/application-core/data/record/sim_20260909_140108.record.00000.20260909140108";
await copyFile(bag, selectedFile);
try {
 const endpoint=await new Promise((resolve,reject)=>{let log='';const timer=setTimeout(()=>reject(Error('Chrome timeout')),15000);chrome.stderr.on('data',b=>{log+=b;const m=log.match(/DevTools listening on (ws:\/\/\S+)/);if(m){clearTimeout(timer);resolve(m[1])}});chrome.on('error',reject)});
 socket=new WebSocket(endpoint);await new Promise(r=>socket.addEventListener('open',r,{once:true}));
 let seq=0;const pending=new Map(),requests=[],errors=[];
 const send=(method,params={},sessionId)=>new Promise((resolve,reject)=>{const id=++seq;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));});
 socket.addEventListener('message',e=>{const m=JSON.parse(e.data);if(m.id){const p=pending.get(m.id);if(p){pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result)}}else if(m.method==='Network.requestWillBeSent'){if(m.params.request.url.startsWith('http://127.0.0.1:')&&m.params.request.url.includes('/api/'))requests.push({url:m.params.request.url,method:m.params.request.method});}else if(m.method==='Page.fileChooserOpened'){chooser=m.params;}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);});
 const {targetId}=await send('Target.createTarget',{url:'about:blank'});
 const {sessionId}=await send('Target.attachToTarget',{targetId,flatten:true});
 const c=(m,p={})=>send(m,p,sessionId);
 const evaluate=async expression=>{const r=await c('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
 const delay=ms=>new Promise(r=>setTimeout(r,ms));
 await c('Page.enable');await c('Runtime.enable');await c('Network.enable');
 await c('Page.addScriptToEvaluateOnNewDocument',{source:'sessionStorage.setItem("wm_upload_progress",JSON.stringify({active:true,phase:"reading",filename:"stale.record",fraction:0,loaded:0,total:2151716805,message:"Reading bag"}));window.sentChunks=[];const originalFetch=window.fetch;window.fetch=function(url,opts){if(String(url).includes("/api/browser_record?action=chunk"))window.sentChunks.push(opts?.body?.size);return originalFetch.apply(this,arguments)};window.bagReads=[];const orig=FileReader.prototype.readAsArrayBuffer;FileReader.prototype.readAsArrayBuffer=function(file){window.bagReads.push({name:file.name,size:file.size});return orig.call(this,file)};'});
 await c('Emulation.setDeviceMetricsOverride',{width:1600,height:1000,deviceScaleFactor:1,mobile:false});
 await c('Page.navigate',{url:process.argv[2]||'http://127.0.0.1:9090/'});
 for(let i=0;i<120;i++){if(await evaluate('!!window._handle'))break;await delay(500);}
 for(let i=0;i<30;i++){if(await evaluate('JSON.parse(sessionStorage.getItem("wm_upload_progress")||"null")?.phase==="error"'))break;await delay(500);}
 assert.equal(await evaluate('JSON.parse(sessionStorage.getItem("wm_upload_progress")).phase'),'error','stale reading state must be dismissable');
 console.log('PASS stale Reading bag state becomes explicit interrupted error');
 const click=async(x,y)=>{await c('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});await c('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});await delay(500);};
 await click(38,110);
 let state;
 for(let i=0;i<120;i++){state=await evaluate('window._handle.get_playback_state()');const r=state.source_ui?.controls?.['Choose a bag'];if(r&&r[0]>80)break;await delay(500);}
 console.log('SOURCE',JSON.stringify(state.source_ui));
 const rect=state.source_ui.controls['Choose a bag'];assert.ok(rect,'Choose a bag control must exist');
 assert.equal(state.source_ui.controls['Open Path'],undefined);
 await c('Page.setInterceptFileChooserDialog',{enabled:true});
 await click((rect[0]+rect[2])/2,(rect[1]+rect[3])/2);
 for(let i=0;i<30&&!chooser;i++)await delay(100);
 assert.ok(chooser,'click must open browser computer file chooser');
 await c('DOM.setFileInputFiles',{backendNodeId:chooser.backendNodeId,files:[selectedFile]});
 console.log('PASS browser file chooser selects client-only /tmp file');
 let previewEvidence;
 for(let i=0;i<1800;i++){
   await delay(500);state=await evaluate('window._handle.get_playback_state()');
   if(i%20===0)console.log('PROGRESS',await evaluate('sessionStorage.getItem("wm_upload_progress")'),JSON.stringify({mcap:state.mcap,pending:state.pending,error:state.error,clock:state.clock_ready}));
   if(state.error)throw Error(state.error);
   const stream=await evaluate('JSON.parse(sessionStorage.getItem("wm_browser_stream")||"null")');
   if(stream?.first_open_bytes&&!previewEvidence){
     previewEvidence=stream;console.log('FIRST_OPEN',JSON.stringify(stream));
     assert.ok(stream.first_open_bytes<stream.total/2,'playback before half of file is sent');
   }
   if(stream?.phase==='error')throw Error(stream.message);
   if(stream?.phase==='done'&&state.clock_ready&&!state.pending&&state.mcap===stream.output)break;
 }
 assert.ok(state.clock_ready,'record must initialize playback clock');
 assert.ok(previewEvidence,'must open first segment before transferring whole bag');
 assert.equal(await evaluate('JSON.parse(sessionStorage.getItem("wm_browser_stream")).phase'),'done');
 assert.ok((await evaluate('window.sentChunks')).every(n=>n>0&&n<=2*1024*1024));
 assert.equal(requests.some(r=>r.url.includes('pick_recording')),false);
 assert.equal(requests.some(r=>r.url.includes('upload_recording')),false);
 assert.deepEqual(await evaluate('window.bagReads'),[]);
 assert.equal(errors.length,0);
 await delay(1500);
 let layers=await evaluate('window._handle.get_display_layers_state()');
 const lidar=layers.nodes['sensing/lidar'];assert.ok(lidar);await click(...lidar);
 const samples=[];
 for(const offset of [2,8,15]){
   const target=1788933669272381404n+BigInt(offset)*1000000000n;
   await evaluate('window._handle.set_playing(window._handle.get_active_recording_id(),false)');
   await evaluate('window._handle.set_time_for_timeline(window._handle.get_active_recording_id(),"publish_time",'+Number(target)+')');
   let points;
   for(let i=0;i<120;i++){
     await delay(500);state=await evaluate('window._handle.get_playback_state()');
     points=await evaluate('["/lidar/top/points","/lidar/front/points"].map(p=>window._handle.get_point_cloud_state(p))');
     if(points.every(p=>p.points>0&&Math.abs(Number(p.sample_ns)-Number(p.playhead_ns))<250000000)&&!state.pending)break;
   }
   assert.equal(await evaluate('window._handle.has_panicked()'),false);
   assert.ok(points.every(p=>p.points>0&&Math.abs(Number(p.sample_ns)-Number(p.playhead_ns))<250000000),'fresh point clouds at '+offset);
   samples.push({offset,points});
 }
 layers=await evaluate('window._handle.get_display_layers_state()');
 await evaluate('window._handle.set_time_for_timeline(window._handle.get_active_recording_id(),"publish_time",1788933671272381404)');
 await delay(1000);
 const before=await evaluate('window._handle.get_ad_scene_state().playhead_ns');
 await evaluate('window._handle.set_playing(window._handle.get_active_recording_id(),true)');
 await delay(2500);
 await evaluate('window._handle.set_playing(window._handle.get_active_recording_id(),false)');
 await delay(500);
 const after=await evaluate('window._handle.get_ad_scene_state().playhead_ns');
 assert.ok(Number(after)-Number(before)>500000000,'playback clock advances');
 assert.equal(errors.length,0);assert.equal(await evaluate('window._handle.has_panicked()'),false);
 console.log('PASS playback advances',before,after);
 state=await evaluate('window._handle.get_playback_state()');
 assert.equal(requests.some(r=>r.url.includes('upload_recording')),false);
 assert.deepEqual(await evaluate('window.bagReads'),[]);
 console.log('SAMPLES',JSON.stringify(samples));
 console.log('PLAYBACK',JSON.stringify(state));
 console.log('LAYERS',JSON.stringify(await evaluate('window._handle.get_display_layers_state()')));
 console.log('SCENE',JSON.stringify(await evaluate('window._handle.get_ad_scene_state()')));
 const shot=await c('Page.captureScreenshot',{format:'jpeg',quality:35});await writeFile('/tmp/wm-local-open.jpg',Buffer.from(shot.data,'base64'));
 await writeFile('/tmp/wm-local-open-report.json',JSON.stringify({state,requests,errors,samples,layers,previewEvidence,chunks:await evaluate("window.sentChunks")},null,2));
 console.log('PASS browser client file: bounded chunks, preview before full transfer, full playback ready');
} finally {socket?.close();chrome.kill();await rm(localDir,{recursive:true,force:true});}
