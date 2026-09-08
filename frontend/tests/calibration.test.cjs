const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../webui/index.html'), 'utf8');
const section = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));

test('screen capture has separate persisted feedback and blocks duplicate starts', () => {
  const progress = {}, buttons = [{}, {}];
  const ctx = vm.createContext({$:()=>progress, document:{querySelectorAll:()=>buttons},
    calibrationMode:st=>st.selection?.mode});
  vm.runInContext(section('function observationFeedback(', 'async function observeNow('), ctx);
  ctx.st = {running:true,selection:{mode:'observe'},state:{observation:{status:'running',message:'Reading Home'}}};
  vm.runInContext('observationFeedback(st)',ctx);
  assert.match(progress.textContent,/running.*Reading Home/);
  assert.ok(buttons.every(b=>b.disabled));
  ctx.st = {running:false,state:{observation:{status:'done',message:'Captured 2'}}};
  vm.runInContext('observationFeedback(st)',ctx);
  assert.match(progress.textContent,/finished.*Captured 2/);
  assert.ok(buttons.every(b=>!b.disabled));
  ctx.st.state.observation.status = 'running';
  vm.runInContext('observationFeedback(st)',ctx);
  assert.match(progress.textContent,/stopped before completion/);
});

test('polling survives an initial stopped response and a connection failure without rebuilding the form', async () => {
  let calls = 0, next, reports = [];
  const progress = {};
  const ctx = vm.createContext({S:{}, $:()=>progress,
    clearTimeout:()=>{}, setTimeout:fn=>{next=fn;},
    api:async()=>{ calls++; if(calls === 2) throw Error('offline'); return {running:calls>2}; },
    calibReport:st=>reports.push(st.running)});
  vm.runInContext(section('function pollCalibrate(){', 'function cropRefresh()'),ctx);
  await vm.runInContext('pollCalibrate()',ctx);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(reports,[false]);
  await next();
  assert.match(progress.textContent,/Retrying/);
  await next();
  assert.deepEqual(reports,[false,true]);
});

test('selections survive re-render, reload, and switching between accounts', () => {
  const stored = new Map();
  const controls = {'#calib-taps':{checked:true},'#calib-over':{checked:true}};
  const ctx = vm.createContext({S:{config:{active_instance:'main',instances:{main:{account:'alice',serial:'one'}}}},
    $:id=>controls[id],document:{querySelectorAll:()=>[{value:'g'},{value:'w'}]},
    localStorage:{getItem:key=>stored.get(key),setItem:(key,v)=>stored.set(key,v)}});
  vm.runInContext(section('function calibrationPreferences(){','async function renderCalibrate(){'),ctx);
  vm.runInContext('rememberCalibration(); S.calibrationChoices = {};',ctx);
  assert.equal(vm.runInContext('calibrationPreferences().phases.join(",")',ctx),'g,w');
  assert.equal(vm.runInContext('calibrationPreferences().overwrite',ctx),true);
  vm.runInContext('S.config.instances.main.account="bob"',ctx);
  assert.equal(vm.runInContext('calibrationPreferences().phases.join(",")',ctx),'c,m');
  vm.runInContext('S.config.instances.main.account="alice"',ctx);
  assert.equal(vm.runInContext('calibrationPreferences().phases.join(",")',ctx),'g,w');
});


test('saved knowledge stays separate from unchecked next-scan choices and image verification', () => {
  const ctx = vm.createContext({esc:v=>String(v), S:{calibrationChoices:{}}});
  vm.runInContext(section('function calibrationKnowledge(st){', 'async function renderCalibrate(){'),ctx);
  ctx.status = {state:{phases:{global:{status:'done',results:{global_presets:['Example']}}}},
    report:{entries:[{phase:'global',status:'stale',verified:false},{phase:'global',status:'exists'}]}};
  const rendered = vm.runInContext('calibrationKnowledge(status)',ctx);
  assert.match(rendered,/Scanned/);
  assert.match(rendered,/Example/);
  assert.match(rendered,/1 need attention/);
  assert.match(rendered,/1 not yet verified/);
  assert.match(rendered,/Not scanned/);
  assert.doesNotMatch(rendered,/checkbox/);
});


test('fresh setup redirects direct Control links until the scan gate passes', async () => {
  const node = {classList:{toggle:()=>{}},setAttribute:()=>{}};
  const location = {hash:'#home'};
  let rendered;
  const ctx = vm.createContext({S:{status:{setup_complete:true,calibration:{ready:false}}},
    $:()=>node, location, history:{replaceState:(_a,_b,v)=>location.hash=v},
    window:{addEventListener:()=>{}}, syncConnectOverlay:()=>{},
    renderCalibrate:async()=>{rendered='calibrate'},renderSetup:async()=>{rendered='setup'},renderHome:async()=>{rendered='home'}});
  vm.runInContext(section('function calibOk(){','/* ------------------------------------------------- connect overlay'),ctx);
  assert.equal(vm.runInContext('defaultView()',ctx),'calibrate');
  await vm.runInContext('route()',ctx);
  assert.equal(location.hash,'#calibrate');
  assert.equal(rendered,'calibrate');
  vm.runInContext('S.status.calibration.ready=true',ctx);
  location.hash='#home';
  await vm.runInContext('route()',ctx);
  assert.equal(rendered,'home');
  vm.runInContext('S.status.setup_complete=false',ctx);
  await vm.runInContext('route()',ctx);
  assert.equal(rendered,'setup');
});

test('progress counts completed and skipped steps, retains history, and escapes game text',()=>{
  const ctx=vm.createContext({esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')});
  vm.runInContext(section('function scanTimelineHTML(', 'function renderScanTimeline('),ctx);
  ctx.record={status:'running',message:'Reading <name>',steps:[
    {id:'a',label:'Home',status:'done'}, {id:'b',label:'Guild',status:'skipped'},
    {id:'c',label:'Modules',status:'running'}, {id:'d',label:'Save',status:'pending'}
  ],history:[{at:1,message:'Home verified'},{at:2,message:'Read <script>'}]};
  const html=vm.runInContext('scanTimelineHTML(record,true)',ctx);
  assert.match(html,/value="2" max="4"/);
  assert.match(html,/aria-current="step"/);
  assert.match(html,/1 skipped/);
  assert.match(html,/Home verified/);
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<script>/);
  const interrupted=vm.runInContext('scanTimelineHTML(record,false)',ctx);
  assert.match(interrupted,/Stopped before completion/);
  assert.doesNotMatch(interrupted,/aria-current="step"/);
  assert.match(interrupted,/scan-step-stopped/);
});

test('old scan reports do not invent a detailed step history',()=>{
  const ctx=vm.createContext({esc:String});
  vm.runInContext(section('function scanTimelineHTML(', 'function renderScanTimeline('),ctx);
  const html=vm.runInContext('scanTimelineHTML({status:"done",completed:16,total:16})',ctx);
  assert.match(html,/next scan/);
  assert.doesNotMatch(html,/<progress/);
});

test('basic scan uses actual phase history and ignores previous starter activity',()=>{
  const ctx=vm.createContext({});
  vm.runInContext(section('function calibrationMode(', 'function calibReport('),ctx);
  ctx.st={running:true,selection:{mode:'basic',phases:['c','m']},state:{phases:{cards:{status:'done',started_at:1,finished_at:3},modules:{status:'running',started_at:4},bootstrap:{status:'done'}}},activity:{kind:'calibrate_cut',name:'Example'}};
  const p=vm.runInContext('basicScanProgress(st)',ctx);
  assert.equal(p.steps.length,2);
  assert.equal(p.active_step,'modules');
  assert.equal(p.history.length,3);
  assert.equal(p.message,'Checking Example');
});

test('asset summary separates extracted artwork, mappings, and verified screen appearances',()=>{
  const ctx=vm.createContext({esc:s=>String(s).replaceAll('<','&lt;'), rowState:()=>'', artPair:()=>''});
  vm.runInContext(section('function assetLibraryHTML(', 'function calibrationMode('),ctx);
  ctx.assets={version:'1',images:120,unique_images:100,mapped:4,verified:1,empty_placeholders:2,targets:[{rel:'home/icon.png',names:['Icon'],verification:'pending'}]};
  const html=vm.runInContext('assetLibraryHTML(assets)',ctx);
  assert.match(html,/120 image objects extracted/);
  assert.match(html,/4 manifest targets mapped/);
  assert.match(html,/1 verified on screen/);
  assert.match(html,/empty texture placeholders/);
  assert.match(html,/Awaiting screen verification/);
});

test('card inventory count is independent of preset and reference counts', () => {
  const ctx=vm.createContext({esc:v=>String(v)});
  vm.runInContext(section('function calibrationKnowledge(st){','async function renderCalibrate(){'),ctx);
  ctx.status={state:{phases:{cards:{status:'done',results:{names:['First','Second']}}}},
    report:{entries:[]},card_manifest:{complete:true,cards:Array.from({length:35},(_,i)=>({name:'Card '+(i+1)}))}};
  const rendered=vm.runInContext('calibrationKnowledge(status)',ctx);
  assert.match(rendered,/35 cards identified; full inventory verified; 2 presets/);
  assert.match(rendered,/Card 35/);
});

test('card inventory surfaces active and mastery counts and per-card marks', () => {
  const ctx=vm.createContext({esc:v=>String(v)});
  vm.runInContext(section('function calibrationKnowledge(st){','async function renderCalibrate(){'),ctx);
  ctx.status={state:{phases:{cards:{status:'done',results:{names:['P1']}}}},
    report:{entries:[]},card_manifest:{complete:true,active:22,mastered:5,
      cards:[{name:'Damage'},{name:'Nuke',active:true},{name:'Intro Sprint',active:true,mastery:true}]}};
  const rendered=vm.runInContext('calibrationKnowledge(status)',ctx);
  assert.match(rendered,/3 cards identified; full inventory verified; 1 presets; 22 active; 5 mastered/);
  assert.match(rendered,/Nuke ✓/);
  assert.match(rendered,/Intro Sprint ✓ ★/);
});

test('starter scan consent gate posts flows=true on OK and flows=false on Cancel', async () => {
  const run = async (confirmReturns) => {
    let body = null;
    const progress = {};
    const ctx = vm.createContext({
      S:{}, window:{confirm:()=>confirmReturns}, Date:{now:()=>1},
      rememberCalibration:()=>{}, calibrationBusy:()=>{}, pollCalibrate:()=>{},
      toast:()=>{},
      $:id=> id==='#bootstrap-progress' ? progress : {checked:false},
      post:async(url,b)=>{ body=b; return {}; },
    });
    vm.runInContext(section('async function startBootstrap(){', 'async function startCalibrate('), ctx);
    await vm.runInContext('startBootstrap()', ctx);
    return body;
  };
  const ok = await run(true);
  assert.equal(ok.bootstrap, true);
  assert.equal(ok.flows, true);
  const cancelled = await run(false);
  assert.equal(cancelled.bootstrap, true);
  assert.equal(cancelled.flows, false);
});
