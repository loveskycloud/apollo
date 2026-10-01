// Expand the baseline suite using every directed lane and connected map routes.
import {readFileSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {parseApolloBaseMapText,parseApolloLaneWidths} from '../src/map/loaders/apolloBaseMapText.ts';
import {buildWorldSimScenarioPayload} from '../src/scenarios/exportWorldSimScenario.ts';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..'), mapId='beijing_zongyuan_1haolou';
if(!process.argv[2]) throw Error('Provide base_map.txt');
const mapText=readFileSync(process.argv[2],'utf8'), widths=parseApolloLaneWidths(mapText);
const map=parseApolloBaseMapText(mapText,mapId);map.id=mapId;
const origin=map.meta.origin;
const lanes=new Map(map.lanes.map(l=>[l.id,{...l,points:l.centerline.map(p=>({x:p.x+origin.x,y:p.y+origin.y,z:0}))}]));
for(const l of lanes.values()) {l.arc=[0];for(let i=1;i<l.points.length;i++)l.arc.push(l.arc.at(-1)+Math.hypot(l.points[i].x-l.points[i-1].x,l.points[i].y-l.points[i-1].y));l.length=l.arc.at(-1);}
function lanePose(id,s,lateral=0){const l=lanes.get(id);s=Math.max(.001,Math.min(l.length-.001,s));const i=l.arc.findIndex((x,i)=>i>0&&x>=s);const a=l.points[i-1],b=l.points[i],t=(s-l.arc[i-1])/(l.arc[i]-l.arc[i-1]);const heading=Math.atan2(b.y-a.y,b.x-a.x);return {position:{x:a.x+(b.x-a.x)*t-Math.sin(heading)*lateral,y:a.y+(b.y-a.y)*t+Math.cos(heading)*lateral,z:0},heading};}
function connectedRoute(anchor){
 const ids=[anchor];let before=0;
 while(before<14){const p=lanes.get(ids[0]).predecessors.find(id=>lanes.has(id)&&!ids.includes(id));if(!p)break;ids.unshift(p);before+=lanes.get(p).length;}
 let after=0;
 while(after<12||ids.reduce((s,id)=>s+lanes.get(id).length,0)<24){const p=lanes.get(ids.at(-1)).successors.find(id=>lanes.has(id)&&!ids.includes(id));if(!p)break;ids.push(p);after+=lanes.get(p).length;}
 const offsets=[0];for(const id of ids)offsets.push(offsets.at(-1)+lanes.get(id).length);
 const total=offsets.at(-1),at=offsets[ids.indexOf(anchor)]+lanes.get(anchor).length/2;
 let start=Math.max(.5,at-7);
 const pose=(s,l=0)=>{const i=Math.min(ids.length-1,offsets.findIndex((d,i)=>i>0&&d>s)-1);if(i<0)throw Error(`outside route ${s}/${total}`);return lanePose(ids[i],s-offsets[i],l);};
 // Begin on a straight approach, away from a routing-lane join. Starting
 // centimetres before a short bend can snap Routing to the next lane and put
 // the physical ego behind the planning reference's first sample.
 const initial=start;
 for(let back=0;back<=5;back+=.25){
  const candidate=initial-back;if(candidate<.8)break;
  const i=Math.min(ids.length-1,offsets.findIndex((d,i)=>i>0&&d>candidate)-1);
  const local=candidate-offsets[i];
  const dh=pose(candidate+.35).heading-pose(candidate-.35).heading;
  if(local>.8&&lanes.get(ids[i]).length-local>.8&&Math.abs(Math.atan2(Math.sin(dh),Math.cos(dh)))<.06){start=candidate;break;}
 }
 const end=Math.min(total-1,Math.max(at+10,start+18));
 const samples=(a,b,l=0)=>{let p=[];for(let s=a;s<b;s+=.35)p.push(pose(s,l));p.push(pose(b,l));return p;};
 const waypoints=[pose(start)];for(let i=0;i<ids.length;i++){const s=offsets[i]+lanes.get(ids[i]).length/2;if(s>start+.5&&s<end-.5)waypoints.push(pose(s));}waypoints.push(pose(end));
 const roadAt=s=>{
  const i=Math.min(ids.length-1,offsets.findIndex((d,i)=>i>0&&d>s)-1),lane=widths.get(ids[i]);
  const interpolate=values=>{if(!values?.length)throw Error('Missing map width samples');const t=s-offsets[i];if(t<=values[0].s)return values[0].width;for(let j=1;j<values.length;j++){const a=values[j-1],b=values[j];if(t<=b.s)return a.width+(b.width-a.width)*(t-a.s)/(b.s-a.s);}return values.at(-1).width;};
  return {left:interpolate(lane.left),right:interpolate(lane.right)};
 };
 return {ids,offsets,total,at,start,end,pose,samples,waypoints,roadAt};
}
function actor(id,type,poses,speed,enabled=true,size){return {id,name:id,type,...poses[0],speed,enabled,size:size??(type==='pedestrian'?{x:.4,y:.4,z:1.7}:{x:.72,y:.5,z:.8}),color:'#F5B654',activeRouteId:id+'-route',routes:[{id:id+'-route',name:id,pathType:'polyline',waypoints:poses.map((p,i)=>({id:id+'-'+i,...p,speed}))}]};}
function location(id,route,s,actions){const p=route.pose(s);return {id,name:id,type:'location',center:p.position,heading:p.heading,size:{x:.8,y:1.0,z:3},targetAgentId:'ego',actions};}
const out=resolve(root,'examples',mapId),manifestPath=resolve(out,mapId+'.suite.json');
const passability=JSON.parse(readFileSync(resolve(out,'passability-evidence.json'))).scenes;
const manifest=JSON.parse(readFileSync(manifestPath));manifest.scenarios=manifest.scenarios.filter(p=>!p.startsWith('coverage_'));
manifest.excluded=[];
const coverage=[];
const evaluations=[];
const sha=text=>createHash('sha256').update(text).digest('hex');
const segmentDistance=(p,a,b)=>{const dx=b.position.x-a.position.x,dy=b.position.y-a.position.y;const t=Math.max(0,Math.min(1,((p.position.x-a.position.x)*dx+(p.position.y-a.position.y)*dy)/(dx*dx+dy*dy)));return Math.hypot(p.position.x-a.position.x-t*dx,p.position.y-a.position.y-t*dy);};
function emit(anchor,family,variant=0){
 const route=connectedRoute(anchor),id=`coverage_${anchor}_${family}`,l=lanes.get(anchor),at=route.at;
 const scene={id,name:`${anchor} · ${family}`,description:`Map coverage: ${route.ids.join(' -> ')}; family ${family}; anchor ${anchor}.`,mapId,duration:180,
 agents:[actor('ego','ego',route.waypoints,0)],triggers:[]};
 const add=(a,triggerAt)=>{scene.agents.push(a);if(triggerAt!==undefined){a.enabled=false;scene.triggers.push(location('activate-'+a.id,route,triggerAt,[{kind:'enable',targetAgentId:a.id}]));}};
 const side=variant%2 ? -1 : 1;
 const auditStatic=()=>scene.agents.filter(a=>a.type==='static').map(a=>{
  let nearest={s:route.start,d:Infinity};for(let s=route.start;s<route.end;s+=.025){const p=route.pose(s).position,d=Math.hypot(p.x-a.position.x,p.y-a.position.y);if(d<nearest.d)nearest={s,d};}
  const p=route.pose(nearest.s),w=route.roadAt(nearest.s),lateral=-(a.position.x-p.position.x)*Math.sin(p.heading)+(a.position.y-p.position.y)*Math.cos(p.heading);
  const angle=a.heading-p.heading,extent=Math.abs(Math.sin(angle))*a.size.x/2+Math.abs(Math.cos(angle))*a.size.y/2;
  const passage=Math.max(w.left-lateral-extent,w.right+lateral-extent);
  const dh=route.pose(nearest.s+.3).heading-route.pose(Math.max(route.start,nearest.s-.3)).heading;
  const curvature=Math.abs(Math.atan2(Math.sin(dh),Math.cos(dh)))/.6;
  // Rear-axle pose: the 0.62 m nose sweeps farther out than the 0.25 m
  // half-width on a bend. Width-only audits incorrectly call these passable.
  const radius=1/Math.max(.001,Math.min(1/.5366601099392426,curvature));
  const sweptWidth=Math.sqrt((radius+.25)**2+.62**2)-(radius-.25);
  const required=sweptWidth+.12+.02;
  return {actor_id:a.id,s:nearest.s,lane_width_m:w.left+w.right,available_passage_m:passage,
          curvature_per_m:curvature,swept_vehicle_width_m:sweptWidth,required_passage_m:required,blocked:passage<required};
 });
 const crossing=(id,s,sign,speed,release,size,type='pedestrian')=>{
  const activation=route.pose(release),egoSamples=route.samples(route.start,route.end);
  const distance=(a,b)=>Math.hypot(a.position.x-b.position.x,a.position.y-b.position.y);
  const triggerSamples=egoSamples.filter(p=>distance(p,activation)<1.0);
  const blockers=auditStatic().filter(a=>a.blocked);
  const waiting=blockers.flatMap(a=>route.samples(Math.max(route.start,a.s-3.5),a.s-.9));
  let poses,center;
  for(let crossingS=s;crossingS<=route.end-1;crossingS+=.5){
   center=route.pose(crossingS);
   for(const minimumOffset of [2.5,.8]){
   const outside=(direction,valid)=>{for(let offset=minimumOffset;offset<=30;offset+=.25){const p=route.pose(crossingS,direction*offset);if(valid(p))return p;}throw Error(`No crossing endpoint for ${anchor}/${id}`);};
   const candidate=[outside(sign,p=>triggerSamples.every(q=>distance(p,q)>=3)),outside(-sign,p=>egoSamples.every(q=>distance(p,q)>=1.2))];
   // Scripted walkers must cross the road, not walk through a car correctly
   // waiting at a permanent obstruction. Keep the interaction downstream.
   // On a hairpin the normal can intersect an earlier branch of the route.
   // Keep a yielding approach available even without a permanent blocker;
   // shorter sidewalk paths still obey the same spawn/end clearances.
   const upstream=route.samples(route.start,Math.max(route.start,crossingS-1.5));
   if([...waiting,...upstream].every(p=>segmentDistance(p,candidate[0],candidate[1])>=1.2)){poses=candidate;break;}
   }
   if(poses)break;
  }
  if(!poses)throw Error(`Crossing overlaps the blocked-road waiting region: ${anchor}/${id}`);
  poses[0].heading=center.heading-sign*Math.PI/2;poses[1].heading=poses[0].heading;
  add(actor(id,type,poses,speed,false,size),release);
 };
 if(family==='static_left'||family==='static_right'||family==='static_slalom'){
  const offsets=family==='static_slalom'?[-1.0,1.2]:[0];
  offsets.forEach((ds,i)=>{const s=Math.max(route.start+3,Math.min(route.end-3,at+ds));const sign=family==='static_right'?-1:(i%2?-1:1);add(actor('static-'+i,'static',[route.pose(s,sign*Math.max(.25,l.width/2-.08))],0,true,{x:.35,y:.18,z:.6}));});
 }
 if(family==='overtake'||family==='vehicle_train'){
  const count=family==='overtake'?1:3;
  for(let i=0;i<count;i++){
   const s=Math.min(route.end-4,at-1+i*1.5),offset=side*Math.max(.05,l.width/2-.3);
   // Drive through the destination so a parked lead car cannot make completion impossible.
   add(actor('lead-'+i,'vehicle',route.samples(s,route.total-.25,offset),.22+i*.035,true,{x:.6,y:.32,z:.6}));
  }
 }
 if(family==='pedestrian_group')for(let i=0;i<6;i++)crossing('ped-'+i,at+(i%3-1)*.6,i%2?-1:1,.35+i*.06,Math.max(route.start+.5,at-4+i*.25));
 if(family==='continuous_crossing'){
  const release=Math.max(route.start+.5,at-4),station=Math.min(route.end-3,at);
  crossing('convoy-template',station,side,.55,release,{x:.65,y:.4,z:.6},'vehicle');
  const template=scene.agents.pop();scene.triggers.pop();
  const points=template.routes[0].waypoints,a=points[0],b=points[1],heading=a.heading;
  const shifted=(p,d)=>({position:{x:p.position.x+d*Math.cos(heading),y:p.position.y+d*Math.sin(heading),z:0},heading});
  const ego=route.samples(route.start,route.end),trigger=route.pose(release),near=ego.filter(p=>Math.hypot(p.position.x-trigger.position.x,p.position.y-trigger.position.y)<1);
  const clear=(p,refs,d)=>refs.every(q=>Math.hypot(p.position.x-q.position.x,p.position.y-q.position.y)>=d);
  let start=0,end=4.2;
  while(![0,1,2,3].every(i=>clear(shifted(a,start-i*1.4),near,3))){start-=.25;if(start < -30)throw Error('Unsafe convoy spawn');}
  while(![0,1,2,3].every(i=>clear(shifted(b,end-i*1.4),ego,1.2))){end+=.25;if(end>30)throw Error('Unsafe convoy endpoint');}
  // A real successive stream: same corridor, direction and speed, fixed body
  // separation. Four crossing headings at a hairpin otherwise intersect and
  // can surround a yielding ego with mutually incompatible scripted motions.
  for(let i=0;i<4;i++)add(actor('cross-car-'+i,'vehicle',[shifted(a,start-i*1.4),shifted(b,end-i*1.4)],.55,false,{x:.65,y:.4,z:.6}),release);
 }
 if(family==='mixed'){
  add(actor('roadside','static',[route.pose(at-1,Math.max(.25,l.width/2-.08))],0,true,{x:.4,y:.18,z:.5}));
  for(let i=0;i<3;i++)crossing('ped-'+i,at+1+i*.5,i%2?-1:1,.4+i*.1,Math.max(route.start+.5,at-4+i*.5));
 }
 const passage=auditStatic();
 const geometry=buildWorldSimScenarioPayload(scene);
 const geometryHash=sha(JSON.stringify({mapId:geometry.mapId,ego:geometry.ego,agents:geometry.agents,triggers:geometry.triggers}));
 const proof=passability[id];
 if(proof && proof.geometry_sha256!==geometryHash)throw Error(`Passability evidence is stale for ${id}; review changed geometry`);
 const blocked=proof?[]:passage.filter(a=>a.blocked);
 const expectation=blocked.length?'safe_stop':(['pedestrian_group','continuous_crossing','mixed'].includes(family)?'yield_then_proceed':'reach_goal');
 const reason=blocked.length?'窄路永久障碍占用可通行空间，预期在障碍前安全停车等待，不要求强行到达终点。':expectation==='yield_then_proceed'?'动态障碍会离开道路，预期合理让行后到达终点。':'道路可通行，预期无碰撞到达终点。';
 scene.description+=' Expected: '+expectation+'. '+reason;
 const payload=buildWorldSimScenarioPayload(scene),text=JSON.stringify(payload,null,2)+'\n';
 writeFileSync(resolve(out,id+'.worldsim.scenario.json'),text);
 writeFileSync(resolve(out,id+'.mineproj.json'),JSON.stringify({kind:'mine-project',version:1,name:scene.name,mapSource:'embedded',map:{name:mapId,data:map},scenario:scene,savedAt:'2026-09-29T00:00:00.000Z'},null,2)+'\n');
 const evaluation={kind:'worldsim-evaluation',version:1,scenario_sha256:sha(text),expectation,reason,audit:{anchor,passages:passage,static_obstacle_clearance_m:.12,road_margin_m:.02,vehicle_width_m:.5}};
 if(proof)evaluation.audit.passability_evidence=proof;
 if(blocked.length){
  evaluation.validity={status:'INVALID',scope:'passage-regression',reason:'永久静态障碍未满足当前车辆弯道扫掠及安全余量的通行要求；从通行验证集合剔除，保留场景及原失败证据。局部扫掠审核不等同于任意姿态下的不可通行数学证明。'};
  manifest.excluded.push({scenario:id+'.worldsim.scenario.json',scenario_sha256:sha(text),...evaluation.validity,evidence:blocked});
 }
 if(blocked.length){const first=blocked.reduce((a,b)=>a.s<b.s?a:b);evaluation.blocked_by=blocked.map(a=>a.actor_id);evaluation.stop_path=route.samples(Math.max(route.start,first.s-3.5),first.s-.9).map(p=>({x:p.position.x,y:p.position.y}));}
 writeFileSync(resolve(out,id+'.evaluation.json'),JSON.stringify(evaluation,null,2)+'\n');evaluations.push({id,...evaluation});
 if(!blocked.length)manifest.scenarios.push(id+'.worldsim.scenario.json');coverage.push({id,anchor,family,route:route.ids,start_s:route.start,end_s:route.end,actor_count:scene.agents.length-1,expectation,validity:blocked.length?'INVALID':'ACTIVE'});
}
for(const id of lanes.keys())emit(id,'clear');
const anchors=['Lane_9','Lane_10','Lane_47','Lane_42','Lane_72','Lane_48','Lane_50','Lane_75','Lane_60','Lane_39','Lane_65','Lane_67'];
for(const [i,id] of anchors.flatMap(id=>[id,id+'_reverse']).entries())for(const family of ['static_left','static_right','static_slalom','overtake','vehicle_train','pedestrian_group','continuous_crossing','mixed'])emit(id,family,i);
manifest.name=`Beijing 1haolou · ${manifest.scenarios.length} active · ${manifest.excluded.length} invalid · all-map`;
writeFileSync(manifestPath,JSON.stringify(manifest,null,2)+'\n');
writeFileSync(resolve(out,'coverage.json'),JSON.stringify({mapId,directed_lanes:lanes.size,total_scenarios:manifest.scenarios.length,scenarios:coverage},null,2)+'\n');
console.log(JSON.stringify({scenarios:manifest.scenarios.length,directed_lanes:lanes.size,coverage:coverage.length}));

for(const member of manifest.scenarios.filter(p=>!p.startsWith('coverage_'))){
 const text=readFileSync(resolve(out,member),'utf8'),scene=JSON.parse(text),dynamic=scene.agents.some(a=>a.speed>0||a.routes.some(r=>r.waypoints.some(w=>w.speed>0)));
 const evaluation={kind:'worldsim-evaluation',version:1,scenario_sha256:sha(text),expectation:dynamic?'yield_then_proceed':'reach_goal',reason:dynamic?'动态障碍会离开道路，预期合理让行后到达终点。':'道路可通行，预期无碰撞到达终点。'};
 writeFileSync(resolve(out,member.replace('.worldsim.scenario.json','.evaluation.json')),JSON.stringify(evaluation,null,2)+'\n');evaluations.push({id:scene.id,...evaluation});
}
writeFileSync(resolve(out,'scenario-audit.json'),JSON.stringify({version:1,scope:'Map passage widths, safe actor activation, crossing endpoints and permanent-blockage expectations; full swept-path feasibility still requires replay review',counts:Object.fromEntries(['reach_goal','yield_then_proceed','safe_stop'].map(mode=>[mode,evaluations.filter(e=>e.expectation===mode).length])),scenarios:evaluations},null,2)+'\n');

const counts=Object.fromEntries(['reach_goal','yield_then_proceed','safe_stop'].map(mode=>[mode,evaluations.filter(e=>e.expectation===mode).length]));
const review=['# 北京总院场景合理性审核','',`保留 ${evaluations.length} 个场景文件；有效集合 ${manifest.scenarios.length} 个，另 ${manifest.excluded.length} 个永久阻塞场景标记 INVALID 并移出通行验证集合。清单 excluded 及 evaluation.validity 保存原因、源哈希和几何证据；旧录包和失败结果不变。`,'','有效场景要求真实车身无碰撞、规划轨迹持续有效、终点距离 ≤ 0.4 m。不能因算法失败直接认定场景无效。无效场景保留原 safe_stop 判据便于历史复核，但不计入通过率。局部扫掠审核不等同于任意姿态下的不可通行数学证明。','','## 已剔除的永久阻塞场景','','| 场景 | 路宽 m | 剩余通道 m | 弯道扫掠与安全余量需求 m |','| --- | ---: | ---: | ---: |'];
for(const e of evaluations.filter(e=>e.expectation==='safe_stop')){const a=e.audit.passages.find(a=>a.blocked);review.push(`| [${e.id}](${e.id}.mineproj.json) | ${a.lane_width_m.toFixed(3)} | ${a.available_passage_m.toFixed(3)} | ${a.required_passage_m.toFixed(3)} |`);}
review.push('','## 场景生成修正与审核边界','','- 使用原始地图车道宽度采样，并估算 Ranger 车头弯道扫掠；不只比较车宽。','- 演员出生点与触发区内主车路线保持至少 3 米距离；横穿终点距主车路线至少 1.2 米。','- 永久阻塞的混合场景中，脚本行人避开合理等待区，避免演员强行走入正确停车的主车。','- 急弯横穿路线额外避开上游让行区域；优先保留原路线，仅在有交叉冲突时缩短人行道路径或调整横穿位置，出生 3 米及终点 1.2 米的距离要求保持不变。','- 连续小车改为同一通道、同方向、同速度且间距 1.4 米的连续流，避免在急弯上强加互相交叉的脚本路线。','- 扩展上游路线，尽量保留 7 米起步引导距离，避免路线截断导致车辆直接从狭窄急弯中起步。','- 原生物理碰撞检测不变；旧失败录包和输入快照保留。','- 这是几何及预期审核，不是全套通过证明；完整连续车身轨迹和动态交互仍需仿真与回放。','','每个 `.evaluation.json` 包含预期、理由、空间计算、停止区域和源文件 SHA-256。编辑场景后必须重新审核，哈希不符会拒绝沿用过期判据。');
writeFileSync(resolve(out,'SCENARIO_AUDIT.md'),review.join('\n')+'\n');
