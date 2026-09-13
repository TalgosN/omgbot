async function testAppRecovery(sources) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const compile = (file, name, mocks) => {
    const source = sources[file];
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    const end = source.indexOf('\n}\n', start);
    assert(start >= 0 && end > start, `Missing ${name}`);
    return new Function(...Object.keys(mocks), `${source.slice(start, end + 2)}; return ${name};`)(...Object.values(mocks));
  };
  let xhr;
  class FakeXhr {
    constructor() { xhr = this; this.events = {}; this.upload = { addEventListener() {} }; }
    open() {}
    setRequestHeader() {}
    addEventListener(name, callback) { this.events[name] = callback; }
    send() {}
  }
  const upload = compile('shift_tasks.js', 'uploadTask', {
    XMLHttpRequest: FakeXhr, state: { current: { id: 7 } }, tg: null,
  });
  for (const body of ['<html>OK</html>', '{}', '{"id":7,"status":"pending"}', '{"id":8,"status":"completed"}']) {
    const result = upload({}, {}).then(() => false, () => true);
    xhr.status = 200;
    xhr.responseText = body;
    xhr.events.load();
    assert(await result, 'Unconfirmed task response must fail');
  }
  for (const event of ['timeout', 'abort', 'error']) {
    const result = upload({}, {}).then(() => false, () => true);
    assert(xhr.timeout > 0, 'Upload timeout is required');
    xhr.events[event]();
    assert(await result, `${event} must preserve the form by rejecting`);
  }
  const success = upload({}, {});
  xhr.status = 200;
  xhr.responseText = '{"id":7,"status":"completed","already_completed":true}';
  xhr.events.load();
  assert((await success).already_completed, 'Confirmed retry must succeed');

  for (const staleFailure of [false, true]) {
    const pending = [];
    const state = { dataRequest: 0, day: '2026-08-01', month: '2026-08' };
    const mocks = {
      state, employeeList: {}, $: () => ({}), numericDayLabel: (value) => value,
      URLSearchParams: class { toString() { return ''; } },
      api: () => new Promise((resolve, reject) => pending.push({ resolve, reject })),
      showToast() { throw new Error('Stale error must not be shown'); },
    };
    for (const name of ['renderSummary', 'renderStatus', 'renderFreshness', 'renderMyKpi', 'renderManagerFilters', 'renderEmployees']) mocks[name] = () => {};
    const load = compile('app.js', 'loadData', mocks);
    const old = load();
    state.day = '2026-09-08';
    state.month = '2026-09';
    const current = load();
    pending[1].resolve({ date: state.day, month: state.month });
    await current;
    if (staleFailure) pending[0].reject(new Error('Old request failed'));
    else pending[0].resolve({ date: '2026-08-01', month: '2026-08' });
    await old;
    assert(state.day === '2026-09-08', 'Old response must not replace selected date');
  }

  for (const cancel of [false, true]) {
    let grant;
    let stopped = 0;
    const state = { problemCameraRequest: 0, problemCameraStream: null };
    const document = { hidden: false };
    const nodes = {};
    const camera = compile('problems.js', 'openProblemCamera', {
      state, document, $: (key) => nodes[key] || (nodes[key] = {}),
      window: { isSecureContext: true },
      navigator: { mediaDevices: { getUserMedia: () => new Promise((resolve) => { grant = resolve; }) } },
      toast() { throw new Error('Unexpected camera error'); }, stopProblemCameraStream() {},
    });
    const opening = camera();
    if (cancel) state.problemCameraRequest += 1;
    else document.hidden = true;
    grant({ getTracks: () => [{ stop() { stopped += 1; } }] });
    await opening;
    assert(stopped === 1 && !state.problemCameraStream, 'Cancelled camera must release its stream');
    assert(!nodes['#openProblemCamera'].disabled, 'Camera button must recover');
  }
  return '12 app recovery scenarios passed';
}

if (typeof require !== 'undefined') {
  const fs = require('node:fs');
  const path = require('node:path');
  const sources = Object.fromEntries(['shift_tasks.js', 'problems.js', 'app.js'].map((file) => [
    file, fs.readFileSync(path.join(__dirname, '../kpi_static', file), 'utf8').replace(/\r\n/g, '\n'),
  ]));
  testAppRecovery(sources).then(console.log).catch((error) => { console.error(error); process.exitCode = 1; });
}
