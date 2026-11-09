/** Advance device-local time every second; resync without per-second HTTP traffic. */
let machineClockSample = null;
let machineClockTick = null;
let machineClockRefresh = null;
let machineClockController = null;
let machineClockActive = false;
let machineClockRun = 0;

function renderMachineClock() {
  const element = document.getElementById('machineClock');
  if (!element) return;
  const elapsed = machineClockSample ? performance.now() - machineClockSample.received : 0;
  if (!machineClockSample || elapsed > 90000) {
    element.textContent = 'Unavailable';
    return;
  }
  const localMs = machineClockSample.unixMs + elapsed + machineClockSample.offset * 1000;
  element.textContent = new Date(localMs).toISOString().slice(0, 19).replace('T', ' ')
    + ' ' + machineClockSample.timezone;
}

async function refreshMachineClock() {
  if (!machineClockActive || machineClockController) return;
  const controller = new AbortController();
  const run = machineClockRun;
  machineClockController = controller;
  const timeout = setTimeout(() => controller.abort(), 3000);
  try {
    const started = performance.now();
    const response = await fetch('/api/diagnostic/time', {
      cache: 'no-store', signal: controller.signal,
    });
    if (!machineClockActive || controller.signal.aborted) return;
    if (response.redirected || response.status === 401) {
      machineClockSample = null;
      stopMachineClock();
      renderMachineClock();
      window.location.href = '/login';
      return;
    }
    const data = await response.json();
    if (!response.ok || !Number.isFinite(data.unix_ms)
        || !Number.isFinite(data.utc_offset_seconds) || typeof data.timezone !== 'string'
        || Math.abs(data.utc_offset_seconds) > 86400
        || Math.abs(data.unix_ms) > 8.63e15) {
      throw new Error('Invalid device clock response');
    }
    if (!machineClockActive || controller.signal.aborted) return;
    const received = performance.now();
    machineClockSample = {
      unixMs: data.unix_ms + (received - started) / 2,
      offset: data.utc_offset_seconds, timezone: data.timezone, received,
    };
  } catch (error) {
    // Keep advancing the last sample briefly; stop claiming accuracy after 90s.
  } finally {
    clearTimeout(timeout);
    machineClockController = null;
    renderMachineClock();
    if (machineClockActive) {
      machineClockRefresh = setTimeout(() => {
        machineClockRefresh = null;
        refreshMachineClock();
      }, run === machineClockRun ? 30000 : 0);
    }
  }
}

function startMachineClock() {
  if (machineClockActive || document.hidden) return;
  machineClockActive = true;
  machineClockRun += 1;
  renderMachineClock();
  machineClockTick = setInterval(renderMachineClock, 1000);
  refreshMachineClock();
}

function stopMachineClock() {
  machineClockActive = false;
  if (machineClockTick !== null) clearInterval(machineClockTick);
  if (machineClockRefresh !== null) clearTimeout(machineClockRefresh);
  machineClockTick = null;
  machineClockRefresh = null;
  if (machineClockController) machineClockController.abort();
}

document.addEventListener('DOMContentLoaded', startMachineClock);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopMachineClock();
  else startMachineClock();
});
window.addEventListener('pagehide', stopMachineClock);
window.addEventListener('pageshow', startMachineClock);
