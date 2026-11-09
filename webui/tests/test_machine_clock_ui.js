/** Run with node tests/test_machine_clock_ui.js; no real clocks, timers or HTTP calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const sample = {
  unix_ms: Date.UTC(2026, 10, 9, 0, 0, 0), utc_offset_seconds: 8 * 3600, timezone: 'UTC+08',
};

function setup() {
  let now = 0;
  let id = 0;
  const timers = new Map();
  const intervals = new Map();
  const events = {};
  const element = {};
  const requests = [];
  const pending = [];
  const document = {
    hidden: false, getElementById: () => element,
    addEventListener: (name, callback) => { events[name] = callback; },
  };
  const window = {
    location: {}, addEventListener: (name, callback) => { events[name] = callback; },
  };
  const context = vm.createContext({
    document, window, AbortController, performance: { now: () => now },
    setTimeout: (fn, delay) => { timers.set(++id, { fn, delay }); return id; },
    clearTimeout: key => timers.delete(key),
    setInterval: (fn, delay) => { intervals.set(++id, { fn, delay }); return id; },
    clearInterval: key => intervals.delete(key),
    fetch: (url, options) => {
      requests.push({ url, options });
      return new Promise((resolve, reject) => {
        pending.push({ resolve, reject });
        options.signal.addEventListener('abort', () => reject(new Error('Aborted')));
      });
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/machine_clock.js'), 'utf8'), context);
  return {
    context, document, window, events, timers, intervals, element, requests,
    advance: ms => { now += ms; for (const timer of intervals.values()) timer.fn(); },
    complete: async (data = sample, response = {}) => {
      pending.shift().resolve({ ok: true, status: 200, json: async () => data, ...response });
      await flush();
    },
    fail: async () => { pending.shift().reject(new Error('Offline')); await flush(); },
    fire: async delay => {
      const entry = [...timers].find(([, timer]) => timer.delay === delay);
      assert.ok(entry, `Missing ${delay}ms timer`);
      timers.delete(entry[0]);
      entry[1].fn();
      await flush();
    },
  };
}

async function main() {
  const clock = setup();
  clock.events.DOMContentLoaded();
  clock.events.pageshow();
  clock.context.startMachineClock();
  assert.equal(clock.requests.length, 1, 'Start once despite multiple lifecycle events');
  assert.equal(clock.intervals.size, 1);
  assert.equal([...clock.intervals.values()][0].delay, 1000);
  assert.equal(clock.requests[0].url, '/api/diagnostic/time');
  assert.equal(clock.requests[0].options.cache, 'no-store');
  await clock.complete();
  assert.equal(clock.element.textContent, '2026-11-09 08:00:00 UTC+08');
  clock.advance(1000);
  assert.equal(clock.element.textContent, '2026-11-09 08:00:01 UTC+08');
  assert.equal(clock.requests.length, 1, 'One-second display ticks must not make HTTP requests');
  await clock.fire(30000);
  await clock.context.refreshMachineClock();
  assert.equal(clock.requests.length, 2, 'No overlapping resync requests');
  await clock.complete({ ...sample, unix_ms: sample.unix_ms + 3600000,
    utc_offset_seconds: -18000, timezone: 'UTC-05' });
  assert.equal(clock.element.textContent, '2026-11-08 20:00:00 UTC-05',
    'Resync follows device clock corrections and device timezone, not browser timezone');
  await clock.fire(30000);
  await clock.fail();
  clock.advance(2000);
  assert.match(clock.element.textContent, /20:00:02/);
  clock.advance(90000);
  assert.equal(clock.element.textContent, 'Unavailable', 'Do not extrapolate stale time indefinitely');
  await clock.fire(30000);
  await clock.complete();
  assert.equal(clock.element.textContent, '2026-11-09 08:00:00 UTC+08');

  clock.document.hidden = true;
  clock.events.visibilitychange();
  assert.equal(clock.intervals.size, 0);
  assert.equal(clock.timers.size, 0);
  clock.document.hidden = false;
  clock.events.visibilitychange();
  assert.equal(clock.intervals.size, 1);
  await clock.complete();
  clock.events.pagehide();
  assert.equal(clock.intervals.size, 0);
  assert.equal(clock.timers.size, 0);
  clock.events.pageshow();
  await clock.complete();
  assert.equal(clock.intervals.size, 1);

  const timeout = setup();
  timeout.context.startMachineClock();
  await timeout.fire(3000);
  assert.equal(timeout.requests[0].options.signal.aborted, true);
  assert.equal(timeout.element.textContent, 'Unavailable');
  assert.ok([...timeout.timers.values()].some(timer => timer.delay === 30000));

  const resume = setup();
  resume.context.startMachineClock();
  resume.context.stopMachineClock();
  resume.context.startMachineClock();
  await flush();
  await resume.fire(0);
  assert.equal(resume.requests.length, 2, 'Resume promptly after interrupted request');

  for (const response of [{ status: 401 }, { redirected: true }]) {
    const expired = setup();
    expired.context.startMachineClock();
    await expired.complete({}, response);
    assert.equal(expired.window.location.href, '/login');
    assert.equal(expired.intervals.size, 0);
    assert.equal(expired.timers.size, 0);
  }
  for (const data of [{}, { ...sample, unix_ms: 'bad' },
    { ...sample, utc_offset_seconds: 1e9 }]) {
    const invalid = setup();
    invalid.context.startMachineClock();
    await invalid.complete(data);
    assert.equal(invalid.element.textContent, 'Unavailable');
  }
  console.log('Device clock frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
