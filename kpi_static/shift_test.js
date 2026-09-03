const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const DRAFT_SCHEMA = 3;
const DRAFT_PREFIX = 'omg-shift-report-draft-v3:';
const PHOTO_DATABASE = 'omg-shift-test';
const PHOTO_STORE = 'photos';
const MAX_PHOTO_BYTES = 1.8 * 1024 * 1024;

const runtime = {
  action: new URLSearchParams(window.location.search).get('action'),
  scenario: null,
  draft: null,
  photoDatabase: null,
  stream: null,
  capturing: false,
  retakeQuestionId: null,
  editingAnswerQuestionId: null,
  reviewUrls: [],
  batchItems: [],
  batchReviewUrls: [],
  batchReplaceIndex: null,
  batchStartIndex: null,
};

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#0d0913');
tg?.setBackgroundColor('#0d0913');

function requestAppFullscreen() {
  tg?.expand();
  if (!tg?.isFullscreen && typeof tg?.requestFullscreen === 'function') {
    try { tg.requestFullscreen(); }
    catch (_error) { tg.expand(); }
  }
}

tg?.onEvent?.('fullscreenFailed', () => tg.expand());
requestAppFullscreen();

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[character]));
}

function toast(message, error = false) {
  const element = $('#testToast');
  element.textContent = message;
  element.className = `test-toast visible${error ? ' error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.className = 'test-toast'; }, 3200);
}

function setCameraFeedback(visible, message = 'Сохраняем фотографию…', success = false) {
  const stage = $('#cameraStage');
  const feedback = $('#cameraSaving');
  stage.classList.toggle('busy', visible);
  feedback.hidden = !visible;
  feedback.classList.toggle('success', visible && success);
  $('#cameraSavingText').textContent = message;
  $('#shutter').disabled = visible;
}

function setPhotoProcessing(visible, message = 'Обрабатываем фотографию…', success = false) {
  const feedback = $('#photoProcessing');
  feedback.hidden = !visible;
  feedback.classList.toggle('success', visible && success);
  $('#photoProcessingText').textContent = message;
  document.querySelectorAll('.photo-source-actions button').forEach((button) => {
    button.disabled = visible;
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'X-Telegram-Init-Data': tg?.initData || '',
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || 'Не удалось выполнить запрос');
    error.code = payload.code || '';
    throw error;
  }
  return payload;
}

function uploadForm(path, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.setRequestHeader('X-Telegram-Init-Data', tg?.initData || '');
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) {
        onProgress?.(Math.min(99, Math.round((event.loaded / event.total) * 100)));
      }
    });
    xhr.upload.addEventListener('load', () => onProgress?.(100));
    xhr.addEventListener('load', () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText || '{}'); }
      catch (_error) { /* The status below provides the fallback message. */ }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload);
        return;
      }
      const error = new Error(payload.error || 'Не удалось отправить отчёт');
      error.code = payload.code || '';
      reject(error);
    });
    xhr.addEventListener('error', () => reject(new Error('Соединение прервалось во время отправки')));
    xhr.addEventListener('abort', () => reject(new Error('Отправка отменена')));
    xhr.send(form);
  });
}

function draftStorageKey(action = runtime.action) {
  return `${DRAFT_PREFIX}${action}`;
}

function loadLocalDraft() {
  try {
    return JSON.parse(localStorage.getItem(draftStorageKey()) || 'null');
  } catch (_error) {
    return null;
  }
}

function saveDraft() {
  runtime.draft.updated_at = new Date().toISOString();
  localStorage.setItem(draftStorageKey(), JSON.stringify(runtime.draft));
}

function openPhotoDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(PHOTO_DATABASE, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(PHOTO_STORE)) {
        database.createObjectStore(PHOTO_STORE, { keyPath: 'id' });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error('IndexedDB недоступна'));
  });
}

function photoRequest(mode, callback) {
  return new Promise((resolve, reject) => {
    const transaction = runtime.photoDatabase.transaction(PHOTO_STORE, mode);
    const store = transaction.objectStore(PHOTO_STORE);
    let result;
    try {
      result = callback(store);
    } catch (error) {
      reject(error);
      return;
    }
    transaction.oncomplete = () => resolve(result?.result);
    transaction.onerror = () => reject(transaction.error || result?.error);
    transaction.onabort = () => reject(transaction.error || new Error('Операция с черновиком отменена'));
  });
}

function photoKey(questionId, draft = runtime.draft) {
  return `${draft.id}:${questionId}`;
}

function putPhoto(questionId, blob) {
  return photoRequest('readwrite', (store) => store.put({
    id: photoKey(questionId),
    question_id: questionId,
    blob,
    saved_at: new Date().toISOString(),
  }));
}

function getPhoto(questionId, draft = runtime.draft) {
  return new Promise((resolve, reject) => {
    const transaction = runtime.photoDatabase.transaction(PHOTO_STORE, 'readonly');
    const request = transaction.objectStore(PHOTO_STORE).get(photoKey(questionId, draft));
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error);
  });
}

function clearDraftPhotos(draft) {
  if (!draft?.id || !runtime.photoDatabase) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const transaction = runtime.photoDatabase.transaction(PHOTO_STORE, 'readwrite');
    const store = transaction.objectStore(PHOTO_STORE);
    const cursor = store.openCursor();
    cursor.onsuccess = () => {
      const item = cursor.result;
      if (!item) return;
      if (String(item.key).startsWith(`${draft.id}:`)) item.delete();
      item.continue();
    };
    cursor.onerror = () => reject(cursor.error);
    transaction.oncomplete = resolve;
    transaction.onerror = () => reject(transaction.error);
  });
}

async function deleteDraft(draft = runtime.draft) {
  if (draft) await clearDraftPhotos(draft);
  localStorage.removeItem(draftStorageKey());
}

function textQuestions() {
  return runtime.scenario.questions.filter((question) => question.type !== 'photo');
}

function shiftPhotoQuestions() {
  return runtime.scenario.questions.filter((question) => question.type === 'photo');
}

function cleanlinessQuestions() {
  return runtime.scenario.cleanliness_questions || [];
}

function isCleanlinessPhase() {
  return runtime.draft?.photo_phase === 'cleanliness';
}

function photoQuestions() {
  return isCleanlinessPhase() ? cleanlinessQuestions() : shiftPhotoQuestions();
}

function currentPhotoIds() {
  return isCleanlinessPhase()
    ? runtime.draft.cleanliness_photo_ids
    : runtime.draft.photo_ids;
}

function setStage(stageId) {
  ['loadingCard', 'errorCard', 'ownerClubStage', 'cleanlinessIntroStage', 'cleanlinessTransitionStage', 'checklistStage', 'questionStage', 'photoStage', 'batchOrderStage', 'batchReviewStage', 'reviewStage', 'successStage']
    .forEach((id) => { $(`#${id}`).hidden = id !== stageId; });
}

function setWorkflowStep(step) {
  if (runtime.scenario?.action !== 'close') return;
  document.body.dataset.workflowStep = step;
  $$('[data-workflow-step]').forEach((element) => {
    const position = element.dataset.workflowStep;
    element.classList.toggle('active', position === step);
    element.classList.toggle(
      'completed',
      position === 'cleanliness' && step === 'closing',
    );
  });
}

function renderOwnerClubSelection(selection) {
  const closing = selection.action === 'close';
  $('#pageTitle').textContent = closing ? 'ЗАКРЫТИЕ СМЕНЫ' : 'ОТКРЫТИЕ СМЕНЫ';
  $('#pageDescription').textContent = 'Смена на сегодня не найдена · выберите клуб';
  const clubList = $('#ownerClubList');
  clubList.replaceChildren();
  selection.clubs.forEach((clubName) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = clubName;
    button.addEventListener('click', async () => {
      const buttons = [...clubList.querySelectorAll('button')];
      buttons.forEach((item) => { item.disabled = true; });
      button.classList.add('loading');
      try {
        runtime.scenario = await fetchScenario(null, clubName);
        setPageCopy();
        createDraft();
        renderInitialStage();
      } catch (error) {
        toast(error.message, true);
        button.classList.remove('loading');
        buttons.forEach((item) => { item.disabled = false; });
      }
    });
    clubList.append(button);
  });
  setStage('ownerClubStage');
}

function setPageCopy() {
  const closing = runtime.scenario.action === 'close';
  const hasCleanliness = cleanlinessQuestions().length > 0;
  $('#pageTitle').textContent = closing ? 'ЗАКРЫТИЕ СМЕНЫ' : 'ОТКРЫТИЕ СМЕНЫ';
  $('#pageDescription').textContent = `${runtime.scenario.club} · ${formatDate(runtime.scenario.shift.date)} · набор ${runtime.scenario.variant_label}`;
  $$('[data-club]').forEach((element) => { element.textContent = runtime.scenario.club; });
  $('#variantLabel').textContent = `Набор ${runtime.scenario.variant_label}`;
  $('#closingWorkflow').hidden = !closing || !hasCleanliness;
  $('#shiftReviewStep').textContent = closing && hasCleanliness ? '🌙 Этап 2' : 'Отчёт о смене';
  $('#shiftReviewTitle').textContent = closing ? 'Закрытие' : 'Открытие';
}

function formatDate(value) {
  const [year, month, day] = String(value || '').split('-');
  return year && month && day ? `${day}.${month}.${year}` : value;
}

function renderCleanlinessIntro() {
  const questions = cleanlinessQuestions();
  $('#cleanlinessChecklist').innerHTML = questions
    .map((question) => `<li>${escapeHtml(question.text)}</li>`)
    .join('');
  runtime.draft.stage = 'cleanliness_intro';
  runtime.draft.photo_phase = 'cleanliness';
  runtime.draft.photo_index = runtime.draft.cleanliness_photo_ids.length;
  saveDraft();
  setWorkflowStep('cleanliness');
  setStage('cleanlinessIntroStage');
}

function renderCleanlinessTransition() {
  stopCamera();
  runtime.draft.stage = 'cleanliness_transition';
  runtime.draft.photo_phase = 'cleanliness';
  saveDraft();
  setWorkflowStep('closing');
  setStage('cleanlinessTransitionStage');
}

function renderChecklist() {
  const items = runtime.scenario.checklist;
  $('#checklistList').innerHTML = items.length
    ? items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')
    : '<li class="empty">Дополнительного чек-листа для этого набора нет</li>';
  const closing = runtime.scenario.action === 'close';
  const hasCleanliness = cleanlinessQuestions().length > 0;
  $('#checklistEyebrow').textContent = closing && hasCleanliness
    ? '🌙 Этап 2 из 2 · Закрытие'
    : 'Перед началом';
  $('#checklistTitle').textContent = closing ? 'Чек-лист закрытия' : 'Чек-лист смены';
  $('#checklistDescription').textContent = closing && hasCleanliness
    ? 'Чистота готова. Проверьте пункты и завершите закрытие клуба.'
    : 'Проверьте пункты, затем переходите к вопросам.';
  $('#startQuestions').textContent = closing ? 'Начать закрытие' : 'Всё понятно — начать';
  runtime.draft.stage = 'checklist';
  runtime.draft.photo_phase = 'shift';
  saveDraft();
  if (closing) setWorkflowStep('closing');
  setStage('checklistStage');
}

function renderQuestion() {
  const questions = textQuestions();
  const editingIndex = runtime.editingAnswerQuestionId
    ? questions.findIndex((question) => question.id === runtime.editingAnswerQuestionId)
    : -1;
  if (!questions.length || (editingIndex < 0 && runtime.draft.text_index >= questions.length)) {
    startPhotoPhase();
    return;
  }
  const index = editingIndex >= 0 ? editingIndex : Math.max(0, runtime.draft.text_index);
  const question = questions[index];
  runtime.draft.stage = 'questions';
  runtime.draft.photo_phase = 'shift';
  runtime.draft.text_index = index;
  saveDraft();
  $('#questionProgress').textContent = editingIndex >= 0
    ? 'Изменение ответа'
    : `${runtime.scenario.action === 'close' ? '🌙 Закрытие · ' : ''}вопрос ${index + 1} из ${questions.length}`;
  $('#questionProgressBar').style.width = `${((index + 1) / questions.length) * 100}%`;
  $('#questionLabel').textContent = question.text;
  const input = $('#questionAnswer');
  input.value = runtime.draft.answers[question.id] || '';
  input.type = 'text';
  input.inputMode = question.type === 'num' ? 'numeric' : 'text';
  input.placeholder = question.type === 'num' ? 'Введите целое число' : 'Введите ответ';
  $('#questionHint').textContent = question.type === 'num'
    ? 'Допустимы только цифры'
    : 'Ответ обязателен';
  $('#previousQuestion').textContent = editingIndex >= 0
    ? 'К отчёту'
    : index ? 'Назад' : 'К списку';
  $('#questionSubmit').textContent = editingIndex >= 0 ? 'Сохранить' : 'Далее';
  setStage('questionStage');
  setTimeout(() => input.focus(), 80);
}

function startPhotoPhase() {
  runtime.draft.photo_phase = 'shift';
  runtime.draft.stage = 'photos';
  runtime.draft.photo_index = Math.min(
    runtime.draft.photo_ids.length,
    shiftPhotoQuestions().length,
  );
  saveDraft();
  renderPhotoReady();
}

function startCleanlinessPhotoPhase() {
  runtime.draft.photo_phase = 'cleanliness';
  runtime.draft.stage = 'photos';
  runtime.draft.photo_index = Math.min(
    runtime.draft.cleanliness_photo_ids.length,
    cleanlinessQuestions().length,
  );
  saveDraft();
  setWorkflowStep('cleanliness');
  renderPhotoReady();
}

function renderPhotoReady(reason = '') {
  const questions = photoQuestions();
  if (
    !questions.length
    || (!runtime.retakeQuestionId && runtime.draft.photo_index >= questions.length)
  ) {
    finishPhotoPhase();
    return;
  }
  const question = runtime.retakeQuestionId
    ? questions.find((item) => item.id === runtime.retakeQuestionId)
    : questions[runtime.draft.photo_index];
  const index = questions.findIndex((item) => item.id === question.id);
  const cleanliness = isCleanlinessPhase();
  const phaseLabel = cleanliness
    ? '✨ Чистота'
    : runtime.scenario.action === 'close'
      ? '🌙 Закрытие'
      : '🌅 Открытие';
  if (runtime.scenario.action === 'close') {
    setWorkflowStep(cleanliness ? 'cleanliness' : 'closing');
  }
  $('#photoStage').classList.toggle('cleanliness-card', cleanliness);
  $('#photoStageEyebrow').textContent = cleanliness
    ? '✨ Этап 1 из 2 · Чистота'
    : runtime.scenario.action === 'close'
      ? '🌙 Этап 2 из 2 · Закрытие'
      : 'Серия фотографий';
  $('#photoProgress').textContent = `${phaseLabel} · фото ${index + 1} из ${questions.length}`;
  $('#photoStageTitle').textContent = runtime.retakeQuestionId
    ? 'Переснять фотографию'
    : runtime.draft.photo_index ? 'Продолжить серию' : 'Камера готова';
  $('#photoStageText').textContent = reason || (
    runtime.retakeQuestionId
      ? 'Новая фотография заменит выбранную, остальные сохранятся.'
      : 'Камера откроется один раз и будет переходить между пунктами автоматически.'
  );
  $('#nextPhotoPrompt').textContent = question.text;
  $('#openCamera').textContent = runtime.draft.photo_index || runtime.retakeQuestionId
    ? 'Открыть камеру и продолжить'
    : 'Открыть камеру';
  $('#systemCamera').hidden = false;
  $('#batchPhotos').hidden = Boolean(runtime.retakeQuestionId);
  $('#batchPhotos').textContent = `Загрузить несколько фото · осталось ${questions.length - runtime.draft.photo_index}`;
  setStage('photoStage');
}

function releaseBatchReviewUrls() {
  runtime.batchReviewUrls.forEach((url) => URL.revokeObjectURL(url));
  runtime.batchReviewUrls = [];
}

function clearBatchSelection() {
  releaseBatchReviewUrls();
  runtime.batchItems = [];
  runtime.batchReplaceIndex = null;
  runtime.batchStartIndex = null;
  $('#batchPhotoInput').value = '';
  $('#batchReplaceInput').value = '';
}

function renderBatchOrder() {
  clearBatchSelection();
  const questions = photoQuestions();
  const photoIds = currentPhotoIds();
  $('#batchOrderEyebrow').textContent = isCleanlinessPhase()
    ? '✨ Этап 1 из 2 · Чистота'
    : runtime.scenario.action === 'close'
      ? '🌙 Этап 2 из 2 · Закрытие'
      : 'Перед выбором фотографий';
  $('#batchQuestionList').innerHTML = questions.map((question, index) => {
    const completed = photoIds.includes(question.id);
    const current = !completed && index === runtime.draft.photo_index;
    const className = completed ? 'completed' : current ? 'current' : 'pending';
    return `<li class="${className}"><i>${completed ? '✓' : index + 1}</i><span>${escapeHtml(question.text)}</span></li>`;
  }).join('');
  $('#chooseBatchPhotos').textContent = `Выбрать до ${questions.length - runtime.draft.photo_index} фото`;
  setStage('batchOrderStage');
}

function renderBatchReview() {
  releaseBatchReviewUrls();
  const questions = photoQuestions();
  $('#batchReviewEyebrow').textContent = isCleanlinessPhase()
    ? '✨ Этап 1 из 2 · Чистота'
    : runtime.scenario.action === 'close'
      ? '🌙 Этап 2 из 2 · Закрытие'
      : 'Перед сохранением';
  $('#batchReviewProgress').textContent = `${runtime.batchItems.length} фото · проверьте порядок`;
  $('#confirmBatchPhotos').textContent = `Сохранить ${runtime.batchItems.length} фото`;
  $('#batchReviewList').innerHTML = runtime.batchItems.map((item, index) => {
    const question = questions[runtime.batchStartIndex + index];
    const url = URL.createObjectURL(item.blob);
    runtime.batchReviewUrls.push(url);
    return `
      <article>
        <img src="${url}" alt="Фото ${index + 1}">
        <div class="batch-review-copy">
          <span>Фото ${index + 1}</span>
          <strong>${escapeHtml(question.text)}</strong>
          <div class="batch-review-controls">
            <button type="button" data-batch-move="up" data-batch-index="${index}" ${index ? '' : 'disabled'} aria-label="Поднять фотографию">↑</button>
            <button type="button" data-batch-move="down" data-batch-index="${index}" ${index + 1 < runtime.batchItems.length ? '' : 'disabled'} aria-label="Опустить фотографию">↓</button>
            <button type="button" data-batch-replace="${index}">Заменить</button>
          </div>
        </div>
      </article>`;
  }).join('');
  setStage('batchReviewStage');
}

function updateCameraInstruction() {
  const questions = photoQuestions();
  const question = runtime.retakeQuestionId
    ? questions.find((item) => item.id === runtime.retakeQuestionId)
    : questions[runtime.draft.photo_index];
  const index = questions.findIndex((item) => item.id === question.id);
  $('#cameraStage').classList.toggle('cleanliness-camera', isCleanlinessPhase());
  const phaseLabel = isCleanlinessPhase()
    ? '✨ Чистота'
    : runtime.scenario.action === 'close'
      ? '🌙 Закрытие'
      : '🌅 Открытие';
  $('#cameraProgress').textContent = `${phaseLabel} · фото ${index + 1} из ${questions.length}`;
  $('#cameraQuestion').textContent = question.text;
}

function stopCamera() {
  runtime.stream?.getTracks().forEach((track) => track.stop());
  runtime.stream = null;
  $('#cameraView').srcObject = null;
  $('#cameraStage').hidden = true;
  setCameraFeedback(false);
  try { tg?.enableVerticalSwipes?.(); } catch (_error) { /* Older clients. */ }
}

async function openCamera() {
  requestAppFullscreen();
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $('#systemCamera').hidden = false;
    renderPhotoReady('Встроенная камера недоступна. Выберите фото с телефона.');
    return;
  }
  $('#openCamera').disabled = true;
  try { tg?.disableVerticalSwipes?.(); } catch (_error) { /* Older clients. */ }
  try {
    runtime.stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1600 },
        height: { ideal: 1200 },
      },
      audio: false,
    });
    const video = $('#cameraView');
    video.srcObject = runtime.stream;
    await video.play();
    updateCameraInstruction();
    $('#cameraStage').hidden = false;
  } catch (error) {
    stopCamera();
    $('#systemCamera').hidden = false;
    renderPhotoReady('Telegram не дал встроенной камере доступ. Можно выбрать фото с телефона.');
    toast(error.message || 'Камера недоступна', true);
  } finally {
    $('#openCamera').disabled = false;
  }
}

function canvasBlob(canvas, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error('Не удалось сохранить фотографию')),
      'image/jpeg',
      quality,
    );
  });
}

async function jpegFromSource(source, width, height) {
  const maximumSide = 1280;
  const scale = Math.min(1, maximumSide / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  let blob = await canvasBlob(canvas, 0.78);
  if (blob.size > MAX_PHOTO_BYTES) blob = await canvasBlob(canvas, 0.64);
  if (blob.size > MAX_PHOTO_BYTES) {
    throw new Error('Фотография получилась слишком большой. Попробуйте ещё раз');
  }
  return blob;
}

async function capturePhoto() {
  if (runtime.capturing || !runtime.stream) return;
  const video = $('#cameraView');
  if (!video.videoWidth || !video.videoHeight) {
    toast('Камера ещё готовит изображение', true);
    return;
  }
  runtime.capturing = true;
  setCameraFeedback(true, 'Сохраняем фотографию…');
  try {
    const blob = await jpegFromSource(video, video.videoWidth, video.videoHeight);
    const outcome = await saveCapturedPhoto(blob);
    if (outcome === 'camera') {
      setCameraFeedback(true, 'Фото сохранено · следующий пункт', true);
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    runtime.capturing = false;
    setCameraFeedback(false);
  }
}

async function imageSource(file) {
  if (window.createImageBitmap) {
    try {
      let bitmap;
      try {
        bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      } catch (_optionError) {
        bitmap = await createImageBitmap(file);
      }
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        close: () => bitmap.close(),
      };
    } catch (_bitmapError) {
      // Fall through to Image for WebViews with partial createImageBitmap support.
    }
  }
  const url = URL.createObjectURL(file);
  const image = new Image();
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = () => reject(new Error('Не удалось прочитать фотографию'));
    image.src = url;
  });
  return {
    source: image,
    width: image.naturalWidth,
    height: image.naturalHeight,
    close: () => URL.revokeObjectURL(url),
  };
}

async function compressSystemPhoto(file) {
  if (!file?.type.startsWith('image/')) throw new Error('Выберите фотографию');
  const image = await imageSource(file);
  try {
    return await jpegFromSource(image.source, image.width, image.height);
  } finally {
    image.close();
  }
}

async function saveCapturedPhoto(blob) {
  const questions = photoQuestions();
  const question = runtime.retakeQuestionId
    ? questions.find((item) => item.id === runtime.retakeQuestionId)
    : questions[runtime.draft.photo_index];
  await putPhoto(question.id, blob);
  tg?.HapticFeedback?.notificationOccurred('success');

  if (runtime.retakeQuestionId) {
    runtime.retakeQuestionId = null;
    stopCamera();
    saveDraft();
    await renderReview();
    return 'review';
  }

  const photoIds = currentPhotoIds();
  if (!photoIds.includes(question.id)) {
    photoIds.push(question.id);
  }
  runtime.draft.photo_index += 1;
  saveDraft();
  if (runtime.draft.photo_index >= questions.length) {
    stopCamera();
    if (isCleanlinessPhase()) {
      renderCleanlinessTransition();
      return 'transition';
    }
    await renderReview();
    return 'review';
  } else if (runtime.stream) {
    updateCameraInstruction();
    return 'camera';
  } else {
    renderPhotoReady();
    return 'photo';
  }
}

function finishPhotoPhase() {
  if (isCleanlinessPhase()) renderCleanlinessTransition();
  else renderReview();
}

function releaseReviewUrls() {
  runtime.reviewUrls.forEach((url) => URL.revokeObjectURL(url));
  runtime.reviewUrls = [];
}

async function renderReview() {
  stopCamera();
  runtime.editingAnswerQuestionId = null;
  runtime.draft.stage = 'review';
  runtime.draft.photo_phase = 'shift';
  saveDraft();
  if (runtime.scenario.action === 'close') setWorkflowStep('closing');
  const questions = textQuestions();
  $('#answerReview').innerHTML = questions.length
    ? questions.map((question) => `
      <article>
        <div class="answer-review-copy"><span>${escapeHtml(question.text)}</span><strong>${escapeHtml(runtime.draft.answers[question.id] || '—')}</strong></div>
        <button type="button" data-edit-answer="${question.id}">Изменить</button>
      </article>
    `).join('')
    : '<article><div class="answer-review-copy"><span>Текстовых вопросов нет</span><strong>Можно отправлять фотографии</strong></div></article>';
  $('#editAnswers').hidden = !questions.length;

  releaseReviewUrls();

  async function renderPhotoReview(questionsToRender, selector, phase) {
    const photoItems = [];
    for (const question of questionsToRender) {
      const record = await getPhoto(question.id);
      if (!record?.blob) continue;
      const url = URL.createObjectURL(record.blob);
      runtime.reviewUrls.push(url);
      photoItems.push(`
        <article>
          <img src="${url}" alt="${escapeHtml(question.text)}">
          <div><span>${escapeHtml(question.text)}</span><button type="button" data-retake="${question.id}" data-retake-phase="${phase}">Переснять</button></div>
        </article>
      `);
    }
    $(selector).innerHTML = photoItems.length
      ? photoItems.join('')
      : '<div class="empty-review">В этом сценарии нет фотографий</div>';
  }

  const closing = runtime.scenario.action === 'close';
  const hasCleanliness = cleanlinessQuestions().length > 0;
  $('#cleanlinessReviewSection').hidden = !closing || !hasCleanliness;
  if (closing && hasCleanliness) {
    await renderPhotoReview(
      cleanlinessQuestions(), '#cleanlinessPhotoReview', 'cleanliness',
    );
  }
  await renderPhotoReview(shiftPhotoQuestions(), '#photoReview', 'shift');
  setStage('reviewStage');
}

async function repairDraftPhotos() {
  async function validPhotoIds(questions, storedIds) {
    const validIds = [];
    for (const question of questions) {
      if (!storedIds.includes(question.id)) break;
      const record = await getPhoto(question.id);
      if (!record?.blob) break;
      validIds.push(question.id);
    }
    return validIds;
  }

  runtime.draft.cleanliness_photo_ids = await validPhotoIds(
    cleanlinessQuestions(), runtime.draft.cleanliness_photo_ids,
  );
  runtime.draft.photo_ids = await validPhotoIds(
    shiftPhotoQuestions(), runtime.draft.photo_ids,
  );
  if (!cleanlinessQuestions().length && runtime.draft.photo_phase === 'cleanliness') {
    runtime.draft.stage = 'checklist';
    runtime.draft.photo_phase = 'shift';
    runtime.draft.photo_index = 0;
  }
  if (
    runtime.scenario.action === 'close'
    && runtime.draft.stage !== 'cleanliness_intro'
    && runtime.draft.cleanliness_photo_ids.length !== cleanlinessQuestions().length
  ) {
    runtime.draft.stage = 'photos';
    runtime.draft.photo_phase = 'cleanliness';
  } else if (
    runtime.draft.stage === 'review'
    && runtime.draft.photo_ids.length !== shiftPhotoQuestions().length
  ) {
    runtime.draft.stage = 'photos';
    runtime.draft.photo_phase = 'shift';
  }
  if (runtime.draft.stage === 'photos') {
    runtime.draft.photo_index = currentPhotoIds().length;
  }
  saveDraft();
}

function renderSavedStage() {
  if (runtime.draft.stage === 'cleanliness_intro') renderCleanlinessIntro();
  else if (runtime.draft.stage === 'cleanliness_transition') renderCleanlinessTransition();
  else if (runtime.draft.stage === 'questions') renderQuestion();
  else if (runtime.draft.stage === 'photos') renderPhotoReady();
  else if (runtime.draft.stage === 'review') renderReview();
  else renderChecklist();
}

function renderInitialStage() {
  if (runtime.scenario.action === 'close' && cleanlinessQuestions().length) renderCleanlinessIntro();
  else renderChecklist();
}

function createDraft() {
  const scenario = runtime.scenario;
  const createdAt = new Date().toISOString();
  const entropy = crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  runtime.draft = {
    schema: DRAFT_SCHEMA,
    id: scenario.run_id || `${scenario.shift.date}:${scenario.action}:${entropy}`,
    action: scenario.action,
    user_login: scenario.user_login,
    date: scenario.shift.date,
    club: scenario.club,
    variant_index: scenario.variant_index,
    version: scenario.version,
    stage: 'checklist',
    photo_phase: scenario.action === 'close' && cleanlinessQuestions().length
      ? 'cleanliness'
      : 'shift',
    text_index: 0,
    photo_index: 0,
    answers: {},
    photo_ids: [],
    cleanliness_photo_ids: [],
    started_at: scenario.started_at || null,
    created_at: createdAt,
    updated_at: createdAt,
  };
  saveDraft();
}

function compatibleDraft(draft) {
  if (!draft || draft.schema !== DRAFT_SCHEMA) return false;
  const scenario = runtime.scenario;
  const age = Date.now() - Date.parse(draft.updated_at || draft.created_at || 0);
  return age >= 0
    && age <= scenario.draft_ttl_hours * 60 * 60 * 1000
    && draft.action === scenario.action
    && draft.user_login === scenario.user_login
    && draft.date === scenario.shift.date
    && draft.club === scenario.club
    && Number(draft.variant_index) === scenario.variant_index
    && draft.version === scenario.version
    && typeof draft.answers === 'object'
    && Array.isArray(draft.photo_ids)
    && Array.isArray(draft.cleanliness_photo_ids)
    && ['cleanliness', 'shift'].includes(draft.photo_phase);
}

async function fetchScenario(variant = null, club = '', runId = '') {
  const query = new URLSearchParams({ action: runtime.action });
  if (variant !== null && variant !== undefined) query.set('variant', variant);
  if (club) query.set('club', club);
  if (runId) query.set('run_id', runId);
  return api(`/api/shift-test/scenario?${query}`);
}

async function startFreshScenario() {
  const response = await fetchScenario();
  if (response.requires_club_selection) {
    runtime.scenario = null;
    renderOwnerClubSelection(response);
    return;
  }
  runtime.scenario = response;
  setPageCopy();
  createDraft();
  renderInitialStage();
}

function showError(error) {
  stopCamera();
  $('#loadingCard').hidden = true;
  $('#errorTitle').textContent = error.message || 'Неизвестная ошибка';
  $('#errorText').textContent = runtime.action === 'open' || runtime.action === 'close'
    ? 'Проверьте сегодняшнее расписание или настройки сценария.'
    : 'Откройте нужное действие кнопкой из модуля OMG Shift.';
  setStage('errorCard');
}

function continueAfterStart() {
  if (
    runtime.scenario.action === 'close'
    && runtime.draft.cleanliness_photo_ids.length < cleanlinessQuestions().length
  ) {
    startCleanlinessPhotoPhase();
    return;
  }
  runtime.draft.text_index = 0;
  if (textQuestions().length) renderQuestion();
  else startPhotoPhase();
}

async function beginShift(earlyConfirmed = false) {
  if (runtime.draft.started_at) {
    continueAfterStart();
    return;
  }
  const cleanlinessStart = runtime.scenario.action === 'close'
    && runtime.draft.cleanliness_photo_ids.length < cleanlinessQuestions().length;
  const button = cleanlinessStart ? $('#startCleanliness') : $('#startQuestions');
  const idleLabel = cleanlinessStart
    ? 'Начать отчёт о чистоте'
    : runtime.scenario.action === 'close'
      ? 'Начать закрытие'
      : 'Всё понятно — начать';
  button.disabled = true;
  button.textContent = 'Начинаем…';
  try {
    const result = await api('/api/shift-test/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        run_id: runtime.draft.id,
        action: runtime.scenario.action,
        club: runtime.scenario.club,
        variant_index: runtime.scenario.variant_index,
        version: runtime.scenario.version,
        early_confirmed: earlyConfirmed,
      }),
    });
    runtime.draft.started_at = result.started_at;
    runtime.draft.early_close = Boolean(result.early_close);
    saveDraft();
    $('#earlyCloseDialog').close();
    if (result.task_warning?.count) {
      $('#taskWarningText').textContent = result.task_warning.message;
      $('#taskWarningList').replaceChildren(...result.task_warning.titles.map((title) => {
        const item = document.createElement('li');
        item.textContent = title;
        return item;
      }));
      $('#taskWarningDialog').showModal();
      return;
    }
    continueAfterStart();
  } catch (error) {
    if (error.code === 'early_close_confirmation_required') {
      $('#earlyCloseDialog').showModal();
    } else {
      toast(error.message, true);
    }
  } finally {
    button.disabled = false;
    button.textContent = idleLabel;
  }
}

async function submitReport() {
  const button = $('#sendReport');
  button.disabled = true;
  button.textContent = 'Отправляем…';
  try {
    const form = new FormData();
    form.set('report', JSON.stringify({
      action: runtime.scenario.action,
      club: runtime.scenario.club,
      variant_index: runtime.scenario.variant_index,
      version: runtime.scenario.version,
      run_id: runtime.draft.id,
      answers: runtime.draft.answers,
      photo_ids: runtime.draft.photo_ids,
      cleanliness_photo_ids: runtime.draft.cleanliness_photo_ids,
    }));
    for (const question of cleanlinessQuestions()) {
      const record = await getPhoto(question.id);
      if (!record?.blob) throw new Error(`Не найдена фотография чистоты: ${question.text}`);
      form.append('cleanliness_photos', record.blob, `${question.id}.jpg`);
    }
    for (const question of shiftPhotoQuestions()) {
      const record = await getPhoto(question.id);
      if (!record?.blob) throw new Error(`Не найдена фотография: ${question.text}`);
      form.append('photos', record.blob, `${question.id}.jpg`);
    }
    await uploadForm('/api/shift-test/submit', form, (progress) => {
      button.textContent = progress < 100
        ? `Загружаем фотографии · ${progress}%`
        : 'Фотографии загружены · отправляем отчёт';
    });
    await deleteDraft();
    runtime.draft = null;
    releaseReviewUrls();
    setStage('successStage');
    tg?.HapticFeedback?.notificationOccurred('success');
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.textContent = 'Завершить отчёт';
  }
}

$('#startCleanliness').addEventListener('click', () => beginShift());
$('#continueToClosing').addEventListener('click', renderChecklist);
$('#startQuestions').addEventListener('click', () => beginShift());
$('#cancelEarlyClose').addEventListener('click', () => {
  $('#earlyCloseDialog').close();
});
$('#confirmEarlyClose').addEventListener('click', () => beginShift(true));
$('#acknowledgeTaskWarning').addEventListener('click', () => {
  $('#taskWarningDialog').close();
  continueAfterStart();
});

$('#questionForm').addEventListener('submit', (event) => {
  event.preventDefault();
  const question = textQuestions()[runtime.draft.text_index];
  const value = $('#questionAnswer').value.trim();
  if (!value) {
    toast('Введите ответ', true);
    return;
  }
  if (question.type === 'num' && !/^\d+$/u.test(value)) {
    toast('Здесь нужно целое число', true);
    return;
  }
  runtime.draft.answers[question.id] = value;
  if (runtime.editingAnswerQuestionId) {
    runtime.editingAnswerQuestionId = null;
    saveDraft();
    renderReview();
    return;
  }
  runtime.draft.text_index += 1;
  saveDraft();
  renderQuestion();
});

$('#previousQuestion').addEventListener('click', () => {
  if (runtime.editingAnswerQuestionId) {
    runtime.editingAnswerQuestionId = null;
    renderReview();
    return;
  }
  if (runtime.draft.text_index > 0) {
    runtime.draft.text_index -= 1;
    renderQuestion();
  } else renderChecklist();
});

$('#openCamera').addEventListener('click', openCamera);
$('#systemCamera').addEventListener('click', () => $('#systemPhoto').click());
$('#batchPhotos').addEventListener('click', renderBatchOrder);
$('#cameraFileButton').addEventListener('click', () => $('#systemPhoto').click());
$('#closeCamera').addEventListener('click', () => {
  stopCamera();
  renderPhotoReady('Серия сохранена. Можно продолжить с этого же пункта.');
});
$('#shutter').addEventListener('click', capturePhoto);

$('#systemPhoto').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  const cameraVisible = Boolean(runtime.stream && !$('#cameraStage').hidden);
  if (cameraVisible) setCameraFeedback(true, 'Обрабатываем фотографию…');
  else setPhotoProcessing(true, 'Обрабатываем фотографию…');
  try {
    const blob = await compressSystemPhoto(file);
    const outcome = await saveCapturedPhoto(blob);
    if (outcome === 'camera') {
      setCameraFeedback(true, 'Фото сохранено · следующий пункт', true);
    } else if (outcome === 'photo') {
      setPhotoProcessing(true, 'Фото сохранено · следующий пункт', true);
    } else if (outcome === 'transition') {
      toast('Фотографии чистоты готовы');
    } else {
      toast('Фотография заменена');
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    setCameraFeedback(false);
    setPhotoProcessing(false);
  }
});

$('#batchPhotoInput').addEventListener('change', async (event) => {
  const files = [...event.target.files];
  event.target.value = '';
  if (!files.length) return;
  const questions = photoQuestions();
  const remaining = questions.slice(runtime.draft.photo_index);
  if (files.length > remaining.length) {
    toast(`Осталось только ${remaining.length} фотопунктов`, true);
    return;
  }
  const button = $('#chooseBatchPhotos');
  button.disabled = true;
  button.textContent = `Обрабатываем 0 из ${files.length}`;
  runtime.batchStartIndex = runtime.draft.photo_index;
  runtime.batchItems = [];
  try {
    for (let index = 0; index < files.length; index += 1) {
      const blob = await compressSystemPhoto(files[index]);
      runtime.batchItems.push({ blob, filename: files[index].name || `photo-${index + 1}.jpg` });
      button.textContent = `Обрабатываем ${index + 1} из ${files.length}`;
    }
    renderBatchReview();
  } catch (error) {
    clearBatchSelection();
    renderBatchOrder();
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('#cancelBatchOrder').addEventListener('click', () => renderPhotoReady());
$('#chooseBatchPhotos').addEventListener('click', () => $('#batchPhotoInput').click());
$('#cancelBatchReview').addEventListener('click', () => {
  clearBatchSelection();
  renderPhotoReady();
});

$('#batchReviewList').addEventListener('click', (event) => {
  const move = event.target.closest('[data-batch-move]');
  if (move) {
    const index = Number(move.dataset.batchIndex);
    const nextIndex = move.dataset.batchMove === 'up' ? index - 1 : index + 1;
    if (nextIndex < 0 || nextIndex >= runtime.batchItems.length) return;
    [runtime.batchItems[index], runtime.batchItems[nextIndex]] = [
      runtime.batchItems[nextIndex], runtime.batchItems[index],
    ];
    renderBatchReview();
    tg?.HapticFeedback?.selectionChanged();
    return;
  }
  const replace = event.target.closest('[data-batch-replace]');
  if (replace) {
    runtime.batchReplaceIndex = Number(replace.dataset.batchReplace);
    $('#batchReplaceInput').value = '';
    $('#batchReplaceInput').click();
  }
});

$('#batchReplaceInput').addEventListener('change', async (event) => {
  const file = event.target.files[0];
  event.target.value = '';
  const index = runtime.batchReplaceIndex;
  runtime.batchReplaceIndex = null;
  if (!file || index === null || !runtime.batchItems[index]) return;
  try {
    const blob = await compressSystemPhoto(file);
    runtime.batchItems[index] = { blob, filename: file.name || `photo-${index + 1}.jpg` };
    renderBatchReview();
    tg?.HapticFeedback?.notificationOccurred('success');
  } catch (error) {
    toast(error.message, true);
  }
});

$('#confirmBatchPhotos').addEventListener('click', async () => {
  if (!runtime.batchItems.length || runtime.batchStartIndex !== runtime.draft.photo_index) {
    clearBatchSelection();
    renderPhotoReady('Порядок фотопунктов изменился. Выберите пачку ещё раз.');
    return;
  }
  const questions = photoQuestions();
  const button = $('#confirmBatchPhotos');
  button.disabled = true;
  let saved = 0;
  try {
    for (let index = 0; index < runtime.batchItems.length; index += 1) {
      const question = questions[runtime.draft.photo_index];
      await putPhoto(question.id, runtime.batchItems[index].blob);
      const photoIds = currentPhotoIds();
      if (!photoIds.includes(question.id)) {
        photoIds.push(question.id);
      }
      runtime.draft.photo_index += 1;
      saved += 1;
      saveDraft();
      button.textContent = `Сохраняем ${saved} из ${runtime.batchItems.length}`;
    }
    clearBatchSelection();
    tg?.HapticFeedback?.notificationOccurred('success');
    if (runtime.draft.photo_index >= questions.length) finishPhotoPhase();
    else renderPhotoReady(`${saved} фото сохранено. Продолжайте со следующего пункта.`);
  } catch (error) {
    clearBatchSelection();
    renderPhotoReady(saved
      ? `${saved} фото сохранено. Продолжайте с текущего пункта.`
      : 'Пачка не сохранилась. Попробуйте выбрать фотографии ещё раз.');
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

function retakePhoto(event) {
  const button = event.target.closest('[data-retake]');
  if (!button) return;
  runtime.draft.photo_phase = button.dataset.retakePhase;
  runtime.draft.photo_index = currentPhotoIds().length;
  runtime.retakeQuestionId = button.dataset.retake;
  renderPhotoReady();
}

$('#photoReview').addEventListener('click', retakePhoto);
$('#cleanlinessPhotoReview').addEventListener('click', retakePhoto);

$('#answerReview').addEventListener('click', (event) => {
  const button = event.target.closest('[data-edit-answer]');
  if (!button) return;
  const question = textQuestions().find((item) => item.id === button.dataset.editAnswer);
  if (!question) {
    toast('Не удалось найти выбранный вопрос', true);
    return;
  }
  runtime.editingAnswerQuestionId = question.id;
  renderQuestion();
});

$('#editAnswers').addEventListener('click', () => {
  runtime.editingAnswerQuestionId = null;
  runtime.draft.text_index = 0;
  renderQuestion();
});
$('#sendReport').addEventListener('click', submitReport);

$('#resumeDraft').addEventListener('click', () => {
  $('#resumeDialog').close();
  renderSavedStage();
});

$('#discardDraft').addEventListener('click', async () => {
  const button = $('#discardDraft');
  button.disabled = true;
  try {
    $('#resumeDialog').close();
    if (runtime.draft.started_at) {
      await clearDraftPhotos(runtime.draft);
      clearBatchSelection();
      releaseReviewUrls();
      runtime.retakeQuestionId = null;
      runtime.draft.stage = 'checklist';
      runtime.draft.text_index = 0;
      runtime.draft.photo_index = 0;
      runtime.draft.answers = {};
      runtime.draft.photo_ids = [];
      runtime.draft.cleanliness_photo_ids = [];
      runtime.draft.photo_phase = runtime.scenario.action === 'close'
        && cleanlinessQuestions().length
        ? 'cleanliness'
        : 'shift';
      saveDraft();
      renderInitialStage();
      toast('Черновик очищен. Начало смены сохранено.');
    } else {
      await deleteDraft();
      await startFreshScenario();
    }
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

window.addEventListener('omg:navigation-back', (event) => {
  event.preventDefault();
  if (!$('#batchReviewStage').hidden) {
    renderBatchOrder();
    return;
  }
  if (!$('#batchOrderStage').hidden) {
    renderPhotoReady();
    return;
  }
  stopCamera();
  window.location.assign('/shift');
});
window.addEventListener('pagehide', () => {
  stopCamera();
  releaseReviewUrls();
  releaseBatchReviewUrls();
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden && runtime.stream) {
    stopCamera();
    renderPhotoReady('Серия сохранена. Нажмите кнопку, чтобы продолжить.');
  }
});

async function initialize() {
  if (!['open', 'close'].includes(runtime.action)) {
    throw new Error('Не выбрано открытие или закрытие');
  }
  runtime.photoDatabase = await openPhotoDatabase();
  const localDraft = loadLocalDraft();
  const requestedVariant = localDraft?.schema === DRAFT_SCHEMA
    && localDraft.action === runtime.action
    ? localDraft.variant_index
    : null;
  try {
    runtime.scenario = await fetchScenario(
      requestedVariant,
      localDraft?.club || '',
      localDraft?.schema === DRAFT_SCHEMA
        && localDraft.action === runtime.action
        ? localDraft.id
        : '',
    );
  } catch (error) {
    if (!localDraft) throw error;
    if (localDraft.started_at) throw error;
    await deleteDraft(localDraft);
    runtime.scenario = await fetchScenario();
  }
  if (runtime.scenario.requires_club_selection) {
    const selection = runtime.scenario;
    runtime.scenario = null;
    renderOwnerClubSelection(selection);
    return;
  }
  setPageCopy();
  $('#loadingCard').hidden = true;

  if (!compatibleDraft(localDraft)) {
    if (localDraft) await deleteDraft(localDraft);
    createDraft();
    renderInitialStage();
    return;
  }

  runtime.draft = localDraft;
  if (!runtime.draft.started_at && runtime.scenario.started_at) {
    runtime.draft.started_at = runtime.scenario.started_at;
    saveDraft();
  }
  await repairDraftPhotos();
  const answered = Object.keys(runtime.draft.answers).length;
  const photos = runtime.draft.photo_ids.length
    + runtime.draft.cleanliness_photo_ids.length;
  $('#resumeDescription').textContent = `${runtime.scenario.club}: ответов ${answered}, фотографий ${photos}. Черновик хранится только на этом устройстве.`;
  $('#discardDraft').hidden = false;
  $('#resumeDialog').showModal();
}

initialize().catch(showError);
