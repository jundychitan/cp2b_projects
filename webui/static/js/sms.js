/** SMS is loaded on demand, never polled or stored in browser storage. */
let smsBusy = false;
let smsPresenceRequest = null;

function updateSmsPresence(present) {
  const nav = document.getElementById('smsNavItem');
  if (!nav) return;
  nav.classList.toggle('d-none', present !== true);
  const lteNav = document.getElementById('lteNavItem');
  if (lteNav) lteNav.classList.toggle('d-none', present !== true);
  if (present !== true) {
    if (typeof stopLtePolling === 'function') stopLtePolling();
    document.getElementById('smsMessages').replaceChildren();
    document.getElementById('smsStatus').textContent = 'LTE module is not installed.';
    const lte = document.getElementById('lte');
    if (document.getElementById('sms').classList.contains('active')
        || (lte && lte.classList.contains('active'))) {
      bootstrap.Tab.getOrCreateInstance(document.getElementById('rtu-tab')).show();
    }
  }
}

async function refreshSmsPresence() {
  if (smsPresenceRequest) return smsPresenceRequest;
  smsPresenceRequest = (async () => {
    try {
      const response = await fetch('/api/lte/presence', { cache: 'no-store' });
      if (response.redirected || response.status === 401) {
        window.location.href = '/login';
        return;
      }
      const data = await response.json();
      updateSmsPresence(response.ok && data.present === true);
    } catch (error) {
      updateSmsPresence(false);
    } finally {
      smsPresenceRequest = null;
    }
  })();
  return smsPresenceRequest;
}

async function loadSmsMessages() {
  if (smsBusy) return;
  smsBusy = true;
  const button = document.getElementById('smsRefreshBtn');
  const status = document.getElementById('smsStatus');
  const error = document.getElementById('smsError');
  const body = document.getElementById('smsMessages');
  button.disabled = true;
  button.textContent = 'Reading…';
  status.textContent = 'Reading SMS messages…';
  error.classList.add('d-none');
  body.replaceChildren();
  try {
    const response = await fetch('/api/lte/sms', { cache: 'no-store' });
    if (response.redirected || response.status === 401) {
      window.location.href = '/login';
      return;
    }
    const data = await response.json();
    if (typeof data.present === 'boolean') updateSmsPresence(data.present);
    if (!response.ok) throw new Error(data.error || 'Unable to read SMS messages');
    const messages = data.messages || [];
    messages.forEach(message => {
      const row = document.createElement('tr');
      [message.index, message.sender, message.timestamp,
        message.status === 'REC UNREAD' ? 'Unread' : 'Read', message.text].forEach(value => {
        const cell = document.createElement('td');
        cell.textContent = String(value ?? '');
        cell.style.whiteSpace = 'pre-wrap';
        cell.style.overflowWrap = 'anywhere';
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
    status.textContent = `${messages.length} received message(s). Storage: ${data.storage} `
      + `(${data.used}/${data.capacity} slots used).`
      + (messages.length ? '' : ' No received SMS messages found.');
  } catch (err) {
    status.textContent = 'SMS messages unavailable.';
    error.textContent = err.message;
    error.classList.remove('d-none');
  } finally {
    smsBusy = false;
    button.disabled = false;
    button.textContent = 'Refresh';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('sms-tab').addEventListener('shown.bs.tab', loadSmsMessages);
  document.getElementById('smsRefreshBtn').addEventListener('click', loadSmsMessages);
  document.getElementById('mainTabs').addEventListener('shown.bs.tab', refreshSmsPresence);
  refreshSmsPresence();
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshSmsPresence();
});
