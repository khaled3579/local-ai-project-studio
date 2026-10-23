const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function load() {
  const nodes = new Map();
  class Element {
    constructor() { this.children = []; this.textContent = ''; }
    set id(value) { this._id = value; nodes.set(value, this); }
    get id() { return this._id; }
    setAttribute() {}
    insertAdjacentElement() {}
    remove() {}
    append(...children) { this.children.push(...children); }
  }
  const get = id => { if (!nodes.has(id)) { const item = new Element(); item.id = id; } return nodes.get(id); };
  let context;
  context = vm.createContext({
    state: null, token: 'test', $: get,
    document: {createElement: () => new Element()},
    window: {addEventListener() {}, console},
    render: value => { context.state = value; },
    fetch: () => new Promise(() => {}), // No network or reconnect timers in this unit test.
    AbortController, console,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'project_live_client.js'), 'utf8'), context);
  return {context, get};
}

test('renders measured task tokens and distinguishes totals from context size', () => {
  const {context, get} = load();
  context.render({project: 'p', job: {id: 'j', token_usage: {requests: 3, measured_requests: 2, prompt_tokens: 12000, completion_tokens: 400, incomplete_requests: 1}}});
  assert.match(get('tokenUsage').textContent, /12,000/);
  assert.match(get('tokenUsage').textContent, /400/);
  assert.match(get('tokenUsage').title, /2 \/ 3/);
  assert.equal(get('tokenUsage').hidden, false);
});

test('unavailable runtime usage is not displayed as zero consumption', () => {
  const {context, get} = load();
  context.render({project: 'p', job: {token_usage: {requests: 1, measured_requests: 0}}});
  assert.match(get('tokenUsage').textContent, /غير متاح/);
});

test('switching project clears the previous task totals', () => {
  const {context, get} = load();
  context.render({project: 'p', job: {token_usage: {requests: 1, measured_requests: 1, prompt_tokens: 123, completion_tokens: 5}}});
  context.render({project: 'other', job: null});
  assert.equal(get('tokenUsage').hidden, true);
  assert.equal(get('tokenUsage').textContent, '');
});


test('acceptance evidence counts only mapped passing requirements and clears on switch', () => {
  const {context, get} = load();
  context.render({project:'p', job:{delivery_evidence:{validated:false, requirements_coverage:{items:[{passed:true},{passed:false}]}}}});
  assert.equal(get('acceptanceEvidence').disabled, false);
  assert.match(get('acceptanceEvidence').textContent, /1\/2/);
  context.render({project:'other', job:null});
  assert.equal(get('acceptanceEvidence').disabled, true);
  assert.equal(get('acceptanceEvidence').textContent, 'أدلة القبول');
});
