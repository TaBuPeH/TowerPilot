const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(require('node:path').join(__dirname,'../webui/index.html'),'utf8');
const source=html.slice(html.indexOf('let clientClickerGeneration'),html.indexOf('async function observeNow('));
function setup(post){
  const status={}, history={children:[],prepend(row){this.children.unshift(row);}};
  const nodes={'#clicker-status':status,'#clicker-history':history,'#clicker-action':{value:'guild'},'#clicker-preview':{style:{}}};
  const ctx=vm.createContext({S:{config:{active_instance:'main',instances:{main:{account:'test'}}}},
    $:key=>nodes[key], location:{hash:'#calibrate'}, post, setTimeout,
    document:{hidden:false,querySelectorAll:()=>[],createElement:()=>({}),addEventListener:()=>{}},
    window:{addEventListener:()=>{}}});
  vm.runInContext(source,ctx);
  return ctx;
}
test('preview never submits an execute request',async()=>{
  const requests=[];
  const ctx=setup(async(_url,body)=>{requests.push(body);return {result:{ok:true,reason:'verified'}};});
  await vm.runInContext("clientClicker('preview')",ctx);
  assert.deepEqual(requests.map(r=>r.execute),[false]);
});
test('uncertain click outcome stops instead of retrying',async()=>{
  const requests=[];
  const ctx=setup(async(_url,body)=>{requests.push(body);return {result:{ok:!body.execute,reason:'result'}};});
  await vm.runInContext("clientClicker('watch')",ctx);
  assert.deepEqual(requests.map(r=>r.execute),[false,true]);
});
test('Stop during preview prevents the later execute request',async()=>{
  let resolve, calls=0;
  const ctx=setup(()=>{calls++;return new Promise(r=>resolve=r);});
  const running=vm.runInContext("clientClicker('watch')",ctx);
  vm.runInContext('stopClientClicker()',ctx);
  resolve({result:{ok:true,reason:'verified'}});
  await running;
  assert.equal(calls,1);
  assert.equal(ctx.S.clientClickerBusy,false);
});
