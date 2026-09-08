const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../webui/index.html'), 'utf8');
const section = (start, end) => html.slice(html.indexOf(start), html.indexOf(end, html.indexOf(start)));

const policies = {uw_policies: {
  farm_cl_choreo: {chain_lightning: {mode: 'fleet_marks'}},
  tourney_cl: {chain_lightning: {mode: 'off_until_wave'}},
  no_cl: {chain_lightning: {mode: 'off'}},
  baseline_only: {baseline: {black_hole: true}},
}};

test('weapon plans that schedule Chain Lightning are blocked until the account owns it', () => {
  const ctx = vm.createContext({S: {profile: {policies, player: {uws: {chain_lightning: false}}}}});
  vm.runInContext(section('function uwPlanBlocked(name){', 'function setBpPolicy('), ctx);
  assert.match(vm.runInContext('uwPlanBlocked("farm_cl_choreo")', ctx), /Chain Lightning \(mode fleet_marks\)/);
  assert.match(vm.runInContext('uwPlanBlocked("tourney_cl")', ctx), /does not own/);
  assert.equal(vm.runInContext('uwPlanBlocked("no_cl")', ctx), '');
  assert.equal(vm.runInContext('uwPlanBlocked("baseline_only")', ctx), '');
  assert.equal(vm.runInContext('uwPlanBlocked("missing_plan")', ctx), '');
  vm.runInContext('S.profile.player.uws.chain_lightning = true', ctx);
  assert.equal(vm.runInContext('uwPlanBlocked("farm_cl_choreo")', ctx), '');
  vm.runInContext('S.profile = {}', ctx);
  assert.equal(vm.runInContext('uwPlanBlocked("farm_cl_choreo")', ctx), '');
});

test('a repeated toast replaces the one already showing instead of stacking', () => {
  const box = {children: [], appendChild(d){ this.children.push(d); }};
  const mk = () => { const d = {className: '', textContent: ''}; d.remove = () => { box.children.splice(box.children.indexOf(d), 1); }; return d; };
  const ctx = vm.createContext({$: () => box, document: {createElement: mk}, setTimeout: () => {}});
  vm.runInContext(section('function toast(msg, err=false){', 'async function api('), ctx);
  vm.runInContext('toast("same verdict", true); toast("same verdict", true); toast("other")', ctx);
  assert.deepEqual(box.children.map(d => d.textContent), ['same verdict', 'other']);
});
