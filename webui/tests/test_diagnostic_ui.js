/** Run with node tests/test_diagnostic_ui.js; no device requests are made. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const elements = {
    diagLteCheckBtn: { disabled: true },
    diagOutput: { textContent: 'Waiting for command...' },
    statusAlert: { classList: { add() {}, remove() {} } },
  };
  const requests = [];
  const replies = [];
  const context = vm.createContext({
    document: { getElementById: id => elements[id], addEventListener() {} },
    window: { location: {} },
    setTimeout() {},
    fetch: async (url, options) => {
      requests.push({ url, options });
      assert.ok(replies.length, 'Unexpected request');
      const reply = replies.shift();
      if (reply instanceof Error) throw reply;
      return { ok: true, status: 200, json: async () => reply };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
  return { context, elements, requests, replies };
}

async function main() {
  const test = setup();
  await test.context.diagLteCheck();
  assert.equal(test.requests.length, 0, 'No diagnostic request before presence is confirmed');
  test.replies.push({ present: false });
  await test.context.refreshDiagnosticLteAvailability();
  assert.equal(test.elements.diagLteCheckBtn.disabled, true);
  assert.match(test.elements.diagLteCheckBtn.title, /not installed/);
  assert.equal(test.requests[0].url, '/api/lte/presence');
  assert.equal(test.requests[0].options.cache, 'no-store');
  await test.context.diagLteCheck();
  assert.equal(test.requests.length, 1, 'Absent modem must not send AT-check requests');

  test.replies.push({ present: true });
  await test.context.refreshDiagnosticLteAvailability();
  assert.equal(test.elements.diagLteCheckBtn.disabled, false);
  test.replies.push({ present: true }, { output: 'Modem OK' }, { present: true });
  const first = test.context.diagLteCheck();
  assert.equal(test.elements.diagLteCheckBtn.disabled, true);
  await test.context.diagLteCheck();
  await first;
  const posts = test.requests.filter(request => request.url === '/api/diagnostic/lte-check');
  assert.equal(posts.length, 1, 'Prevent duplicate diagnostic checks');
  assert.match(test.elements.diagOutput.textContent, /Modem OK/);
  assert.equal(test.elements.diagLteCheckBtn.disabled, false);

  test.replies.push({ present: false }, { present: false });
  await test.context.diagLteCheck();
  assert.equal(test.elements.diagLteCheckBtn.disabled, true);
  assert.equal(test.requests.filter(request => request.url === '/api/diagnostic/lte-check').length, 1,
    'Removal before a check must not send a diagnostic request');

  test.replies.push(new Error('offline'));
  await test.context.refreshDiagnosticLteAvailability();
  assert.equal(test.elements.diagLteCheckBtn.disabled, true);
  test.context.updateDiagnosticLtePresence(true);
  assert.equal(test.elements.diagLteCheckBtn.disabled, false);
  test.context.updateDiagnosticLtePresence(false);
  assert.equal(test.elements.diagLteCheckBtn.disabled, true);
  console.log('Diagnostic LTE availability checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
