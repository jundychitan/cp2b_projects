/** SMS is loaded on demand, never polled or stored in browser storage. */
let smsBusy = false;
let smsPresenceRequest = null;
let smsModemPresent = false;

function setSmsBusy(busy) {
  smsBusy = busy;
  document.getElementById('smsRefreshBtn').disabled = busy;
  const remove = document.getElementById('smsDeleteAllBtn');
  if (remove) remove.disabled = busy || !smsModemPresent;
}

function updateSmsPresence(present) {
  smsModemPresent = present === true;
  const remove = document.getElementById('smsDeleteAllBtn');
  if (remove) remove.disabled = smsBusy || !smsModemPresent;
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
  setSmsBusy(true);
  const button = document.getElementById('smsRefreshBtn');
  const status = document.getElementById('smsStatus');
  const error = document.getElementById('smsError');
  const body = document.getElementById('smsMessages');
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
        message.status === 'REC UNREAD' ? 'Unread' : 'Read', message.text].forEach((value, index) => {
        const cell = document.createElement('td');
        if (index === 1) cell.className = 'sms-sender';
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
    setSmsBusy(false);
    button.textContent = 'Refresh';
  }
}

async function deleteAllSmsMessages() {
  if (!CAN_MANAGE || smsBusy || !smsModemPresent) return;
  if (!confirm('Delete ALL messages from the modem\'s currently selected SMS storage? '
      + 'This includes read, unread, sent and unsent messages, even those not shown here. '
      + 'This cannot be undone.')) return;
  setSmsBusy(true);
  const button = document.getElementById('smsDeleteAllBtn');
  const status = document.getElementById('smsStatus');
  const error = document.getElementById('smsError');
  button.textContent = 'Deleting…';
  status.textContent = 'Deleting SMS messages…';
  error.classList.add('d-none');
  try {
    const response = await fetch('/api/lte/sms/delete-all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': button.dataset.csrfToken },
      body: JSON.stringify({ confirm: true }),
    });
    if (response.redirected || response.status === 401) {
      window.location.href = '/login';
      return;
    }
    const data = await response.json();
    if (typeof data.present === 'boolean') updateSmsPresence(data.present);
    if (!response.ok) throw new Error(data.error || 'SMS deletion was not confirmed. Refresh before retrying.');
    if (data.used !== 0) throw new Error('SMS deletion was not confirmed. Refresh before retrying.');
    document.getElementById('smsMessages').replaceChildren();
    status.textContent = `${data.message} (${data.used}/${data.capacity} slots used).`;
  } catch (err) {
    status.textContent = 'SMS deletion was not confirmed.';
    error.textContent = err.message + ' Refresh SMS to check the current messages.';
    error.classList.remove('d-none');
  } finally {
    setSmsBusy(false);
    button.textContent = 'Delete All';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('sms-tab').addEventListener('shown.bs.tab', loadSmsMessages);
  document.getElementById('smsRefreshBtn').addEventListener('click', loadSmsMessages);
  const remove = document.getElementById('smsDeleteAllBtn');
  if (remove) remove.addEventListener('click', deleteAllSmsMessages);
  document.getElementById('mainTabs').addEventListener('shown.bs.tab', refreshSmsPresence);
  refreshSmsPresence();
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshSmsPresence();
});
