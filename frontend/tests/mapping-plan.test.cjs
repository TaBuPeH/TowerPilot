const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../webui/index.html'),'utf8');
const source=html.slice(html.indexOf('function renderMappingPlan('),html.indexOf('function renderInterfaceCoverage('));
test('mapping route stays vertical and distinguishes unavailable steps',()=>{
  const context=vm.createContext({esc:s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;')});
  vm.runInContext(source,context);
  const rendered=vm.runInContext(`renderMappingPlan({screens:{home:{label:'Home'},lobby:{label:'<Lobby>'}},steps:[{source:'home',destination:'lobby',status:'deferred'}]})`,context);
  assert.match(rendered,/<ol>/);
  assert.match(rendered,/Waiting for an open tournament/);
  assert.match(rendered,/&lt;Lobby&gt;/);
  assert.match(rendered,/<details open>/);
});
