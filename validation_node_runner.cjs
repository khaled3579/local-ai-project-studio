/* Native node:test reporter, plus a bounded parallel syntax-check entry point. */
const fs = require('node:fs');
const path = require('node:path');
const cp = require('node:child_process');

function atomicReport(destination, report) {
  const temporary = destination + '.' + process.pid + '.tmp';
  const fd = fs.openSync(temporary, 'w');
  try { fs.writeFileSync(fd, JSON.stringify(report)); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  fs.renameSync(temporary, destination);
}

module.exports = async function* executionReporter(source) {
  const registered = new Map();
  const successes = new Map();
  const registeredNames = new Map();
  const identities = new Map();
  function identify(data) {
    const file = path.relative(process.env.LOCAL_AI_VALIDATION_ROOT || process.cwd(), data.file || data.entryFile).split(path.sep).join('/');
    const suite = data.classname || data.details?.classname || '';
    const id = 'node:' + [file, suite, data.name].map(encodeURIComponent).join('::');
    const alias = 'node:' + data.name;
    identities.set(id, {file, suite, name: data.name, legacy: alias});
    return [id, alias];
  }
  // A bare file can be a passing implicit test in Node. Only count registered
  // test cases from per-file test-runner summaries, retaining the global status.
  for await (const event of source) {
    if (['test:pass', 'test:fail'].includes(event.type) && event.data.entryFile && event.data.details?.type !== 'suite') {
      for (const id of identify(event.data)) {
        registeredNames.set(id, (registeredNames.get(id) || 0) + 1);
      }
    }
    if (event.type === 'test:pass' && event.data.entryFile && event.data.details?.type !== 'suite' && !event.data.skip && !event.data.todo) {
      for (const id of identify(event.data)) {
        successes.set(id, (successes.get(id) || 0) + 1);
      }
    }
    if (event.type !== 'test:summary') continue;
    if (event.data.file) { registered.set(event.data.file, event.data.counts); continue; }
    const directory = process.env.LOCAL_AI_VALIDATION_DIRECTORY;
    const runId = process.env.LOCAL_AI_VALIDATION_RUN_ID;
    if (!directory || !runId) throw Error('Missing validation execution scope');
    const counts = Object.fromEntries(['tests', 'passed', 'failed', 'cancelled', 'skipped', 'todo']
      .map(key => [key, [...registered.values()].reduce((sum, item) => sum + item[key], 0)]));
    atomicReport(path.join(directory, 'node-' + process.pid + '.json'), {
      runner: 'node:test', identity_version: 2, test_identities: Object.fromEntries(identities), run_id: runId, registered_test_names: Object.fromEntries(registeredNames), successful_tests: [...successes].filter(([id, count]) => count === 1 && registeredNames.get(id) === 1).map(([id]) => id), tests: counts.tests, passed: counts.passed,
      failed: counts.failed, cancelled: counts.cancelled, skipped: counts.skipped,
      todo: counts.todo, executed: counts.tests - counts.skipped - counts.todo,
      success: event.data.success, process_counts: event.data.counts,
      registered_test_files: registered.size, seconds: event.data.duration_ms / 1000
    });
  }
};

async function syntax(specFile) {
  const spec = JSON.parse(fs.readFileSync(specFile, 'utf8'));
  const errors = [];
  let cursor = 0;
  async function worker() {
    for (;;) {
      const index = cursor++;
      if (index >= spec.files.length) return;
      const relative = spec.files[index];
      const target = path.resolve(spec.root, relative);
      if (!target.startsWith(path.resolve(spec.root) + path.sep)) throw Error('Invalid syntax target');
      const result = await new Promise(resolve => {
        const child = cp.spawn(process.execPath, ['--check', target], {
          cwd: spec.root, windowsHide: true, env: { ...process.env, NODE_OPTIONS: '' }
        });
        let output = '', timedOut = false;
        const timer = setTimeout(() => { timedOut = true; child.kill(); }, 15000);
        const append = data => { output = (output + data.toString('utf8')).slice(-6000); };
        child.stdout.on('data', append); child.stderr.on('data', append);
        child.once('error', error => { clearTimeout(timer); resolve({ code: -1, output: String(error) }); });
        child.once('close', code => { clearTimeout(timer); resolve({ code, output, timedOut }); });
      });
      if (result.code !== 0) errors.push({ path: relative, stderr: result.output, timed_out: !!result.timedOut });
    }
  }
  await Promise.all(Array.from({ length: Math.min(4, spec.files.length) }, worker));
  atomicReport(spec.report, { runner: 'node:syntax', run_id: spec.run_id, files: spec.files.length,
                             errors, success: errors.length === 0 });
  process.exitCode = errors.length ? 1 : 0;
}

if (require.main === module) {
  syntax(process.argv[2]).catch(error => { console.error(error); process.exitCode = 1; });
}
