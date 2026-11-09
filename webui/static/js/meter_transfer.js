/** Meter CSV transfer: validate before uploading; the server validates independently. */
const MAX_METER_UPLOAD_BYTES = 1024 * 1024;
let meterUploadBusy = false;
let meterDownloadBusy = false;

function validateMeterBatchCsv(text) {
  const records = [];
  let row = [];
  let field = '';
  let state = 'plain';
  let line = 1;
  text = text.replace(/^\uFEFF/, '');
  const finishRow = () => {
    row.push(field);
    records.push({ fields: row, line });
    row = [];
    field = '';
    state = 'plain';
  };
  for (let i = 0; i < text.length; i++) {
    const character = text[i];
    if (state === 'quoted') {
      if (character === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else state = 'closed';
      } else {
        field += character;
        if (character === '\n' || (character === '\r' && text[i + 1] !== '\n')) line++;
      }
      continue;
    }
    if (state === 'closed' && ![',', '\r', '\n'].includes(character)) {
      throw new Error(`Line ${line}: invalid CSV format`);
    }
    if (character === ',') {
      row.push(field);
      field = '';
      state = 'plain';
    } else if (character === '\r' || character === '\n') {
      finishRow();
      if (character === '\r' && text[i + 1] === '\n') i++;
      line++;
    } else if (character === '"' && field === '') {
      state = 'quoted';
    } else {
      field += character;
    }
  }
  if (state === 'quoted') throw new Error(`Line ${line}: invalid CSV format`);
  if (row.length || field || state === 'closed') finishRow();
  let total = 0;
  for (const record of records) {
    const fields = record.fields.map(value => value.trim());
    if (fields.length === 1 && !fields[0]) continue;
    if (fields.length !== 3) {
      throw new Error(`Line ${record.line}: expected serial number,config_file.cfg,modbus_id (3 fields)`);
    }
    const [serial, configFile, modbusId] = fields;
    if (!/^[0-9]+$/.test(serial)) {
      throw new Error(`Line ${record.line}: serial number must contain digits only`);
    }
    if (configFile.length <= 4 || !configFile.endsWith('.cfg')
        || /[\/\\\u0000-\u001f\u007f-\u009f]/.test(configFile)) {
      throw new Error(`Line ${record.line}: config_file must be a filename ending in .cfg, without a directory path`);
    }
    if (!/^[0-9]+$/.test(modbusId)) {
      throw new Error(`Line ${record.line}: Modbus ID must contain digits only`);
    }
    total++;
  }
  if (!total) throw new Error('The uploaded file contains no meter entries');
  return total;
}

function chooseMeterBatchUpload() {
  if (!CAN_MANAGE || meterUploadBusy) return;
  const input = document.getElementById('meterBatchUploadFile');
  input.value = '';
  input.click();
}

async function uploadMeterBatch() {
  if (!CAN_MANAGE || meterUploadBusy) return;
  const input = document.getElementById('meterBatchUploadFile');
  const file = input.files[0];
  if (!file) return;
  meterUploadBusy = true;
  const button = document.getElementById('meterBatchUploadBtn');
  const controls = ['meterBatchUploadFile', 'meterBatchUploadBtn', 'meterBatchAddBtn', 'meterBatchSaveBtn']
    .map(id => document.getElementById(id));
  controls.forEach(control => { control.disabled = true; });
  button.textContent = 'Validating...';
  let submitted = false;
  let saved = false;
  try {
    if (file.size > MAX_METER_UPLOAD_BYTES) throw new Error('Upload must be 1 MiB or smaller');
    let text;
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer());
    } catch (error) {
      throw new Error('Upload must be a UTF-8 CSV text file');
    }
    const total = validateMeterBatchCsv(text);
    const limit = total > 32 ? ` Only the first 32 of ${total} validated entries will be kept.` : '';
    if (!confirm('Replace the saved meter_batch.csv and any unsaved meter table edits?' + limit)) return;
    const body = new FormData();
    body.append('file', file);
    button.textContent = 'Uploading...';
    submitted = true;
    const response = await fetch('/api/amr/meters/upload', {
      method: 'POST', headers: { 'X-CSRF-Token': button.dataset.csrfToken }, body,
    });
    if (response.redirected || response.status === 401) {
      window.location.href = '/login';
      return;
    }
    const data = await response.json();
    if (!response.ok) {
      showAlert(data.error || 'Upload failed; meter_batch.csv was not replaced', 'danger');
      return;
    }
    if (!Array.isArray(data.meters)) throw new Error('Invalid upload response');
    saved = true;
    renderMeterTable(data.meters);
    showAlert(data.message, data.truncated ? 'warning' : 'success');
  } catch (error) {
    const message = saved ? 'Upload saved, but the table could not refresh. Reopen AMR Setting.'
      : (submitted ? 'Upload result is unknown. Reopen AMR Setting to check the saved batch before retrying.'
        : error.message);
    showAlert(message, 'danger');
  } finally {
    meterUploadBusy = false;
    controls.forEach(control => { control.disabled = false; });
    button.textContent = 'Upload';
    input.value = '';
  }
}

async function downloadMeterBatch() {
  if (meterDownloadBusy) return;
  meterDownloadBusy = true;
  const button = document.getElementById('meterBatchDownloadBtn');
  button.disabled = true;
  let writable = null;
  try {
    // Invoke during the click gesture; on HTTP/unsupported browsers use normal downloads.
    const handle = typeof window.showSaveFilePicker === 'function'
      ? await window.showSaveFilePicker({
        suggestedName: 'meter_batch.csv',
        types: [{ description: 'CSV file', accept: { 'text/csv': ['.csv'] } }],
      }) : null;
    const response = await fetch('/api/amr/meters/download', { cache: 'no-store' });
    if (response.redirected || response.status === 401) {
      window.location.href = '/login';
      return;
    }
    if (!response.ok) {
      const data = await response.json();
      showAlert(data.error || 'Unable to download meter_batch.csv', 'danger');
      return;
    }
    const blob = await response.blob();
    if (handle) {
      writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      writable = null;
    } else {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'meter_batch.csv';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  } catch (error) {
    if (error.name !== 'AbortError') showAlert('Unable to download or save meter_batch.csv', 'danger');
  } finally {
    if (writable && typeof writable.abort === 'function') {
      try { await writable.abort(); } catch (error) { /* Do not replace the original error. */ }
    }
    meterDownloadBusy = false;
    button.disabled = false;
  }
}
