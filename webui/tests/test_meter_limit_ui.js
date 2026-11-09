/** Run with node tests/test_meter_limit_ui.js; table and network operations are mocked. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function main() {
  const table = {
    children: [], appendChild(row) { this.children.push(row); },
    set innerHTML(value) { this.children = []; },
  };
  const alert = { classList: { remove() {}, add() {} } };
  const replies = [];
  const context = vm.createContext({
    CAN_MANAGE: true,
    document: {
      getElementById: id => id === 'meterTableBody' ? table : alert,
      querySelectorAll: () => [],
      addEventListener() {},
      createElement: () => ({
        dataset: {}, textContent: '',
        set innerHTML(value) { this.html = value; },
        get innerHTML() { return this.html || this.textContent; },
      }),
    },
    setTimeout() {},
    fetch: async () => ({ status: 200, json: async () => replies.shift() }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
  const meters = Array.from({ length: 40 }, (_, i) => ({
    meter_sn: `SN${i}`, config_file: 'meter.cfg', alt_address: '',
  }));
  context.renderMeterTable(meters);
  assert.equal(table.children.length, 32, 'Defensively cap the rendered table at 32 rows');
  assert.match(table.children[31].html, /SN31/);
  context.addMeterRow();
  assert.equal(table.children.length, 32);
  assert.match(alert.textContent, /Maximum of 32/);
  replies.push({ configs: ['meter.cfg'] }, { meters: meters.slice(0, 32), truncated: true });
  await context.loadMeters();
  assert.equal(table.children.length, 32);
  assert.match(alert.textContent, /Only the first 32 were kept/);
  assert.equal(alert.className, 'alert alert-warning');
  console.log('Meter CSV limit frontend checks passed');
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
