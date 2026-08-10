const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const HOLD_DELAY_MS = 420;
const MAX_VIDEO_MS = 15000;

const state = {
  stream: null,
  recorder: null,
  chunks: [],
  pressTimer: null,
  recordingTimer: null,
  recordingTimeout: null,
  recordingStartedAt: 0,
  pointerActive: false,
  holdTriggered: false,
  discardRecording: false,
  media: null,
  previewUrl: null,
  diagnostics: {
    platform: tg?.platform || 'browser',
    telegram_version: tg?.version || 'неизвестно',
    capture_method: '',
    mime_type: '',
    resolution: '',
    duration: '',
    camera_status: navigator.mediaDevices?.getUserMedia ? 'Поддерживается' : 'Недоступна',
    microphone_status: 'Не проверен',
    recorder_status: window.MediaRecorder ? 'Поддерживается' : 'Недоступен',
    user_agent: navigator.userAgent,
    error: '',
  },
};

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#070b1f');
tg?.setBackgroundColor('#070b1f');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[character]));
}

function toast(message, error = false) {
  const element = $('#cameraToast');
  element.textContent = message;
  element.className = `camera-toast visible${error ? ' error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.className = 'camera-toast'; }, 3000);
}

function supportValue(selector, supported, labels = ['Доступна', 'Нет']) {
  const element = $(selector);
  element.textContent = supported ? labels[0] : labels[1];
  element.className = supported ? 'ok' : 'fail';
}

function renderSupport() {
  supportValue('#cameraSupport', Boolean(navigator.mediaDevices?.getUserMedia));
  supportValue('#recorderSupport', Boolean(window.MediaRecorder));
  supportValue('#secureSupport', window.isSecureContext, ['HTTPS', 'Нужен HTTPS']);
}

function stopStream() {
  state.stream?.getTracks().forEach((track) => track.stop());
  state.stream = null;
  $('#cameraView').srcObject = null;
}

function clearRecordingTimers() {
  clearInterval(state.recordingTimer);
  clearTimeout(state.recordingTimeout);
  state.recordingTimer = null;
  state.recordingTimeout = null;
}

function closeCamera() {
  clearTimeout(state.pressTimer);
  state.pointerActive = false;
  if (state.recorder?.state === 'recording') {
    state.discardRecording = true;
    state.recorder.stop();
  }
  else stopStream();
  clearRecordingTimers();
  $('#cameraStage').hidden = true;
  $('#recordingBadge').hidden = true;
  $('#shutter').classList.remove('holding');
}

function recorderMimeType() {
  if (!window.MediaRecorder) return '';
  const types = [
    'video/mp4;codecs=h264,aac',
    'video/mp4',
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm',
  ];
  return types.find((type) => MediaRecorder.isTypeSupported?.(type)) || '';
}

async function openCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    showFallback('Браузер Telegram не предоставил доступ к web-камере.');
    return;
  }
  $('#startCamera').disabled = true;
  state.diagnostics.error = '';
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1280 },
        height: { ideal: 720 },
      },
      audio: true,
    });
    state.diagnostics.microphone_status = stream.getAudioTracks().length
      ? 'Работает' : 'Нет аудиодорожки';
  } catch (primaryError) {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      state.diagnostics.microphone_status = 'Недоступен — видео будет без звука';
      state.diagnostics.error = `Микрофон: ${primaryError.name || primaryError.message}`;
    } catch (cameraError) {
      const reason = `${cameraError.name || 'CameraError'}: ${cameraError.message || 'доступ отклонён'}`;
      state.diagnostics.camera_status = 'Ошибка';
      state.diagnostics.error = reason;
      showFallback(reason);
      $('#startCamera').disabled = false;
      return;
    }
  }

  state.stream = stream;
  state.diagnostics.camera_status = 'Работает';
  state.diagnostics.capture_method = 'Камера внутри Mini App';
  const video = $('#cameraView');
  video.srcObject = stream;
  try {
    await video.play();
  } catch (error) {
    state.diagnostics.error = `${error.name || 'PlayError'}: ${error.message || ''}`;
    stopStream();
    showFallback(state.diagnostics.error);
    $('#startCamera').disabled = false;
    return;
  }
  $('#fallbackCard').hidden = true;
  $('#previewCard').hidden = true;
  $('#cameraStage').hidden = false;
  $('#startCamera').disabled = false;
}

function capturePhoto() {
  if (!state.stream) return;
  const video = $('#cameraView');
  if (!video.videoWidth || !video.videoHeight) {
    toast('Камера ещё не успела подготовить изображение.', true);
    return;
  }
  const maximumWidth = 1600;
  const scale = Math.min(1, maximumWidth / video.videoWidth);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  canvas.toBlob((blob) => {
    if (!blob) {
      toast('Не удалось получить фотографию.', true);
      return;
    }
    state.diagnostics.capture_method = 'Короткое нажатие в Mini App';
    state.diagnostics.mime_type = blob.type || 'image/jpeg';
    state.diagnostics.resolution = `${canvas.width}×${canvas.height}`;
    state.diagnostics.duration = '';
    setCapturedMedia(blob, 'photo', 'camera-test.jpg');
  }, 'image/jpeg', 0.86);
}

function updateRecordingTime() {
  const elapsed = Math.min(
    MAX_VIDEO_MS,
    Date.now() - state.recordingStartedAt,
  );
  $('#recordingTime').textContent = `00:${String(Math.floor(elapsed / 1000)).padStart(2, '0')}`;
}

function startRecording() {
  if (!state.pointerActive || !state.stream || !window.MediaRecorder) {
    if (!window.MediaRecorder) toast('На этом устройстве видео недоступно.', true);
    return;
  }
  const mimeType = recorderMimeType();
  try {
    state.discardRecording = false;
    state.chunks = [];
    state.recorder = new MediaRecorder(
      state.stream,
      mimeType ? { mimeType, videoBitsPerSecond: 2500000 } : undefined,
    );
  } catch (error) {
    state.diagnostics.recorder_status = 'Ошибка запуска';
    state.diagnostics.error = `${error.name || 'RecorderError'}: ${error.message || ''}`;
    toast('Видео не запустилось — можно сделать фото.', true);
    return;
  }
  state.recorder.ondataavailable = (event) => {
    if (event.data?.size) state.chunks.push(event.data);
  };
  state.recorder.onerror = (event) => {
    state.diagnostics.error = event.error?.message || 'Ошибка записи видео';
  };
  state.recorder.onstop = () => {
    if (state.discardRecording) {
      stopStream();
      return;
    }
    const duration = Math.max(0, Date.now() - state.recordingStartedAt);
    const type = state.recorder.mimeType || mimeType || 'video/webm';
    const blob = new Blob(state.chunks, { type });
    const extension = type.includes('mp4') ? 'mp4' : 'webm';
    state.diagnostics.capture_method = 'Удержание в Mini App';
    state.diagnostics.mime_type = type;
    state.diagnostics.duration = `${(duration / 1000).toFixed(1)} сек`;
    state.diagnostics.recorder_status = 'Работает';
    setCapturedMedia(blob, 'video', `camera-test.${extension}`);
  };
  state.recordingStartedAt = Date.now();
  state.recorder.start(250);
  $('#recordingBadge').hidden = false;
  $('#shutter').classList.add('holding');
  $('#captureHint').textContent = 'Отпустите, чтобы закончить';
  updateRecordingTime();
  state.recordingTimer = setInterval(updateRecordingTime, 200);
  state.recordingTimeout = setTimeout(stopRecording, MAX_VIDEO_MS);
  tg?.HapticFeedback?.impactOccurred('medium');
}

function stopRecording() {
  clearRecordingTimers();
  $('#recordingBadge').hidden = true;
  $('#shutter').classList.remove('holding');
  $('#captureHint').textContent = 'Нажмите или удерживайте';
  if (state.recorder?.state === 'recording') state.recorder.stop();
}

function resetPreviewUrl() {
  if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
  state.previewUrl = null;
}

function humanSize(size) {
  return size >= 1024 * 1024
    ? `${(size / (1024 * 1024)).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(size / 1024))} КБ`;
}

function diagnosticRows() {
  const rows = [
    ['Платформа', state.diagnostics.platform],
    ['Формат', state.diagnostics.mime_type || '—'],
    ['Разрешение', state.diagnostics.resolution || '—'],
    ['Продолжительность', state.diagnostics.duration || 'Фото'],
    ['Микрофон', state.diagnostics.microphone_status],
    ['Запись видео', state.diagnostics.recorder_status],
  ];
  return rows.map(([label, value]) => (
    `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value || '—')}</strong></div>`
  )).join('');
}

function setCapturedMedia(blob, kind, filename) {
  if (!blob?.size) {
    toast('Камера вернула пустой файл.', true);
    return;
  }
  state.media = { blob, kind, filename };
  state.diagnostics.mime_type = blob.type || state.diagnostics.mime_type;
  resetPreviewUrl();
  state.previewUrl = URL.createObjectURL(blob);
  $('#previewTitle').textContent = kind === 'photo' ? 'Фотография готова' : 'Видео готово';
  $('#previewSize').textContent = humanSize(blob.size);
  $('#previewMedia').innerHTML = kind === 'photo'
    ? `<img src="${state.previewUrl}" alt="Тестовая фотография">`
    : `<video src="${state.previewUrl}" controls playsinline></video>`;
  $('#diagnostics').innerHTML = diagnosticRows();
  $('#previewCard').hidden = false;
  $('#fallbackCard').hidden = true;
  $('#cameraStage').hidden = true;
  stopStream();
  tg?.HapticFeedback?.notificationOccurred('success');
}

function showFallback(reason) {
  stopStream();
  $('#cameraStage').hidden = true;
  $('#fallbackReason').textContent = reason || 'Используйте системную камеру телефона.';
  $('#fallbackCard').hidden = false;
}

function handleSystemFile(file) {
  if (!file) return;
  const kind = file.type.startsWith('video/') ? 'video' : 'photo';
  const maximum = kind === 'video' ? 20 * 1024 * 1024 : 6 * 1024 * 1024;
  if (file.size > maximum) {
    toast(kind === 'video' ? 'Видео больше 20 МБ.' : 'Фото больше 6 МБ.', true);
    return;
  }
  state.diagnostics.capture_method = 'Системная камера';
  state.diagnostics.mime_type = file.type || (kind === 'video' ? 'video/mp4' : 'image/jpeg');
  state.diagnostics.camera_status = 'Работает через системный режим';
  state.diagnostics.duration = kind === 'video' ? 'Определит Telegram' : '';
  setCapturedMedia(file, kind, file.name || `camera-test.${kind === 'video' ? 'mp4' : 'jpg'}`);
}

async function sendResult(includeMedia = true) {
  const button = includeMedia ? $('#sendCapture') : $('#sendFailure');
  button.disabled = true;
  const data = new FormData();
  data.set('consent', 'yes');
  data.set('diagnostics', JSON.stringify(state.diagnostics));
  if (includeMedia && state.media) {
    data.set('media', state.media.blob, state.media.filename);
  }
  try {
    const response = await fetch('/api/camera-test', {
      method: 'POST',
      headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
      body: data,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Не удалось отправить тест');
    toast('Тест отправлен Павлу');
    tg?.HapticFeedback?.notificationOccurred('success');
    setTimeout(() => { window.location.href = '/shift'; }, 1300);
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

$('#startCamera').addEventListener('click', openCamera);
$('#closeCamera').addEventListener('click', closeCamera);
$('#fallbackPhoto').addEventListener('click', () => $('#systemPhoto').click());
$('#fallbackVideo').addEventListener('click', () => $('#systemVideo').click());
$('#systemPhoto').addEventListener('change', (event) => handleSystemFile(event.target.files[0]));
$('#systemVideo').addEventListener('change', (event) => handleSystemFile(event.target.files[0]));
$('#sendCapture').addEventListener('click', () => sendResult(true));
$('#sendFailure').addEventListener('click', () => sendResult(false));
$('#retryCapture').addEventListener('click', () => {
  state.media = null;
  resetPreviewUrl();
  $('#previewCard').hidden = true;
  openCamera();
});

const shutter = $('#shutter');
shutter.addEventListener('contextmenu', (event) => event.preventDefault());
shutter.addEventListener('pointerdown', (event) => {
  event.preventDefault();
  state.pointerActive = true;
  state.holdTriggered = false;
  shutter.setPointerCapture?.(event.pointerId);
  clearTimeout(state.pressTimer);
  state.pressTimer = setTimeout(() => {
    state.holdTriggered = true;
    startRecording();
  }, HOLD_DELAY_MS);
});
shutter.addEventListener('pointerup', (event) => {
  event.preventDefault();
  const recording = state.recorder?.state === 'recording';
  const held = state.holdTriggered;
  state.pointerActive = false;
  clearTimeout(state.pressTimer);
  if (recording) stopRecording();
  else if (!held) capturePhoto();
});
shutter.addEventListener('pointercancel', () => {
  state.pointerActive = false;
  clearTimeout(state.pressTimer);
  if (state.recorder?.state === 'recording') stopRecording();
});

window.addEventListener('pagehide', stopStream);
document.addEventListener('visibilitychange', () => {
  if (document.hidden && state.stream && state.recorder?.state !== 'recording') {
    closeCamera();
  }
});

renderSupport();
