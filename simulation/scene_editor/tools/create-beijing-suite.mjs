// Run from scene_editor/tools. Uses the editor's map parser and WorldSim exporter.
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseApolloBaseMapText } from '../src/map/loaders/apolloBaseMapText.ts';
import { buildWorldSimScenarioPayload } from '../src/scenarios/exportWorldSimScenario.ts';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const mapId = 'beijing_zongyuan_1haolou';
const source = process.argv[2];
if (!source) throw new Error('Usage: node tools/create-beijing-suite.mjs /path/to/beijing_zongyuan_1haolou/base_map.txt');
const map = parseApolloBaseMapText(readFileSync(source, 'utf8'), mapId);
map.id = mapId;
const origin = map.meta.origin;
const lane = map.lanes.find(l => l.id === 'Lane_9');
if (!lane || lane.centerline.length < 2) throw new Error('Required straight Lane_9 is absent');
const points = lane.centerline.map(p => ({x:p.x+origin.x,y:p.y+origin.y,z:p.z+origin.z}));
const lengths = [0];
for (let i=1;i<points.length;i++) lengths.push(lengths.at(-1)+Math.hypot(points[i].x-points[i-1].x,points[i].y-points[i-1].y));
function pose(s, lateral=0) {
  const i = lengths.findIndex((d,i) => i>0 && d>=s);
  if(i<1) throw new Error(`Lane_9 is too short for ${s} m`);
  const a=points[i-1],b=points[i],t=(s-lengths[i-1])/(lengths[i]-lengths[i-1]);
  const heading=Math.atan2(b.y-a.y,b.x-a.x);
  return {position:{x:a.x+t*(b.x-a.x)-Math.sin(heading)*lateral,y:a.y+t*(b.y-a.y)+Math.cos(heading)*lateral,z:0},heading};
}
function route(id, coords, speed, heading=false) {
  return {id,name:id,pathType:'polyline',waypoints:coords.map(([s,l=0],i)=>({id:`${id}-${i}`,position:pose(s,l).position,speed,...(heading?{heading:pose(s,l).heading}:{})}))};
}
function actor(id,type,coords,speed,enabled=false) {
  const r=route(`${id}-route`,coords,speed,type==='ego');
  const a=r.waypoints[0].position,b=r.waypoints[1].position;
  return {id,name:id,type,position:a,heading:Math.atan2(b.y-a.y,b.x-a.x),speed,enabled,
    size:type==='pedestrian'?{x:0.4,y:0.4,z:1.7}:{x:0.72,y:0.5,z:0.8},
    color:type==='ego'?'#B894F6':'#F5B654',activeRouteId:r.id,routes:[r]};
}
function trigger(id,s,actions) {
  return {id,name:id,type:'location',...pose(s),center:pose(s).position,
    size:{x:1.5,y:3,z:3},targetAgentId:'ego',actions};
}
function enable(id) {return {kind:'enable',targetAgentId:id};}
function scenario(id,name,agents=[],triggers=[]) {
  return {id:`beijing-${id}`,name,description:'Lane_9 / ENU；位置触发，固定路径可重复回放。',duration:70,mapId,
    agents:[actor('ego','ego',[[3],[33]],0,true),...agents],triggers};
}
const cases = [
  ['straight',scenario('straight','直行 · 无障碍')],
  ['moving_lead',scenario('moving-lead','动态车辆 · 慢速前车',
    [actor('lead','vehicle',[[12],[36]],0.55,true)])],
  ['crossing_vehicle',scenario('crossing-vehicle','动态车辆 · 位置触发横穿',
    [actor('cross-car','vehicle',[[18,2.5],[18,-2.5]],0.6)],
    [trigger('ego-enters-crossing-zone',11,[enable('cross-car')])])],
  ['pedestrian_sudden',scenario('pedestrian-sudden','突发行人 · 近距离出现',
    [actor('sudden','pedestrian',[[18,1.2],[18,-2.5]],0.9)],
    [trigger('near-pedestrian-zone',14,[enable('sudden')])])],
  ['pedestrian_left',scenario('pedestrian-left','行人横穿 · 左侧出现',
    [actor('left','pedestrian',[[18,2],[18,-2.5]],0.65)],
    [trigger('left-crossing-zone',11,[enable('left')])])],
  ['pedestrian_right',scenario('pedestrian-right','行人横穿 · 右侧出现',
    [actor('right','pedestrian',[[18,-2],[18,2.5]],0.7)],
    [trigger('right-crossing-zone',12,[enable('right')])])],
  ['pedestrian_wandering',scenario('pedestrian-wandering','游走行人 · 多次折返',
    [actor('walker','pedestrian',[[16,2.2],[17,-1.6],[20,1.6],[23,-1.5],[26,2.5]],0.65)],
    [trigger('wandering-start-zone',9,[enable('walker')])])],
  ['multi_trigger',scenario('multi-trigger','连续位置触发 · 两次横穿与变速',
    [actor('first','pedestrian',[[13,2],[13,-2.5]],0.7),
     actor('second','pedestrian',[[25,-2],[25,2.5]],0.55)],
    [trigger('first-zone',7,[enable('first')]),trigger('second-zone',19,[enable('second')]),
     trigger('speed-change-zone',22,[{kind:'set_speed',targetAgentId:'second',speed:0.9}])])],
];
const out=resolve(root,'examples',mapId);
// Twelve behavioral families, ten parameter combinations each. These alter
// geometry/timing/motion, not just filenames. Original eight remain regressions.
const families=['clear','cross_left','cross_right','sudden','far_cross','wander',
  'diagonal','two_crossings','opposing_pedestrians','slow_lead','cross_vehicle','side_nudge'];
for (const [familyIndex,family] of families.entries()) for(let variant=0;variant<10;variant++) {
  const id=`${family}_${String(variant+1).padStart(2,'0')}`;
  const s=13+variant*.85, side=variant%2 ? -1 : 1;
  const speed=.3+variant*.075, release=s-(2.3+(variant%5)*1.2);
  let agents=[],triggers=[];
  const add=(name,type,coords,v,at)=>{
    agents.push(actor(name,type,coords,v));
    triggers.push(trigger(`${name}-position`,at,[enable(name)]));
  };
  switch(family) {
    case 'clear': break;
    case 'cross_left': add('walker','pedestrian',[[s,1.3+variant*.08],[s,-2.6]],speed,release);break;
    case 'cross_right': add('walker','pedestrian',[[s,-1.3-variant*.08],[s,2.6]],speed,release);break;
    case 'sudden': add('walker','pedestrian',[[s,side*(.65+variant*.035)],[s,-side*2.6]],speed,s-2.1-variant*.09);break;
    case 'far_cross': add('walker','pedestrian',[[s+6,side*1.5],[s+6,-side*2.6]],speed,4+variant*.1);break;
    case 'wander': add('walker','pedestrian',[[s,side*1.5],[s+1,-side*1.2],[s+3,side*1.2],[s+5,-side*1.5],[s+6,side*2.6]],speed,release);break;
    case 'diagonal': add('walker','pedestrian',[[s,side*1.6],[s+3+variant*.15,-side*2.6]],speed,release);break;
    case 'two_crossings':
      add('first','pedestrian',[[s-3,side*1.5],[s-3,-side*2.6]],speed,Math.max(4,release-3));
      add('second','pedestrian',[[s+7,-side*1.5],[s+7,side*2.6]],speed*.8,release+7);break;
    case 'opposing_pedestrians':
      add('first','pedestrian',[[s,1.8],[s,-2.6]],speed,release);
      add('second','pedestrian',[[s+1.5,-1.8],[s+1.5,2.6]],speed*.9,release+.8);break;
    case 'slow_lead':
      add('lead','vehicle',[[s,side*.15],[39,side*.15]],.35+variant*.04,4);
      triggers.push(trigger('lead-accelerates',s+3,[{kind:'set_speed',targetAgentId:'lead',speed:.9}]));break;
    case 'cross_vehicle': add('vehicle','vehicle',[[s,side*2.6],[s,-side*2.6]],speed,release);break;
    case 'side_nudge':
      add('walker','pedestrian',[[s,side*(.35+variant*.025)],[s+1,side*2.6]],0,release);
      // A narrow but finite obstruction: position activates it; timed departure
      // lets the car choose a safe nudge or wait without an impossible deadlock.
      triggers.push({id:'pedestrian-leaves',name:'pedestrian-leaves',type:'time',time:22+variant,
        actions:[{kind:'set_speed',targetAgentId:'walker',speed:.55}]});break;
  }
  const scene=scenario(id,`${family} · ${variant+1}`,agents,triggers);
  scene.agents[0]=actor('ego','ego',[[2.5+(variant%4)*.25],[32+(variant%5)]],0,true);
  scene.duration=90;
  scene.description=`Family ${familyIndex+1}/12; variant ${variant+1}/10; location-triggered deterministic held-out WorldSim regression.`;
  cases.push([id,scene]);
}
mkdirSync(out,{recursive:true});
for(const [id,scene] of cases) {
  const project={kind:'mine-project',version:1,name:scene.name,mapSource:'embedded',map:{name:mapId,data:map},scenario:scene,savedAt:'2026-09-29T00:00:00.000Z'};
  writeFileSync(resolve(out,`${id}.mineproj.json`),JSON.stringify(project,null,2)+'\n');
  writeFileSync(resolve(out,`${id}.worldsim.scenario.json`),JSON.stringify(buildWorldSimScenarioPayload(scene),null,2)+'\n');
}
writeFileSync(resolve(out,'beijing_zongyuan_1haolou.suite.json'),JSON.stringify({kind:'worldsim-suite',version:1,
  name:`Beijing 1haolou · ${cases.length} scenarios`,mapId,scenarios:cases.map(([id])=>`${id}.worldsim.scenario.json`)},null,2)+'\n');
mkdirSync(resolve(root,'public/maps',mapId),{recursive:true});
copyFileSync(source,resolve(root,'public/maps',mapId,'base_map.txt'));
console.log(`Created ${cases.length} editable projects, WorldSim exports and suite on ${mapId}`);
