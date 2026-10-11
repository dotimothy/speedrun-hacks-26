// Real HTTP integration, with real rules and session state, without model weights.
// Run: PYTHONPATH=backend V2_PYTHON=/path/to/python node frontend/tests/v2.integration.cjs
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const ts = require('../node_modules/typescript');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const filename = path.resolve(__dirname, '../src/workspace/v2.ts');
const compiled = new Module(filename, module);
compiled._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 }
}).outputText, filename);
const { appendUtterance, adaptSnapshot, reviewOperation, ApiConflict } = compiled.exports;
const api = spawn(process.env.V2_PYTHON || 'python3', ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '0'], {
  env: { ...process.env, DISPATCHIQ_MOCK: '1' }, stdio: ['ignore', 'pipe', 'pipe']
});
let logs = '';
let stop;
const closed = new Promise(resolve => api.once('exit', resolve));
function cleanup() { if (api.exitCode === null) api.kill('SIGTERM'); }
process.on('SIGINT', cleanup); process.on('SIGTERM', cleanup);
const timeout = setTimeout(() => { console.error('Integration timeout'); cleanup(); process.exitCode = 1; }, 25000);
const actualFetch = globalThis.fetch;
(async () => {
  const origin = await new Promise((resolve, reject) => {
    stop = setTimeout(() => reject(new Error(`API startup timed out: ${logs}`)), 8000);
    api.on('error', reject);
    api.once('exit', code => reject(new Error(`API exited ${code}: ${logs}`)));
    api.stderr.on('data', chunk => {
      logs += chunk;
      const match = logs.match(/Uvicorn running on (http:\/\/127\.0\.0\.1:\d+)/);
      if (match) { clearTimeout(stop); resolve(match[1]); }
    });
  });
  globalThis.fetch = (url, options) => actualFetch(new URL(url, origin), options);
  const id = `integration-${crypto.randomUUID()}`;
  const signal = AbortSignal.timeout(15000);
  const first = { input: 'We are at Oak and Third', clientId: 'first', speaker: 'caller' };
  let snapshot = await appendUtterance(id, first, signal);
  let session = adaptSnapshot(snapshot, 'transcript');
  assert.equal(session.segments.length, 1);
  assert.equal(session.record.location, undefined);
  const proposal = session.suggestions.find(s => s.field === 'location' && s.status === 'pending');
  assert.ok(proposal);
  const accept = reviewOperation(session, { type: 'review', id: proposal.id, decision: 'accept' });
  snapshot = await accept(); session = adaptSnapshot(snapshot, 'transcript');
  assert.equal(session.record.location, 'Oak and Third');
  const revision = session.revision;
  assert.equal((await accept()).revision, revision, 'same operation must not apply twice');
  const duplicate = await appendUtterance(id, first, signal);
  assert.equal(duplicate.segments.length, 1, 'utterance retry must not duplicate transcript');
  snapshot = await appendUtterance(id, { input: 'Sorry, Fourth, not Third', clientId: 'correction', speaker: 'caller' }, signal);
  session = adaptSnapshot(snapshot, 'transcript');
  assert.equal(session.record.location, 'Oak and Third');
  const correction = session.suggestions.find(s => s.field === 'location' && s.status === 'pending');
  assert.equal(correction.kind, 'correction');
  assert.equal(correction.value, 'Oak and Fourth');
  session = adaptSnapshot(await reviewOperation(session, { type: 'review', id: correction.id, decision: 'accept' })(), 'transcript');
  session = adaptSnapshot(await reviewOperation(session, { type: 'publish', note: '' })(), 'transcript');
  assert.equal(session.published.record.location, 'Oak and Fourth');
  assert.match(session.published.note, /Oak and Fourth/);
  const published = session.published;
  const suggestionCount = session.suggestions.length;
  snapshot = await appendUtterance(id, { input: 'Sorry, Fifth, not Fourth', clientId: 'operator-question', speaker: 'operator' }, signal);
  assert.equal(snapshot.suggestions.length, suggestionCount, 'operator speech must not produce caller corrections');
  session = adaptSnapshot(snapshot, 'transcript');
  const stale = session;
  session = adaptSnapshot(await reviewOperation(session, { type: 'manual', field: 'location', value: 'Operator updated location' })(), 'transcript');
  assert.deepEqual(session.published, published, 'working edits must not change published snapshot');
  await assert.rejects(reviewOperation(stale, { type: 'publish', note: '' })(), ApiConflict);
  const inbox = await (await fetch(`/api/v2/dispatch/inbox/${id}`)).json();
  assert.equal(inbox.publications.length, 1);
  assert.equal(inbox.publications[0].record.location_text, 'Oak and Fourth');
  snapshot = await appendUtterance(id, { input: 'We are at Oak and Sixth', clientId: 'conflict', speaker: 'caller' }, signal);
  session = adaptSnapshot(snapshot, 'transcript');
  const conflict = session.suggestions.find(s => s.field === 'location' && s.status === 'pending' && s.kind === 'conflict');
  assert.ok(conflict, 'contradictory location must offer explicit choices');
  assert.ok(conflict.options.length >= 2);
  const choice = conflict.options.find(o => o.value === 'Oak and Sixth');
  assert.ok(choice, JSON.stringify(conflict.options));
  session = adaptSnapshot(await reviewOperation(session, { type: 'resolve', id: conflict.id, option: choice.id })(), 'transcript');
  assert.equal(session.record.location, 'Oak and Sixth');
  console.log('PASS: real v2 HTTP create → append → accept → retry → correct → accept → publish → inbox; stale publication rejected. Model inference not exercised.');
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  globalThis.fetch = actualFetch;
  clearTimeout(timeout); clearTimeout(stop); cleanup(); await closed;
});
