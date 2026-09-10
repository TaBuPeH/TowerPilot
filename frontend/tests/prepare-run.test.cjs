const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../webui/index.html'),'utf8');
const source=html.slice(html.indexOf('function preparationMessage('),html.indexOf('async function stopCalibrate('));
test('complete dashboard script parses before opening setup',()=>{
  new vm.Script(html.split('<script>')[1].split('</script>')[0]);
});
test('primary setup includes battle verification and remembers automatic application',async()=>{
  const requests=[]; const saved=[];
  const ctx=vm.createContext({S:{},Date,$:()=>({textContent:''}),
    confirmPreparation:async()=>true,calibrationBusy:()=>{},pollCalibrate:()=>{},toast:()=>{},
    post:async(path,body)=>requests.push(body),localStorage:{setItem:(...args)=>saved.push(args)}});
  vm.runInContext(html.slice(html.indexOf('async function remapClient('),html.indexOf('async function startCalibrate(')),ctx);
  await vm.runInContext('remapClient()',ctx);
  assert.equal(requests.length,1);
  assert.equal(requests[0].flows,true);
  assert.deepEqual(saved,[['finishFullSetup','1']]);
});
function setup(ready){
  const calls=[];
  const ctx=vm.createContext({S:{view:'home'},$:()=>null,
    document:{querySelectorAll:()=>[]},window:{confirm:()=>false},confirmPreparation:async()=>false,
    api:async path=>path.includes('/status')?{running:false,state:{player:{card_presets:['farm']}}}:ready,
    post:async path=>{calls.push(path);return {};},
    toast:()=>{},loadStatus:async()=>{},renderHome:async()=>{},renderCalibrate:async()=>{},
    setTimeout,location:{hash:'#home'}});
  vm.runInContext(source,ctx);
  return {ctx,calls};
}
test('completed observations are applied before readiness; ready run starts no scan',async()=>{
  const {ctx,calls}=setup({ready:true});
  await vm.runInContext("prepareRun('bp_farm')",ctx);
  assert.deepEqual(calls,['/api/calibrate/apply']);
  assert.equal(ctx.S.prepareBusy,false);
  assert.match(ctx.S.prepareMessage,/Ready/);
});
test('declining temporary battle preparation sends no scan or run launch',async()=>{
  const {ctx,calls}=setup({ready:false,next_action:'battle_setup'});
  await vm.runInContext("prepareRun('bp_farm')",ctx);
  assert.deepEqual(calls,['/api/calibrate/apply']);
  assert.match(ctx.S.prepareMessage,/has not started/);
});

test('preparing missing battle controls never requests a fresh inventory scan',async()=>{
  const {ctx}=setup({ready:false});
  const requests=[];
  let started=false;
  ctx.confirmPreparation=async()=>true;
  ctx.setTimeout=fn=>fn();
  ctx.api=async path=>path.includes('/status')
    ? {running:false,state:started?{phases:{bootstrap:{status:'done'}}}:{player:{card_presets:['farm']}}}
    : {ready:started};
  ctx.post=async(path,body)=>{requests.push({path,body});if(path.endsWith('/start'))started=true;return {};};
  await vm.runInContext("prepareRun('bp_farm')",ctx);
  const request=requests.find(r=>r.path==='/api/calibrate/start');
  assert.equal(request.body.battle_only,true);
  assert.equal(request.body.fresh,undefined);
  assert.equal(request.body.overwrite,false);
  assert.match(ctx.S.prepareMessage,/Ready/);
});


test('a ready run cannot launch or prepare again while automation is running',()=>{
  const controls={'#start-preset':{value:'bp_farm'},'#run-ready':{},'#start-run':{}};
  const prepare={};
  const ctx=vm.createContext({S:{status:{processes:[{pid:123}]},runReadiness:{bp_farm:{ready:true}}},
    $:key=>controls[key],document:{querySelector:()=>prepare}});
  const start=html.indexOf('function renderRunReady(){');
  const end=html.indexOf('function ',start+10);
  vm.runInContext(html.slice(start,end).replace(/async\s*$/, ''),ctx);
  vm.runInContext('renderRunReady()',ctx);
  assert.equal(controls['#start-run'].disabled,true);
  assert.equal(prepare.disabled,true);
  assert.match(controls['#run-ready'].textContent,/Automation is running/);
});
