/**
 * AMR Gateway Web UI - Frontend Logic
 */

// Cached meter config list for dropdowns
let meterConfigList = [];

// ============================================================================
// Theme toggle (dark/light mode)
// ============================================================================

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-bs-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-bs-theme', next);
  localStorage.setItem('amr-theme', next);
  updateThemeIcon();
}

function updateThemeIcon() {
  const btn = document.getElementById('themeToggle');
  if (!btn) return;
  const theme = document.documentElement.getAttribute('data-bs-theme');
  btn.innerHTML = theme === 'dark' ? '&#9788;' : '&#9790;';  // sun : moon
}

// ============================================================================
// Utility helpers
// ============================================================================

function showAlert(msg, type) {
  const el = document.getElementById('statusAlert');
  el.className = `alert alert-${type}`;
  el.textContent = msg;
  el.classList.remove('d-none');
  setTimeout(() => el.classList.add('d-none'), 5000);
}

async function apiGet(url) {
  const res = await fetch(url);
  if (res.status === 401) { window.location.href = '/login'; return null; }
  return res.json();
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { window.location.href = '/login'; return null; }
  const data = await res.json();
  if (!res.ok) {
    showAlert(data.error || 'Operation failed', 'danger');
    return null;
  }
  return data;
}

async function apiDelete(url) {
  const res = await fetch(url, { method: 'DELETE' });
  if (res.status === 401) { window.location.href = '/login'; return null; }
  const data = await res.json();
  if (!res.ok) {
    showAlert(data.error || 'Operation failed', 'danger');
    return null;
  }
  return data;
}

// ============================================================================
// Role-based UI
// ============================================================================

function applyRoleRestrictions() {
  if (!IS_ADMIN) {
    document.querySelectorAll('.admin-only').forEach(el => {
      if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
        el.disabled = true;
      } else if (el.tagName === 'BUTTON' || el.tagName === 'A') {
        el.style.display = 'none';
      } else if (el.tagName === 'TH' || el.tagName === 'TD') {
        el.style.display = 'none';
      }
    });
  }
}

// ============================================================================
// Change Password
// ============================================================================

async function changePassword() {
  const oldPwd = document.getElementById('oldPassword').value;
  const newPwd = document.getElementById('newPassword').value;
  const confirmPwd = document.getElementById('confirmPassword').value;
  const alertEl = document.getElementById('pwdAlert');

  if (newPwd !== confirmPwd) {
    alertEl.className = 'alert alert-danger';
    alertEl.textContent = 'New passwords do not match.';
    alertEl.classList.remove('d-none');
    return;
  }

  const data = await apiPost('/api/auth/change-password', {
    old_password: oldPwd,
    new_password: newPwd,
  });

  if (data) {
    alertEl.className = 'alert alert-success';
    alertEl.textContent = data.message;
    alertEl.classList.remove('d-none');
    document.getElementById('oldPassword').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('confirmPassword').value = '';
  }
}

// ============================================================================
// LTE Setting
// ============================================================================

let lteStatusInterval = null;

async function loadLteStatus() {
  const data = await apiGet('/api/lte/status');
  if (!data) return;

  const simEl = document.getElementById('lteSimStatus');
  simEl.value = data.sim_status || '--';
  simEl.style.color = data.sim_status === 'Inserted' ? 'green' : 'red';

  document.getElementById('lteIpAddress').value = data.ip_address || '--';
  document.getElementById('lteStatus').value = data.status || '--';
  document.getElementById('lteCarrier').value = data.carrier || '--';
  document.getElementById('lteAccess').value = data.access || '--';
  document.getElementById('lteBand').value = data.band || '--';
  document.getElementById('lteSignal').value = data.signal || '--';
}

function startLtePolling() {
  loadLteStatus();
  if (!lteStatusInterval) {
    lteStatusInterval = setInterval(loadLteStatus, 5000);
  }
}

function stopLtePolling() {
  if (lteStatusInterval) {
    clearInterval(lteStatusInterval);
    lteStatusInterval = null;
  }
}

async function rebootLte() {
  if (!confirm('Reboot the LTE module? The connection will drop temporarily.')) return;
  const data = await apiPost('/api/lte/reboot', {});
  if (data) showAlert(data.message, 'warning');
}

// ============================================================================
// LTE Setting - Internet Watchdog
// ============================================================================

async function loadWatchdogTargets() {
  const data = await apiGet('/api/lte/watchdog');
  if (!data) return;
  const container = document.getElementById('watchdogTargets');
  container.innerHTML = '';
  (data.targets || []).forEach(ip => addWatchdogRow(ip));
  if ((data.targets || []).length === 0) addWatchdogRow('');
}

function addWatchdogRow(value) {
  const container = document.getElementById('watchdogTargets');
  const div = document.createElement('div');
  div.className = 'input-group input-group-sm mb-2';
  div.style.maxWidth = '350px';
  div.innerHTML = `
    <input type="text" class="form-control watchdog-ip" value="${escapeHtml(value || '')}" placeholder="e.g. 8.8.8.8 or https://google.com">
    <button class="btn btn-outline-danger admin-only" type="button" onclick="this.closest('.input-group').remove()">Remove</button>
  `;
  container.appendChild(div);
  applyRoleRestrictions();
}

function addWatchdogTarget() {
  addWatchdogRow('');
}

async function saveWatchdogTargets() {
  const inputs = document.querySelectorAll('.watchdog-ip');
  const targets = [];
  inputs.forEach(el => {
    const v = el.value.trim();
    if (v) targets.push(v);
  });
  const data = await apiPost('/api/lte/watchdog', { targets });
  if (data) showAlert(data.message, 'success');
}

// ============================================================================
// RTU Setting
// ============================================================================

function toggleRtuMethod() {
  const isStatic = document.getElementById('rtuStatic').checked;
  document.querySelectorAll('.rtu-static-field').forEach(el => {
    el.disabled = !isStatic;
  });
}

async function loadRtuConfig() {
  const data = await apiGet('/api/rtu/config');
  if (!data) return;

  if (data.method === 'manual') {
    document.getElementById('rtuStatic').checked = true;
  } else {
    document.getElementById('rtuDhcp').checked = true;
  }
  toggleRtuMethod();

  document.getElementById('rtuMac').value = data.mac_address || '--';
  document.getElementById('rtuIp').value = data.ip_address || '';
  document.getElementById('rtuSubnet').value = data.subnet || '24';
  document.getElementById('rtuGateway').value = data.gateway || '';
  document.getElementById('rtuDns').value = data.dns || '';
  document.getElementById('rtuServerIp').value = data.server_ip || '';
  document.getElementById('rtuLocation').value = data.location || '';
}

async function saveRtuNetwork() {
  const isStatic = document.getElementById('rtuStatic').checked;
  const body = { method: isStatic ? 'manual' : 'auto' };

  if (isStatic) {
    body.ip_address = document.getElementById('rtuIp').value.trim();
    body.subnet = document.getElementById('rtuSubnet').value.trim() || '24';
    body.gateway = document.getElementById('rtuGateway').value.trim();
    body.dns = document.getElementById('rtuDns').value.trim();
  }

  const data = await apiPost('/api/rtu/network', body);
  if (data) showAlert(data.message, 'success');
}

async function saveRtuServer() {
  const data = await apiPost('/api/rtu/server', {
    server_ip: document.getElementById('rtuServerIp').value.trim(),
  });
  if (data) showAlert(data.message, 'success');
}

async function saveRtuLocation() {
  const data = await apiPost('/api/rtu/location', {
    location: document.getElementById('rtuLocation').value.trim(),
  });
  if (data) showAlert(data.message, 'success');
}

// ============================================================================
// AMR Setting - Auto Read
// ============================================================================

async function loadAutoRead() {
  const data = await apiGet('/api/amr/autoread');
  if (!data) return;
  document.getElementById('autoReadEnable').checked = data.enabled;
  document.getElementById('autoReadInterval').value = data.interval || '30';
  document.getElementById('autoReadLabel').textContent =
    data.enabled ? 'Enabled' : 'Disabled';
}

async function saveAutoRead() {
  const enable = document.getElementById('autoReadEnable').checked;
  const interval = document.getElementById('autoReadInterval').value;

  const data = await apiPost('/api/amr/autoread', { enable, interval });
  if (data) {
    showAlert(data.message, 'success');
    document.getElementById('autoReadLabel').textContent =
      enable ? 'Enabled' : 'Disabled';
  }
}

// ============================================================================
// AMR Setting - Meter Batch
// ============================================================================

async function loadMeterConfigs() {
  const data = await apiGet('/api/amr/meter-configs');
  if (data) meterConfigList = data.configs || [];
}

function buildConfigDropdown(selected) {
  let html = '<option value="">-- Select --</option>';
  for (const cfg of meterConfigList) {
    const sel = cfg === selected ? ' selected' : '';
    html += `<option value="${cfg}"${sel}>${cfg}</option>`;
  }
  return html;
}

function renderMeterTable(meters) {
  const tbody = document.getElementById('meterTableBody');
  tbody.innerHTML = '';

  meters.forEach((m, i) => {
    const tr = document.createElement('tr');
    tr.dataset.index = i;
    tr.innerHTML = `
      <td>${i + 1}</td>
      <td><input type="text" class="form-control form-control-sm meter-sn"
                 value="${escapeHtml(m.meter_sn)}" disabled></td>
      <td><select class="form-select form-select-sm meter-cfg" disabled>
            ${buildConfigDropdown(m.config_file)}
          </select></td>
      <td><input type="text" class="form-control form-control-sm meter-alt"
                 value="${escapeHtml(m.alt_address)}" disabled></td>
      <td class="admin-only">
        <button class="btn btn-sm btn-outline-primary btn-action" onclick="editMeterRow(this)">Edit</button>
        <button class="btn btn-sm btn-outline-danger btn-action" onclick="deleteMeterRow(this)">Delete</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  applyRoleRestrictions();
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str || '';
  return div.innerHTML;
}

const MAX_METERS = 32;

function addMeterRow() {
  const tbody = document.getElementById('meterTableBody');
  if (tbody.children.length >= MAX_METERS) {
    showAlert(`Maximum of ${MAX_METERS} meters allowed`, 'warning');
    return;
  }
  const idx = tbody.children.length;
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td>${idx + 1}</td>
    <td><input type="text" class="form-control form-control-sm meter-sn" value=""></td>
    <td><select class="form-select form-select-sm meter-cfg">
          ${buildConfigDropdown('')}
        </select></td>
    <td><input type="text" class="form-control form-control-sm meter-alt" value=""></td>
    <td class="admin-only">
      <button class="btn btn-sm btn-outline-primary btn-action" onclick="editMeterRow(this)">Edit</button>
      <button class="btn btn-sm btn-outline-danger btn-action" onclick="deleteMeterRow(this)">Delete</button>
    </td>
  `;
  tbody.appendChild(tr);
  applyRoleRestrictions();
}

function editMeterRow(btn) {
  const tr = btn.closest('tr');
  tr.querySelector('.meter-sn').disabled = false;
  tr.querySelector('.meter-cfg').disabled = false;
  tr.querySelector('.meter-alt').disabled = false;
}

function deleteMeterRow(btn) {
  const tr = btn.closest('tr');
  tr.remove();
  renumberMeterTable();
}

function renumberMeterTable() {
  const rows = document.querySelectorAll('#meterTableBody tr');
  rows.forEach((tr, i) => {
    tr.querySelector('td:first-child').textContent = i + 1;
  });
}

function collectMeters() {
  const rows = document.querySelectorAll('#meterTableBody tr');
  const meters = [];
  rows.forEach(tr => {
    meters.push({
      meter_sn: tr.querySelector('.meter-sn').value.trim(),
      config_file: tr.querySelector('.meter-cfg').value,
      alt_address: tr.querySelector('.meter-alt').value.trim(),
    });
  });
  return meters;
}

async function saveMeters() {
  const meters = collectMeters();
  const data = await apiPost('/api/amr/meters', { meters });
  if (data) showAlert(data.message, 'success');
}

async function loadMeters() {
  await loadMeterConfigs();
  const data = await apiGet('/api/amr/meters');
  if (data) renderMeterTable(data.meters || []);
}

// ============================================================================
// AMR Setting - Config File Editor
// ============================================================================

async function loadConfigList() {
  const data = await apiGet('/api/amr/configs');
  if (!data) return;

  const sel = document.getElementById('cfgFileSelect');
  const current = sel.value;
  sel.innerHTML = '<option value="">-- Select a file --</option>';
  (data.configs || []).forEach(f => {
    const opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f;
    if (f === current) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function loadConfigFile() {
  const filename = document.getElementById('cfgFileSelect').value;
  const editor = document.getElementById('cfgEditor');

  if (!filename) {
    editor.value = '';
    return;
  }

  const data = await apiGet(`/api/amr/configs/${encodeURIComponent(filename)}`);
  if (data) editor.value = data.content || '';
}

async function saveConfigFile() {
  const filename = document.getElementById('cfgFileSelect').value;
  if (!filename) {
    showAlert('No file selected', 'warning');
    return;
  }

  const content = document.getElementById('cfgEditor').value;
  const data = await apiPost(`/api/amr/configs/${encodeURIComponent(filename)}`, { content });
  if (data) showAlert(data.message, 'success');
}

async function saveConfigFileAs() {
  const newName = prompt('Enter new filename (e.g. meter01.cfg):');
  if (!newName) return;

  const content = document.getElementById('cfgEditor').value;
  const data = await apiPost('/api/amr/configs', { filename: newName, content });
  if (data) {
    showAlert(data.message, 'success');
    await loadConfigList();
    // Select the new file
    if (data.filename) {
      document.getElementById('cfgFileSelect').value = data.filename;
    }
    // Refresh meter config dropdowns too
    await loadMeterConfigs();
  }
}

async function deleteConfigFile() {
  const filename = document.getElementById('cfgFileSelect').value;
  if (!filename) {
    showAlert('No file selected', 'warning');
    return;
  }

  if (!confirm(`Delete ${filename}? This cannot be undone.`)) return;

  const data = await apiDelete(`/api/amr/configs/${encodeURIComponent(filename)}`);
  if (data) {
    showAlert(data.message, 'success');
    document.getElementById('cfgEditor').value = '';
    await loadConfigList();
    await loadMeterConfigs();
  }
}

// ============================================================================
// Live Meter
// ============================================================================

let meterPollTimer = null;

async function readMeters() {
  const btn = document.getElementById('readMetersBtn');
  const output = document.getElementById('liveMeterOutput');
  const readLp = document.getElementById('readLpCheck').checked;

  // Disable button while running
  btn.disabled = true;
  btn.textContent = 'Reading...';
  output.textContent = '';

  // Start the script
  const data = await apiPost('/api/livemeter/read', { read_lp: readLp });
  if (!data) {
    btn.disabled = false;
    btn.textContent = 'Read Meters';
    return;
  }

  // Start polling for output
  pollMeterOutput();
}

async function pollMeterOutput() {
  const btn = document.getElementById('readMetersBtn');
  const output = document.getElementById('liveMeterOutput');

  try {
    const data = await apiGet('/api/livemeter/output');
    if (!data) return;

    output.textContent = data.output || '';
    output.scrollTop = output.scrollHeight;

    if (data.running) {
      // Still running, poll again in 2 seconds
      meterPollTimer = setTimeout(pollMeterOutput, 2000);
    } else {
      // Script finished
      btn.disabled = false;
      btn.textContent = 'Read Meters';
      await loadLiveMeterData();
    }
  } catch (e) {
    output.textContent += '\n[Poll error: ' + e.message + ']';
    btn.disabled = false;
    btn.textContent = 'Read Meters';
  }
}

async function loadLiveMeterData() {
  const data = await apiGet('/api/livemeter/data');
  if (!data) return;

  const tbody = document.getElementById('liveMeterBody');
  const meters = data.meters || [];

  if (meters.length === 0) {
    tbody.innerHTML = '<tr><td colspan="11" class="text-center text-muted">No meter data found in /tmp/amr/log</td></tr>';
    return;
  }

  tbody.innerHTML = '';
  meters.forEach(m => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(m.meter_id)}</td>
      <td>${escapeHtml(m.datetime)}</td>
      <td>${escapeHtml(m.vrms_a)}</td>
      <td>${escapeHtml(m.vrms_b)}</td>
      <td>${escapeHtml(m.vrms_c)}</td>
      <td>${escapeHtml(m.irms_a)}</td>
      <td>${escapeHtml(m.irms_b)}</td>
      <td>${escapeHtml(m.irms_c)}</td>
      <td>${escapeHtml(m.freq)}</td>
      <td>${escapeHtml(m.watt)}</td>
      <td>${escapeHtml(m.wh_total)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function clearLiveMeter() {
  // Stop polling if active
  if (meterPollTimer) {
    clearTimeout(meterPollTimer);
    meterPollTimer = null;
  }
  document.getElementById('liveMeterBody').innerHTML =
    '<tr><td colspan="11" class="text-center text-muted">No data. Click "Read Meters" to start.</td></tr>';
  document.getElementById('liveMeterOutput').textContent = 'Waiting for command...';
}

// ============================================================================
// Diagnostic
// ============================================================================

function appendDiagOutput(text) {
  const el = document.getElementById('diagOutput');
  if (el.textContent === 'Waiting for command...') el.textContent = '';
  el.textContent += text + '\n';
  el.scrollTop = el.scrollHeight;
}

async function diagPing() {
  const target = document.getElementById('diagPingTarget').value.trim();
  if (!target) { showAlert('Enter a target to ping', 'warning'); return; }

  appendDiagOutput(`> ping ${target}\n`);
  const data = await apiPost('/api/diagnostic/ping', { target });
  if (data) appendDiagOutput(data.output);
}

async function diagCheckDevices() {
  appendDiagOutput('> nmcli device status\n');
  const data = await apiPost('/api/diagnostic/devices', {});
  if (data) appendDiagOutput(data.output);
}

async function diagRoutes() {
  appendDiagOutput('> ip route show\n');
  const data = await apiPost('/api/diagnostic/routes', {});
  if (data) appendDiagOutput(data.output);
}

async function diagCheckCM() {
  appendDiagOutput('> journalctl -u connection-manager.service -e\n');
  const data = await apiPost('/api/diagnostic/cm-status', {});
  if (data) appendDiagOutput(data.output);
}

async function diagLteCheck() {
  appendDiagOutput('> LTE AT Command Check\n');
  const data = await apiPost('/api/diagnostic/lte-check', {});
  if (data) appendDiagOutput(data.output);
}

async function diagPingLogs() {
  appendDiagOutput('> tail -n 100 /var/log/internet-watchdog.log\n');
  const data = await apiPost('/api/diagnostic/ping-logs', {});
  if (data) appendDiagOutput(data.output);
}

function clearDiagnostic() {
  document.getElementById('diagOutput').textContent = 'Waiting for command...';
}

async function rebootDevice() {
  const data = await apiPost('/api/diagnostic/reboot', {});
  if (data) {
    showAlert(data.message, 'warning');
    // Close the modal
    const modal = bootstrap.Modal.getInstance(document.getElementById('rebootModal'));
    if (modal) modal.hide();
  }
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
  // Update theme icon to match current theme
  updateThemeIcon();

  // Apply role restrictions
  applyRoleRestrictions();

  // Load data for the active tab (LTE)
  startLtePolling();
  loadWatchdogTargets();

  // Load data when tabs are shown, manage LTE polling
  document.getElementById('lte-tab').addEventListener('shown.bs.tab', () => {
    startLtePolling();
    loadWatchdogTargets();
  });
  document.getElementById('lte-tab').addEventListener('hidden.bs.tab', stopLtePolling);
  document.getElementById('rtu-tab').addEventListener('shown.bs.tab', loadRtuConfig);
  document.getElementById('amr-tab').addEventListener('shown.bs.tab', () => {
    loadAutoRead();
    loadMeters();
    loadConfigList();
  });
  document.getElementById('livemeter-tab').addEventListener('shown.bs.tab', loadLiveMeterData);
});
