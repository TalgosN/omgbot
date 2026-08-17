const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const DRAFT_SCHEMA = 2;
const DRAFT_PREFIX = 'omg-shift-report-draft-v2:';
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
  reviewUrls: [],
};

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#031b22');
tg?.setBackgroundColor('#031b22');

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

function photoQuestions() {
  return runtime.scenario.questions.filter((question) => question.type === 'photo');
}

function setStage(stageId) {
  ['loadingCard', 'errorCard', 'ownerClubStage', 'checklistStage', 'questionStage', 'photoStage', 'reviewStage', 'successStage']
    .forEach((id) => { $(`#${id}`).hidden = id !== stageId; });
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
        renderChecklist();
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
  $('#pageTitle').textContent = closing ? 'ЗАКРЫТИЕ СМЕНЫ' : 'ОТКРЫТИЕ СМЕНЫ';
  $('#pageDescription').textContent = `${runtime.scenario.club} · ${formatDate(runtime.scenario.shift.date)} · набор ${runtime.scenario.variant_label}`;
  $$('[data-club]').forEach((element) => { element.textContent = runtime.scenario.club; });
  $('#variantLabel').textContent = `Набор ${runtime.scenario.variant_label}`;
}

function formatDate(value) {
  const [year, month, day] = String(value || '').split('-');
  return year && month && day ? `${day}.${month}.${year}` : value;
}

function renderChecklist() {
  const items = runtime.scenario.checklist;
  $('#checklistList').innerHTML = items.length
    ? items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')
    : '<li class="empty">Дополнительного чек-листа для этого набора нет</li>';
  runtime.draft.stage = 'checklist';
  saveDraft();
  setStage('checklistStage');
}

function renderQuestion() {
  const questions = textQuestions();
  if (!questions.length || runtime.draft.text_index >= questions.length) {
    startPhotoPhase();
    return;
  }
  const index = Math.max(0, runtime.draft.text_index);
  const question = questions[index];
  runtime.draft.stage = 'questions';
  runtime.draft.text_index = index;
  saveDraft();
  $('#questionProgress').textContent = `Вопрос ${index + 1} из ${questions.length}`;
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
  $('#previousQuestion').textContent = index ? 'Назад' : 'К списку';
  setStage('questionStage');
  setTimeout(() => input.focus(), 80);
}

function startPhotoPhase() {
  runtime.draft.stage = 'photos';
  runtime.draft.photo_index = Math.min(
    runtime.draft.photo_ids.length,
    photoQuestions().length,
  );
  saveDraft();
  renderPhotoReady();
}

function renderPhotoReady(reason = '') {
  const questions = photoQuestions();
  if (!questions.length || runtime.draft.photo_index >= questions.length) {
    renderReview();
    return;
  }
  const question = runtime.retakeQuestionId
    ? questions.find((item) => item.id === runtime.retakeQuestionId)
    : questions[runtime.draft.photo_index];
  const index = questions.findIndex((item) => item.id === question.id);
  $('#photoProgress').textContent = `Фото ${index + 1} из ${questions.length}`;
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

function updateCameraInstruction() {
  const questions = photoQuestions();
  const question = runtime.retakeQuestionId
    ? questions.find((item) => item.id === runtime.retakeQuestionId)
    : questions[runtime.draft.photo_index];
  const index = questions.findIndex((item) => item.id === question.id);
  $('#cameraProgress').textContent = `Фото ${index + 1} из ${questions.length}`;
  $('#cameraQuestion').textContent = question.text;
}

function stopCamera() {
  runtime.stream?.getTracks().forEach((track) => track.stop());
  runtime.stream = null;
  $('#cameraView').srcObject = null;
  $('#cameraStage').hidden = true;
}

async function openCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $('#systemCamera').hidden = false;
    renderPhotoReady('Встроенная камера недоступна. Выберите фото с телефона.');
    return;
  }
  $('#openCamera').disabled = true;
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
  const maximumSide = 1600;
  const scale = Math.min(1, maximumSide / Math.max(width, height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
  let blob = await canvasBlob(canvas, 0.82);
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
  $('#shutter').disabled = true;
  $('#cameraSaving').hidden = false;
  try {
    const blob = await jpegFromSource(video, video.videoWidth, video.videoHeight);
    await saveCapturedPhoto(blob);
  } catch (error) {
    toast(error.message, true);
  } finally {
    runtime.capturing = false;
    $('#shutter').disabled = false;
    $('#cameraSaving').hidden = true;
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
    return;
  }

  if (!runtime.draft.photo_ids.includes(question.id)) {
    runtime.draft.photo_ids.push(question.id);
  }
  runtime.draft.photo_index += 1;
  saveDraft();
  if (runtime.draft.photo_index >= questions.length) {
    stopCamera();
    await renderReview();
  } else if (runtime.stream) {
    updateCameraInstruction();
  } else {
    renderPhotoReady();
  }
}

function releaseReviewUrls() {
  runtime.reviewUrls.forEach((url) => URL.revokeObjectURL(url));
  runtime.reviewUrls = [];
}

async function renderReview() {
  stopCamera();
  runtime.draft.stage = 'review';
  saveDraft();
  const questions = textQuestions();
  $('#answerReview').innerHTML = questions.length
    ? questions.map((question) => `
      <article><span>${escapeHtml(question.text)}</span><strong>${escapeHtml(runtime.draft.answers[question.id] || '—')}</strong></article>
    `).join('')
    : '<article><span>Текстовых вопросов нет</span><strong>Можно отправлять фотографии</strong></article>';
  $('#editAnswers').hidden = !questions.length;

  releaseReviewUrls();
  const photoItems = [];
  for (const question of photoQuestions()) {
    const record = await getPhoto(question.id);
    if (!record?.blob) continue;
    const url = URL.createObjectURL(record.blob);
    runtime.reviewUrls.push(url);
    photoItems.push(`
      <article>
        <img src="${url}" alt="${escapeHtml(question.text)}">
        <div><span>${escapeHtml(question.text)}</span><button type="button" data-retake="${question.id}">Переснять</button></div>
      </article>
    `);
  }
  $('#photoReview').innerHTML = photoItems.length
    ? photoItems.join('')
    : '<div class="empty-review">В этом сценарии нет фотографий</div>';
  setStage('reviewStage');
}

async function repairDraftPhotos() {
  const questions = photoQuestions();
  const validIds = [];
  for (const question of questions) {
    if (!runtime.draft.photo_ids.includes(question.id)) break;
    const record = await getPhoto(question.id);
    if (!record?.blob) break;
    validIds.push(question.id);
  }
  runtime.draft.photo_ids = validIds;
  runtime.draft.photo_index = validIds.length;
  if (runtime.draft.stage === 'review' && validIds.length !== questions.length) {
    runtime.draft.stage = 'photos';
  }
  saveDraft();
}

function renderSavedStage() {
  if (runtime.draft.stage === 'questions') renderQuestion();
  else if (runtime.draft.stage === 'photos') renderPhotoReady();
  else if (runtime.draft.stage === 'review') renderReview();
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
    id: `${scenario.shift.date}:${scenario.action}:${entropy}`,
    action: scenario.action,
    user_login: scenario.user_login,
    date: scenario.shift.date,
    club: scenario.club,
    variant_index: scenario.variant_index,
    version: scenario.version,
    stage: 'checklist',
    text_index: 0,
    photo_index: 0,
    answers: {},
    photo_ids: [],
    started_at: null,
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
    && Array.isArray(draft.photo_ids);
}

async function fetchScenario(variant = null, club = '') {
  const query = new URLSearchParams({ action: runtime.action });
  if (variant !== null && variant !== undefined) query.set('variant', variant);
  if (club) query.set('club', club);
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
  renderChecklist();
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
  runtime.draft.text_index = 0;
  if (textQuestions().length) renderQuestion();
  else startPhotoPhase();
}

async function beginShift(earlyConfirmed = false) {
  if (runtime.draft.started_at) {
    continueAfterStart();
    return;
  }
  const button = $('#startQuestions');
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
    continueAfterStart();
  } catch (error) {
    if (error.code === 'early_close_confirmation_required') {
      $('#earlyCloseDialog').showModal();
    } else {
      toast(error.message, true);
    }
  } finally {
    button.disabled = false;
    button.textContent = 'Всё понятно — начать';
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
    }));
    for (const question of photoQuestions()) {
      const record = await getPhoto(question.id);
      if (!record?.blob) throw new Error(`Не найдена фотография: ${question.text}`);
      form.append('photos', record.blob, `${question.id}.jpg`);
    }
    await api('/api/shift-test/submit', { method: 'POST', body: form });
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

$('#startQuestions').addEventListener('click', () => beginShift());
$('#cancelEarlyClose').addEventListener('click', () => {
  $('#earlyCloseDialog').close();
});
$('#confirmEarlyClose').addEventListener('click', () => beginShift(true));

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
  runtime.draft.text_index += 1;
  saveDraft();
  renderQuestion();
});

$('#previousQuestion').addEventListener('click', () => {
  if (runtime.draft.text_index > 0) {
    runtime.draft.text_index -= 1;
    renderQuestion();
  } else renderChecklist();
});

$('#openCamera').addEventListener('click', openCamera);
$('#systemCamera').addEventListener('click', () => $('#systemPhoto').click());
$('#batchPhotos').addEventListener('click', () => $('#batchPhotoInput').click());
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
  $('#systemCamera').disabled = true;
  try {
    const blob = await compressSystemPhoto(file);
    await saveCapturedPhoto(blob);
  } catch (error) {
    toast(error.message, true);
  } finally {
    $('#systemCamera').disabled = false;
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
  const button = $('#batchPhotos');
  button.disabled = true;
  button.textContent = `Обрабатываем 0 из ${files.length}`;
  let saved = 0;
  try {
    for (let index = 0; index < files.length; index += 1) {
      const question = remaining[index];
      const blob = await compressSystemPhoto(files[index]);
      await putPhoto(question.id, blob);
      if (!runtime.draft.photo_ids.includes(question.id)) {
        runtime.draft.photo_ids.push(question.id);
      }
      runtime.draft.photo_index += 1;
      saved += 1;
      saveDraft();
      button.textContent = `Обрабатываем ${saved} из ${files.length}`;
    }
    tg?.HapticFeedback?.notificationOccurred('success');
    if (runtime.draft.photo_index >= questions.length) await renderReview();
    else renderPhotoReady(`${saved} фото загружено. Следующее фото можно снять или выбрать.`);
  } catch (error) {
    renderPhotoReady(saved
      ? `${saved} фото сохранено. Остальные не загрузились — можно продолжить с текущего пункта.`
      : 'Фото не загрузились. Попробуйте выбрать их ещё раз.');
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('#photoReview').addEventListener('click', (event) => {
  const button = event.target.closest('[data-retake]');
  if (!button) return;
  runtime.retakeQuestionId = button.dataset.retake;
  renderPhotoReady();
});

$('#editAnswers').addEventListener('click', () => {
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
    await deleteDraft();
    await startFreshScenario();
  } catch (error) {
    showError(error);
  } finally {
    button.disabled = false;
  }
});

window.addEventListener('omg:navigation-back', (event) => {
  event.preventDefault();
  stopCamera();
  window.location.assign('/shift');
});
window.addEventListener('pagehide', () => {
  stopCamera();
  releaseReviewUrls();
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
    runtime.scenario = await fetchScenario(requestedVariant, localDraft?.club || '');
  } catch (error) {
    if (!localDraft) throw error;
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
    renderChecklist();
    return;
  }

  runtime.draft = localDraft;
  await repairDraftPhotos();
  const answered = Object.keys(runtime.draft.answers).length;
  const photos = runtime.draft.photo_ids.length;
  $('#resumeDescription').textContent = `${runtime.scenario.club}: ответов ${answered}, фотографий ${photos}. Черновик хранится только на этом устройстве.`;
  $('#discardDraft').hidden = Boolean(runtime.draft.started_at);
  $('#resumeDialog').showModal();
}

initialize().catch(showError);
