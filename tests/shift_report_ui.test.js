async function testShiftReportUi(source) {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const extract = (name) => {
    const start = source.search(new RegExp(`(?:async )?function ${name}\\(`));
    const end = source.indexOf('\n}\n', start);
    assert(start >= 0 && end > start, `Missing function ${name}`);
    return source.slice(start, end + 2);
  };
  const compile = (name, mocks) => new Function(
    ...Object.keys(mocks), `${extract(name)}; return ${name};`,
  )(...Object.values(mocks));
  let xhr;
  class FakeXhr {
    constructor() {
      xhr = this;
      this.listeners = {};
      this.upload = { addEventListener() {} };
    }
    open() {}
    setRequestHeader() {}
    addEventListener(name, callback) { this.listeners[name] = callback; }
    send() {}
  }
  const upload = compile('uploadForm', { XMLHttpRequest: FakeXhr, tg: null });
  for (const response of ['<html>OK</html>', '{}', '{"sent":true,"completed":false}']) {
    const pending = upload('/submit', {}).then(() => false, () => true);
    xhr.status = 200;
    xhr.responseText = response;
    xhr.listeners.load();
    assert(await pending, 'Unconfirmed response must not clear the draft');
  }
  const confirmed = upload('/submit', {});
  xhr.status = 200;
  xhr.responseText = '{"sent":true,"completed":true}';
  xhr.listeners.load();
  assert((await confirmed).completed, 'Confirmed response must succeed');
  const timeout = upload('/submit', {}).then(() => false, () => true);
  assert(xhr.timeout > 0, 'Upload must have a timeout');
  xhr.listeners.timeout();
  assert(await timeout, 'Timeout must reject');

  let stopped = 0;
  let grantAccess;
  const runtime = { stream: null, cameraRequest: 0 };
  const button = { disabled: false };
  const camera = compile('openCamera', {
    runtime, $: () => button, requestAppFullscreen() {}, syncCameraViewport() {},
    window: { isSecureContext: true }, document: { hidden: false }, tg: null,
    navigator: { mediaDevices: { getUserMedia: () => new Promise((resolve) => { grantAccess = resolve; }) } },
    stopCamera() { throw new Error('Unexpected camera failure'); },
    renderPhotoReady() {}, toast() {}, updateCameraInstruction() {},
  });
  const opening = camera();
  runtime.cameraRequest += 1;
  grantAccess({ getTracks: () => [{ stop() { stopped += 1; } }] });
  await opening;
  assert(stopped === 1 && runtime.stream === null, 'Cancelled camera must release its stream');
  assert(!button.disabled, 'Camera button must recover');

  let stage;
  const success = compile('showReportSuccess', {
    runtime: { draft: { id: 'sent' } },
    deleteDraft: async () => { throw new Error('Storage unavailable'); },
    console: { warn() {} }, releaseReviewUrls() {},
    setStage(value) { stage = value; },
    tg: { HapticFeedback: { notificationOccurred() { throw new Error('Unsupported'); } } },
  });
  await success();
  assert(stage === 'successStage', 'Cleanup and haptic errors must not undo confirmed delivery');
  return '7 UI recovery scenarios passed';
}

if (typeof require !== 'undefined') {
  const fs = require('node:fs');
  const path = require('node:path');
  testShiftReportUi(fs.readFileSync(path.join(__dirname, '../kpi_static/shift_test.js'), 'utf8'))
    .then(console.log).catch((error) => { console.error(error); process.exitCode = 1; });
}
