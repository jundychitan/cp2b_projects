/** Standalone reboot monitor: no authenticated UI, background tab timers, or cookies. */
(function () {
  const deadline = Date.now() + 5 * 60 * 1000;
  let wasOffline = false;

  async function check() {
    if (Date.now() >= deadline) {
      document.getElementById('rebootTitle').textContent = 'Device not yet available';
      document.getElementById('rebootStatus').textContent =
        'Automatic checking stopped after five minutes. The reboot may have failed, '
        + 'the device may still be starting, or its IP address may have changed. '
        + 'Check the device and use the login link to try again.';
      return;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), Math.min(3000, deadline - Date.now()));
    let online = false;
    try {
      const response = await fetch(`/api/diagnostic/ready?t=${Date.now()}`, {
        cache: 'no-store', credentials: 'omit', redirect: 'error', signal: controller.signal,
      });
      online = response.ok && (await response.json()).status === 'ready';
    } catch (error) {
      online = false;
    } finally {
      clearTimeout(timeout);
    }
    if (!online) {
      wasOffline = true;
      document.getElementById('rebootStatus').textContent =
        'Device is offline. Waiting for the web UI to return…';
    } else if (wasOffline && Date.now() < deadline) {
      window.location.replace('/login');
      return;
    }
    setTimeout(check, 2000);
  }

  check();
})();
