// Contract test for chunk limits and first-playback backpressure; no real network.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const source=await readFile(new URL('../rerun/crates/viewer/re_viewer/src/web_tools.rs',import.meta.url),'utf8');
const code=source.split('const BROWSER_RECORD_JS: &str = r#"')[1].split('"#;')[0];
const run=new Function('file','map','notify',code);
globalThis.window={};
const storage=new Map();
globalThis.sessionStorage={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
const MiB=1024*1024, size=10*MiB;
const file={name:'client.record',size,slice:(start,end)=>({size:end-start,start,end})};
let received=0, acknowledged=false;
const chunks=[],events=[];
globalThis.fetch=async(url,options)=>{
 const action=new URL(url,'http://localhost').searchParams.get('action');
 let body;
 if(action==='start')body={status:'ok',id:'test',chunk_bytes:2*MiB};
 else if(action==='chunk'){
   assert.ok(options.body.size<=2*MiB);
   assert.equal(options.body.start,received);
   if(received>0)assert.ok(acknowledged,'must not send remainder before preview ready');
   received+=options.body.size;chunks.push(options.body.size);
 }else if(action!=='status')throw Error('Unexpected action '+action);
 body??={status:'ok',received,total:size,source:'/cache/client.record',preview_started:true,
   preview:{status:'done',output_path:'/cache/preview.mcap'},
   complete:received===size?{status:'done',output_path:'/cache/full.mcap'}:null};
 return{ok:true,json:async()=>body};
};
await run(file,'',text=>{
 const event=JSON.parse(text);events.push(event);
 if(event.kind==='convert'&&event.job.output_path==='/cache/preview.mcap'){
   assert.equal(received,2*MiB);
   setTimeout(()=>{acknowledged=true;sessionStorage.setItem('wm_browser_preview_ready','/cache/preview.mcap')},30);
 }
});
assert.equal(chunks.length,5);
assert.equal(received,size);
const result=JSON.parse(sessionStorage.getItem('wm_browser_stream'));
assert.equal(result.phase,'done');
assert.equal(result.first_open_bytes,2*MiB);
assert.ok(events.some(e=>e.kind==='convert'&&e.job.output_path==='/cache/full.mcap'));
globalThis.fetch=async()=>({ok:false,json:async()=>({status:'error',message:'bad record'})});
await run(file,'',text=>events.push(JSON.parse(text)));
assert.equal(JSON.parse(sessionStorage.getItem('wm_browser_stream')).phase,'error');
assert.ok(events.some(e=>e.kind==='error'&&e.message.includes('bad record')));
console.log('PASS bounded slices, preview readiness before remaining bytes, completion, explicit errors');
