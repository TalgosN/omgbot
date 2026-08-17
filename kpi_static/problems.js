const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const PROBLEM_HOLD_DELAY_MS = 420;
const PROBLEM_MAX_VIDEO_MS = 15000;
const state = {
  me: null, meta: null, status: 'work', tasks: [], selected: null,
  action: null, repairCatalog: null, migration: null, mappingTask: null,
  boardView: 'tasks', analyticsMode: 'month', analytics: null,
  problemMedia: null, problemMediaUrl: null, problemCameraStream: null,
  problemRecorder: null, problemRecorderChunks: [], problemPressTimer: null,
  problemRecordingTimer: null, problemRecordingTimeout: null,
  problemRecordingStartedAt: 0, problemPointerActive: false,
  problemHoldTriggered: false, problemDiscardRecording: false,
  problemCameraReturnToForm: false,
};
tg?.ready();
tg?.expand();
if (tg) {
  tg.setHeaderColor('#100524');
  tg.setBackgroundColor('#09031d');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}
function dateLabel(value) {
  if (!value) return '—';
  const [year, month, day] = value.split('-');
  return `${day}.${month}.${year}`;
}
function dateTimeLabel(value) {
  if (!value) return '—';
  const [date, time = ''] = value.split('T');
  return `${dateLabel(date)}${time ? ` · ${time.slice(0, 5)}` : ''}`;
}
async function api(path, options = {}) {
  const headers = { 'X-Telegram-Init-Data': tg?.initData || '', ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Ошибка сервера');
  return payload;
}
function toast(message, error = false) {
  const element = $('#toast');
  element.textContent = message;
  element.className = `problem-toast visible${error ? ' error' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { element.className = 'problem-toast'; }, 2600);
}

function problemMediaSize(size) {
  return size >= 1024 * 1024
    ? `${(size / (1024 * 1024)).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(size / 1024))} КБ`;
}

function renderProblemMedia() {
  const preview = $('#problemMediaPreview');
  if (!state.problemMedia) {
    preview.classList.add('hidden');
    preview.replaceChildren();
    return;
  }
  if (state.problemMediaUrl) URL.revokeObjectURL(state.problemMediaUrl);
  state.problemMediaUrl = URL.createObjectURL(state.problemMedia.blob);
  const media = state.problemMedia.kind === 'photo'
    ? `<img src="${state.problemMediaUrl}" alt="Фото проблемы">`
    : `<video src="${state.problemMediaUrl}" controls muted playsinline preload="metadata"></video>`;
  preview.innerHTML = `${media}<div><strong>${escapeHtml(state.problemMedia.kind === 'photo' ? 'Фотография' : 'Видео')}</strong><small>${escapeHtml(state.problemMedia.filename)} · ${problemMediaSize(state.problemMedia.blob.size)}</small></div><button id="clearProblemMedia" type="button" aria-label="Удалить вложение">×</button>`;
  preview.classList.remove('hidden');
}

function clearProblemMedia() {
  state.problemMedia = null;
  if (state.problemMediaUrl) URL.revokeObjectURL(state.problemMediaUrl);
  state.problemMediaUrl = null;
  $('#problemMediaFile').value = '';
  renderProblemMedia();
}

function setProblemMedia(blob, kind, filename) {
  const maximum = kind === 'video' ? 20 * 1024 * 1024 : 6 * 1024 * 1024;
  if (!blob?.size) {
    toast('Камера вернула пустой файл', true);
    return false;
  }
  if (blob.size > maximum) {
    toast(kind === 'video' ? 'Видео больше 20 МБ' : 'Фото больше 6 МБ', true);
    return false;
  }
  state.problemMedia = { blob, kind, filename };
  renderProblemMedia();
  closeProblemCamera();
  tg?.HapticFeedback?.notificationOccurred('success');
  return true;
}

function stopProblemCameraStream() {
  state.problemCameraStream?.getTracks().forEach((track) => track.stop());
  state.problemCameraStream = null;
  $('#problemCameraView').srcObject = null;
}

function clearProblemRecordingTimers() {
  clearInterval(state.problemRecordingTimer);
  clearTimeout(state.problemRecordingTimeout);
  state.problemRecordingTimer = null;
  state.problemRecordingTimeout = null;
}

function closeProblemCamera(restoreForm = true) {
  clearTimeout(state.problemPressTimer);
  state.problemPointerActive = false;
  if (state.problemRecorder?.state === 'recording') {
    state.problemDiscardRecording = true;
    state.problemRecorder.stop();
  } else {
    stopProblemCameraStream();
  }
  clearProblemRecordingTimers();
  $('#problemCameraStage').hidden = true;
  $('#problemRecordingBadge').hidden = true;
  $('#problemShutter').classList.remove('holding');
  const returnToForm = state.problemCameraReturnToForm;
  state.problemCameraReturnToForm = false;
  if (restoreForm && returnToForm && !$('#createDialog').open) {
    $('#createDialog').showModal();
  }
}

function problemRecorderMimeType() {
  if (!window.MediaRecorder) return '';
  const types = [
    'video/mp4;codecs=h264,aac', 'video/mp4',
    'video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm',
  ];
  return types.find((type) => MediaRecorder.isTypeSupported?.(type)) || '';
}

async function openProblemCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    toast('Встроенная камера недоступна — прикрепите файл', true);
    $('#problemMediaFile').click();
    return;
  }
  $('#openProblemCamera').disabled = true;
  let stream;
  try {
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: true,
      });
    } catch (_microphoneError) {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
    }
    state.problemCameraStream = stream;
    const video = $('#problemCameraView');
    video.srcObject = stream;
    await video.play();
    state.problemCameraReturnToForm = $('#createDialog').open;
    if (state.problemCameraReturnToForm) $('#createDialog').close();
    $('#problemCameraStage').hidden = false;
  } catch (error) {
    stopProblemCameraStream();
    toast(error.message || 'Камера недоступна — прикрепите файл', true);
  } finally {
    $('#openProblemCamera').disabled = false;
  }
}

function captureProblemPhoto() {
  const video = $('#problemCameraView');
  if (!state.problemCameraStream || !video.videoWidth || !video.videoHeight) {
    toast('Камера ещё готовит изображение', true);
    return;
  }
  const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  canvas.toBlob((blob) => {
    if (!blob) toast('Не удалось сохранить фотографию', true);
    else setProblemMedia(blob, 'photo', 'problem-camera.jpg');
  }, 'image/jpeg', 0.84);
}

function updateProblemRecordingTime() {
  const elapsed = Math.min(
    PROBLEM_MAX_VIDEO_MS,
    Date.now() - state.problemRecordingStartedAt,
  );
  $('#problemRecordingTime').textContent = `00:${String(Math.floor(elapsed / 1000)).padStart(2, '0')}`;
}

function stopProblemRecording() {
  clearProblemRecordingTimers();
  $('#problemRecordingBadge').hidden = true;
  $('#problemShutter').classList.remove('holding');
  $('#problemCaptureHint').textContent = 'Нажмите или удерживайте';
  if (state.problemRecorder?.state === 'recording') state.problemRecorder.stop();
}

function startProblemRecording() {
  if (!state.problemPointerActive || !state.problemCameraStream || !window.MediaRecorder) {
    if (!window.MediaRecorder) toast('Видео на этом устройстве недоступно', true);
    return;
  }
  const mimeType = problemRecorderMimeType();
  try {
    state.problemDiscardRecording = false;
    state.problemRecorderChunks = [];
    state.problemRecorder = new MediaRecorder(
      state.problemCameraStream,
      mimeType ? { mimeType, videoBitsPerSecond: 2500000 } : undefined,
    );
  } catch (_error) {
    toast('Видео не запустилось — можно сделать фото', true);
    return;
  }
  state.problemRecorder.ondataavailable = (event) => {
    if (event.data?.size) state.problemRecorderChunks.push(event.data);
  };
  state.problemRecorder.onstop = () => {
    if (state.problemDiscardRecording) {
      stopProblemCameraStream();
      return;
    }
    const type = state.problemRecorder.mimeType || mimeType || 'video/webm';
    const blob = new Blob(state.problemRecorderChunks, { type });
    setProblemMedia(blob, 'video', `problem-camera.${type.includes('mp4') ? 'mp4' : 'webm'}`);
  };
  state.problemRecordingStartedAt = Date.now();
  state.problemRecorder.start(250);
  $('#problemRecordingBadge').hidden = false;
  $('#problemShutter').classList.add('holding');
  $('#problemCaptureHint').textContent = 'Отпустите, чтобы закончить';
  updateProblemRecordingTime();
  state.problemRecordingTimer = setInterval(updateProblemRecordingTime, 200);
  state.problemRecordingTimeout = setTimeout(stopProblemRecording, PROBLEM_MAX_VIDEO_MS);
  tg?.HapticFeedback?.impactOccurred('medium');
}

function renderFilters() {
  const clubOptions = state.meta.clubs.map((club) => `<option value="${escapeHtml(club)}">${escapeHtml(club)}</option>`).join('');
  const typeOptions = state.meta.types.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join('');
  $('#clubFilter').insertAdjacentHTML('beforeend', clubOptions);
  $('#typeFilter').insertAdjacentHTML('beforeend', typeOptions);
  $('#createClub').innerHTML = `<option value="">Выберите клуб</option>${clubOptions}`;
  $('#createType').innerHTML = `<option value="">Выберите тип</option>${typeOptions}`;
  $('#catalogClub').innerHTML = clubOptions;
}

function applyUrlFilters() {
  const club = new URLSearchParams(window.location.search).get('club');
  if (club && state.meta.clubs.includes(club)) $('#clubFilter').value = club;
}

function selectedRepairLocations() {
  return [...document.querySelectorAll('#repairLocations input:checked')].map((input) => ({
    id: Number(input.value), name: input.dataset.name,
  }));
}
function updateRepairTitle() {
  const item = state.repairCatalog?.items.find((entry) => entry.id === Number($('#repairItem').value));
  const detail = item?.details.find((entry) => entry.id === Number($('#repairDetail').value));
  const locations = selectedRepairLocations();
  const title = item && locations.length
    ? `${item.name}${detail ? ` (${detail.name})` : ''} — ${locations.map((entry) => entry.name).join(', ')}`
    : 'Выберите оборудование и место';
  $('#repairTitlePreview').textContent = title;
  $('#createTitle').value = item && locations.length ? title.slice(0, 50) : '';
}
function renderRepairCatalog() {
  const catalog = state.repairCatalog;
  $('#repairItem').innerHTML = `<option value="">Выберите оборудование</option>${catalog.items.filter((item) => item.active).map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('')}`;
  $('#repairLocations').innerHTML = catalog.locations.filter((location) => location.active).map((location) => `
    <label class="location-option"><input type="checkbox" value="${location.id}" data-name="${escapeHtml(location.name)}"><span>${escapeHtml(location.name)}</span></label>
  `).join('') || '<div class="empty-card">Для клуба ещё не добавлены места</div>';
  $('#detailItem').innerHTML = catalog.items.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('');
  $('#catalogItems').innerHTML = catalog.items.map((item) => `
    <div class="catalog-group">
      <button class="catalog-entry${item.active ? '' : ' inactive'}" type="button" data-kind="items" data-entry-id="${item.id}" data-active="${item.active ? '0' : '1'}"><span>${escapeHtml(item.name)}</span><b>${item.active ? 'В списке' : 'Скрыто'}</b></button>
      ${item.details.map((detail) => `<button class="catalog-entry catalog-detail${detail.active ? '' : ' inactive'}" type="button" data-kind="details" data-entry-id="${detail.id}" data-active="${detail.active ? '0' : '1'}"><span>${escapeHtml(detail.name)}</span><b>${detail.active ? 'В списке' : 'Скрыто'}</b></button>`).join('')}
    </div>
  `).join('');
  $('#catalogLocations').innerHTML = catalog.locations.map((location) => `
    <button class="catalog-entry${location.active ? '' : ' inactive'}" type="button" data-kind="locations" data-entry-id="${location.id}" data-active="${location.active ? '0' : '1'}"><span>${escapeHtml(location.name)}</span><b>${location.active ? 'В списке' : 'Скрыто'}</b></button>
  `).join('');
  updateRepairDetails();
  updateRepairTitle();
}
async function loadRepairCatalog(club = $('#createClub').value, includeInactive = false) {
  if (!club) return;
  state.repairCatalog = await api(`/api/repairs/catalog?club=${encodeURIComponent(club)}${includeInactive ? '&include_inactive=1' : ''}`);
  renderRepairCatalog();
}
function updateMappingDetails() {
  const item = state.repairCatalog?.items.find((entry) => entry.id === Number($('#mappingItem').value));
  const details = item?.details.filter((detail) => detail.active) || [];
  $('#mappingDetailField').classList.toggle('hidden', !details.length);
  $('#mappingDetail').innerHTML = `<option value="">Без уточнения</option>${details.map((detail) => `<option value="${detail.id}">${escapeHtml(detail.name)}</option>`).join('')}`;
}
function renderMigration(migration) {
  state.migration = migration;
  $('#migrationSummary').textContent = `${migration.summary.mapped} сопоставлено · ${migration.unmapped.length} требуют проверки · ${migration.summary.duplicates} дубль исключён · ${migration.summary.tests} тестовых исключено`;
  $('#migrationList').innerHTML = migration.unmapped.map((task) => `
    <button class="catalog-entry migration-entry" type="button" data-map-task="${task.ID}"><span><b>№${task.ID} · ${escapeHtml(task.club)}</b><br>${escapeHtml(task.title)}</span><strong>Разобрать</strong></button>
  `).join('') || '<div class="empty-card">Все старые ремонты сопоставлены</div>';
}
async function openMapping(taskId) {
  state.mappingTask = state.migration.unmapped.find((task) => task.ID === taskId);
  $('#catalogClub').value = state.mappingTask.club;
  await loadRepairCatalog(state.mappingTask.club, false);
  $('#mappingTitle').textContent = `№${taskId} · ${state.mappingTask.title}`;
  $('#mappingDescription').textContent = state.mappingTask.desc || 'Без описания';
  $('#mappingItem').innerHTML = `<option value="">Выберите оборудование</option>${state.repairCatalog.items.filter((item) => item.active).map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('')}`;
  $('#mappingLocations').innerHTML = state.repairCatalog.locations.filter((location) => location.active).map((location) => `
    <label class="location-option"><input type="checkbox" value="${location.id}"><span>${escapeHtml(location.name)}</span></label>
  `).join('');
  updateMappingDetails();
  $('#mappingDialog').showModal();
}
function updateRepairDetails() {
  const item = state.repairCatalog?.items.find((entry) => entry.id === Number($('#repairItem').value));
  const details = item?.details.filter((detail) => detail.active) || [];
  $('#repairDetailField').classList.toggle('hidden', !details.length);
  $('#repairDetail').innerHTML = `<option value="">Без уточнения</option>${details.map((detail) => `<option value="${detail.id}">${escapeHtml(detail.name)}</option>`).join('')}`;
}
async function updateCreateFields() {
  const repair = $('#createType').value === 'Ремонт';
  const currentClub = $('#createClub').value;
  const availableClubs = repair ? state.meta.repair_clubs : state.meta.clubs;
  $('#createClub').innerHTML = `<option value="">Выберите клуб</option>${availableClubs.map((club) => `<option value="${escapeHtml(club)}">${escapeHtml(club)}</option>`).join('')}`;
  if (availableClubs.includes(currentClub)) $('#createClub').value = currentClub;
  $('#repairFields').classList.toggle('hidden', !repair);
  $('#regularTitleField').classList.toggle('hidden', repair);
  $('#createTitle').required = !repair;
  $('#repairItem').required = repair;
  if (repair && $('#createClub').value) await loadRepairCatalog();
}

function renderList() {
  const club = $('#clubFilter').value;
  const type = $('#typeFilter').value;
  const tasks = state.tasks.filter((task) => (!club || task.club === club) && (!type || task.type === type));
  $('#problemList').innerHTML = tasks.length ? tasks.map((task) => `
    <button class="problem-card" type="button" data-id="${task.id}">
      <div class="problem-card-badges"><span class="type-badge${task.type === 'Ремонт' ? ' repair' : ''}">${escapeHtml(task.type)}</span>${task.has_photo ? '<span class="media-badge photo">● Фото</span>' : ''}${task.has_video ? '<span class="media-badge video">● Видео</span>' : ''}</div>
      <h3>${escapeHtml(task.title)}</h3>
      <p>${escapeHtml(task.club)} · ${dateLabel(task.date)}</p>
    </button>
  `).join('') : '<div class="empty-card">В этом разделе задач нет</div>';
}

function localMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
}

const russianMonths = [
  'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь',
];

function analyticsPeriodLabel() {
  if (state.analyticsMode === 'month') {
    return $('#analyticsMonth').selectedOptions[0]?.textContent || '';
  }
  if (state.analyticsMode === 'year') return $('#analyticsYear').value;
  return 'Всё время';
}

function percentLabel(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function durationLabel(seconds, precision) {
  if (seconds == null) return '—';
  if (precision !== 'exact') return `≈ ${(Number(seconds) / 86400).toFixed(1)} дн.`;
  const totalMinutes = Math.round(Number(seconds) / 60);
  if (totalMinutes < 60) return `${totalMinutes} мин.`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 24) return `${hours} ч${minutes ? ` ${minutes} мин.` : ''}`;
  const days = Math.floor(hours / 24);
  return `${days} дн. ${hours % 24} ч`;
}

function renderAnalyticsBreakdown(selector, rows) {
  const container = $(selector);
  container.innerHTML = rows.length ? rows.map((item) => `
    <article class="analytics-breakdown-row">
      <div class="analytics-breakdown-head"><strong>${escapeHtml(item.label)}</strong><b>${item.count}</b></div>
      <div class="analytics-breakdown-track"><i style="width:${Math.max(Number(item.share || 0) * 100, item.count ? 2 : 0)}%"></i></div>
      <small>${percentLabel(item.share)} от всех · ${item.open} открыто · среднее ${durationLabel(item.average_seconds, item.precision)}</small>
    </article>
  `).join('') : '<div class="empty-card">За выбранный период заявок нет</div>';
}

function renderProblemAnalytics(data) {
  state.analytics = data;
  $('#analyticsPeriodLabel').textContent = analyticsPeriodLabel();
  const summary = data.summary;
  $('#analyticsSummary').innerHTML = `
    <article><span>Создано</span><strong>${summary.created}</strong></article>
    <article><span>Выполнено</span><strong>${summary.completed} (${percentLabel(summary.completion_rate)})</strong></article>
    <article><span>Среднее решение</span><strong>${durationLabel(summary.average_seconds, summary.precision)}</strong></article>
    <article><span>Осталось открыто</span><strong>${summary.open}</strong></article>
  `;
  $('#analyticsStatusBar').innerHTML = data.statuses.map((item) => (
    `<i class="status-${item.key}" style="width:${Number(item.share || 0) * 100}%" title="${escapeHtml(item.label)}: ${item.count}"></i>`
  )).join('');
  $('#analyticsStatusLegend').innerHTML = data.statuses.map((item) => `
    <div><i class="status-${item.key}"></i><span>${escapeHtml(item.label)}</span><strong>${item.count}</strong></div>
  `).join('');
  const oldest = $('#oldestProblem');
  oldest.classList.toggle('hidden', !data.oldest_open);
  if (data.oldest_open) {
    oldest.dataset.taskId = data.oldest_open.id;
    oldest.innerHTML = `<span>Самая старая открытая · ${data.oldest_open.age_days} дн.</span><strong>${escapeHtml(data.oldest_open.title)}</strong><small>${escapeHtml(data.oldest_open.club)} · открыть заявку →</small>`;
  }
  const trendCard = $('#analyticsTrendCard');
  trendCard.classList.toggle('hidden', !data.trend.length);
  if (data.trend.length) {
    const maxValue = Math.max(1, ...data.trend.map((item) => item.created));
    const monthLabels = ['Я', 'Ф', 'М', 'А', 'М', 'И', 'И', 'А', 'С', 'О', 'Н', 'Д'];
    $('#analyticsTrend').innerHTML = data.trend.map((item, index) => `
      <div class="trend-month" title="${item.created} создано · ${item.completed} выполнено">
        <div><i style="height:${item.created ? Math.max(item.created / maxValue * 100, 7) : 0}%"></i><b style="height:${item.completed ? Math.max(item.completed / maxValue * 100, 7) : 0}%"></b></div>
        <span>${monthLabels[index]}</span>
      </div>
    `).join('');
  }
  renderAnalyticsBreakdown('#analyticsTypes', data.types);
  renderAnalyticsBreakdown('#analyticsClubs', data.clubs);
}

async function loadProblemAnalytics() {
  $('#analyticsSummary').innerHTML = '<div class="analytics-loading"></div><div class="analytics-loading"></div>';
  const params = new URLSearchParams({ mode: state.analyticsMode });
  if (state.analyticsMode === 'month') params.set('month', $('#analyticsMonth').value);
  if (state.analyticsMode === 'year') params.set('year', $('#analyticsYear').value);
  try {
    renderProblemAnalytics(await api(`/api/problems/analytics?${params}`));
  } catch (error) {
    $('#analyticsSummary').innerHTML = `<div class="empty-card analytics-error">${escapeHtml(error.message)}</div>`;
  }
}

async function setBoardView(view) {
  state.boardView = view;
  $('#tasksView').classList.toggle('hidden', view !== 'tasks');
  $('#analyticsView').classList.toggle('hidden', view !== 'analytics');
  document.querySelectorAll('#boardViewTabs button').forEach(
    (button) => button.classList.toggle('active', button.dataset.boardView === view),
  );
  if (view === 'analytics') await loadProblemAnalytics();
}

async function loadTasks() {
  $('#problemList').innerHTML = '<div class="empty-card">Загрузка…</div>';
  try {
    const payload = await api(`/api/problems?status=${state.status}`);
    state.tasks = payload.tasks;
    $('#workCount').textContent = payload.counts.work;
    $('#reviewCount').textContent = payload.counts.review;
    renderList();
  } catch (error) {
    $('#problemList').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`;
  }
}

async function openTask(taskId) {
  try {
    const task = await api(`/api/problems/${taskId}`);
    state.selected = task;
    $('#detailMeta').textContent = `${task.type} · ${task.club} · ${dateLabel(task.date)}`;
    $('#detailTitle').textContent = task.title;
    const actions = task.status === 'На проверке'
      ? `<button class="action-button confirm" data-action="confirm">Подтвердить решение</button><button class="action-button return" data-action="return">Вернуть в работу</button>`
      : (task.status === 'В работе' && state.meta.can_process
        ? '<button class="action-button" data-action="solution">Написать решение</button>' : '');
    const repair = task.repair;
    const repairIdentity = repair ? `
      <div class="repair-identity"><span>Оборудование</span><strong>${escapeHtml(repair.item.name)}${repair.detail ? ` · ${escapeHtml(repair.detail.name)}` : ''}</strong><small>${repair.locations.map((location) => escapeHtml(location.name)).join(' · ')}</small></div>
    ` : '';
    const activityLabels = {
      created: ['Создано', '👤'],
      solution: ['Ответ отправлен', '🧑‍🔧'],
      returned: ['Возвращено в работу', '↩️'],
      confirmed: ['Выполнено', '✅'],
    };
    const taskActivity = task.activity?.length
      ? task.activity
      : (task.type === 'Ремонт' ? [{ event_type: 'created', event_at: task.date, actor: null }] : []);
    const activity = taskActivity.length ? `
      <section class="task-activity">
        <div class="history-head"><span>История заявки</span><b>${taskActivity.length}</b></div>
        ${taskActivity.map((event) => {
          const [label, icon] = activityLabels[event.event_type] || [event.event_type, '•'];
          const actor = event.actor
            ? `${event.actor.name || event.actor.login}${event.actor.login && event.actor.login !== event.actor.name ? ` (${event.actor.login})` : ''}`
            : (task.type === 'Ремонт' && event.event_type === 'created' ? 'Автор неизвестен' : '');
          return `<div class="task-activity-row">
            <i>${icon}</i><div><strong>${escapeHtml(label)}</strong>${actor ? `<span>${escapeHtml(actor)}</span>` : ''}</div><time>${dateTimeLabel(event.event_at)}</time>
          </div>`;
        }).join('')}
      </section>` : '';
    const history = repair ? `
      <section class="repair-history"><div class="history-head"><span>История оборудования</span><b>${repair.history.length}</b></div>
        ${repair.history.map((entry) => `<button type="button" class="history-entry" data-history-task="${entry.task_id}">
          <div><strong>Заявка №${entry.task_id}</strong><span>${dateLabel(entry.date)} · ${escapeHtml(entry.status)}</span></div>
          <p>${escapeHtml(entry.title)}</p>
          ${entry.events.length ? `<small>${entry.events.map((event) => `${dateTimeLabel(event.event_at)} · ${escapeHtml({ created: 'создана', solution: 'решение', returned: 'возвращена', confirmed: 'закрыта' }[event.event_type] || event.event_type)}`).join('<br>')}</small>` : ''}
        </button>`).join('')}
      </section>` : '';
    $('#detailContent').innerHTML = `
      ${repairIdentity}
      <div class="detail-description"><span>Описание</span><p>${escapeHtml(task.description)}</p></div>
      ${task.has_photo ? '<div id="detailPhoto" class="empty-card">Загрузка фото…</div>' : ''}
      ${task.has_video ? '<div id="detailVideo" class="empty-card">Загрузка видео…</div>' : ''}
      ${activity}
      <div class="detail-feedback"><span>История решения</span><p>${escapeHtml(task.feedback || 'Ожидает решения…')}</p></div>
      ${history}
      ${actions ? `<div class="detail-actions">${actions}</div>` : ''}
    `;
    $('#detailDialog').showModal();
    if (task.has_photo) loadProblemPhoto(task.id);
    if (task.has_video) loadProblemVideo(task.id);
  } catch (error) { toast(error.message, true); }
}

async function loadProblemPhoto(taskId) {
  try {
    const response = await fetch(`/api/problems/${taskId}/photo`, {
      headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
    });
    if (!response.ok) throw new Error('Не удалось загрузить фото');
    const url = URL.createObjectURL(await response.blob());
    const container = $('#detailPhoto');
    if (container) container.outerHTML = `<img class="detail-photo" src="${url}" alt="Фото проблемы">`;
  } catch (error) {
    const container = $('#detailPhoto');
    if (container) container.textContent = error.message;
  }
}

async function loadProblemVideo(taskId) {
  try {
    const response = await fetch(`/api/problems/${taskId}/video`, {
      headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
    });
    if (!response.ok) throw new Error('Не удалось загрузить видео');
    const url = URL.createObjectURL(await response.blob());
    const container = $('#detailVideo');
    if (container) container.outerHTML = `<video class="detail-video" src="${url}" controls playsinline preload="metadata"></video>`;
  } catch (error) {
    const container = $('#detailVideo');
    if (container) container.textContent = error.message;
  }
}

$('#problemTabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-status]');
  if (!button) return;
  state.status = button.dataset.status;
  document.querySelectorAll('#problemTabs button').forEach((item) => item.classList.toggle('active', item === button));
  loadTasks();
});
$('#boardViewTabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-board-view]');
  if (button) setBoardView(button.dataset.boardView);
});
$('#analyticsPeriodTabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-period]');
  if (!button) return;
  state.analyticsMode = button.dataset.period;
  document.querySelectorAll('#analyticsPeriodTabs button').forEach(
    (item) => item.classList.toggle('active', item === button),
  );
  $('#analyticsMonth').classList.toggle('hidden', state.analyticsMode !== 'month');
  $('#analyticsYear').classList.toggle('hidden', state.analyticsMode !== 'year');
  loadProblemAnalytics();
});
$('#analyticsMonth').addEventListener('change', loadProblemAnalytics);
$('#analyticsYear').addEventListener('change', loadProblemAnalytics);
$('#oldestProblem').addEventListener('click', () => {
  const taskId = Number($('#oldestProblem').dataset.taskId);
  if (taskId) openTask(taskId);
});
$('#clubFilter').addEventListener('change', renderList);
$('#typeFilter').addEventListener('change', renderList);
$('#problemList').addEventListener('click', (event) => {
  const card = event.target.closest('[data-id]');
  if (card) openTask(Number(card.dataset.id));
});
$('#newProblem').addEventListener('click', () => $('#createDialog').showModal());
$('#attachProblemFile').addEventListener('click', () => {
  $('#problemMediaFile').value = '';
  $('#problemMediaFile').click();
});
$('#problemMediaFile').addEventListener('change', (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const photoTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
  const videoTypes = new Set(['video/mp4', 'video/quicktime', 'video/webm']);
  if (photoTypes.has(file.type)) setProblemMedia(file, 'photo', file.name || 'problem-photo.jpg');
  else if (videoTypes.has(file.type)) setProblemMedia(file, 'video', file.name || 'problem-video.mp4');
  else toast('Выберите JPEG, PNG, WebP, MP4, MOV или WebM', true);
});
$('#problemMediaPreview').addEventListener('click', (event) => {
  if (event.target.closest('#clearProblemMedia')) clearProblemMedia();
});
$('#openProblemCamera').addEventListener('click', openProblemCamera);
$('#closeProblemCamera').addEventListener('click', closeProblemCamera);
$('#problemShutter').addEventListener('pointerdown', (event) => {
  event.preventDefault();
  state.problemPointerActive = true;
  state.problemHoldTriggered = false;
  $('#problemShutter').setPointerCapture?.(event.pointerId);
  state.problemPressTimer = setTimeout(() => {
    state.problemHoldTriggered = true;
    startProblemRecording();
  }, PROBLEM_HOLD_DELAY_MS);
});
const finishProblemCapture = (event) => {
  event.preventDefault();
  clearTimeout(state.problemPressTimer);
  if (!state.problemPointerActive) return;
  state.problemPointerActive = false;
  if (state.problemHoldTriggered) stopProblemRecording();
  else captureProblemPhoto();
};
$('#problemShutter').addEventListener('pointerup', finishProblemCapture);
$('#problemShutter').addEventListener('pointercancel', (event) => {
  event.preventDefault();
  clearTimeout(state.problemPressTimer);
  state.problemPointerActive = false;
  if (state.problemHoldTriggered) stopProblemRecording();
});
$('#problemShutter').addEventListener('contextmenu', (event) => event.preventDefault());
$('#createType').addEventListener('change', () => updateCreateFields().catch((error) => toast(error.message, true)));
$('#createClub').addEventListener('change', () => updateCreateFields().catch((error) => toast(error.message, true)));
$('#repairItem').addEventListener('change', () => { updateRepairDetails(); updateRepairTitle(); });
$('#repairDetail').addEventListener('change', updateRepairTitle);
$('#repairLocations').addEventListener('change', updateRepairTitle);
document.addEventListener('click', (event) => {
  if (event.target.closest('.close-dialog')) event.target.closest('dialog')?.close();
});
$('#createForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter || $('#createForm button[type="submit"]');
  button.disabled = true;
  try {
    const data = new FormData(event.target);
    if (state.problemMedia?.kind === 'photo') {
      data.set('photo', state.problemMedia.blob, state.problemMedia.filename);
    } else if (state.problemMedia?.kind === 'video') {
      data.set('video', state.problemMedia.blob, state.problemMedia.filename);
    }
    if (data.get('type') === 'Ремонт') {
      data.set('repair_location_ids', JSON.stringify(selectedRepairLocations().map((entry) => entry.id)));
    }
    await api('/api/problems', { method: 'POST', body: data });
    event.target.reset();
    clearProblemMedia();
    await updateCreateFields();
    $('#createDialog').close();
    state.status = 'work';
    document.querySelectorAll('#problemTabs button').forEach((item) => item.classList.toggle('active', item.dataset.status === 'work'));
    if (state.boardView === 'analytics') await loadProblemAnalytics();
    else await loadTasks();
    toast('Проблема добавлена анонимно');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
window.addEventListener('pagehide', () => closeProblemCamera(false));
document.addEventListener('visibilitychange', () => {
  if (document.hidden && state.problemCameraStream && state.problemRecorder?.state !== 'recording') {
    closeProblemCamera();
  }
});
$('#detailDialog').addEventListener('click', async (event) => {
  const historyTask = event.target.closest('[data-history-task]');
  if (historyTask) {
    $('#detailDialog').close();
    await openTask(Number(historyTask.dataset.historyTask));
    return;
  }
  const action = event.target.closest('[data-action]')?.dataset.action;
  if (!action || !state.selected) return;
  if (action === 'confirm') {
    try {
      await api(`/api/problems/${state.selected.id}/confirm`, { method: 'POST' });
      $('#detailDialog').close();
      if (state.boardView === 'analytics') await loadProblemAnalytics();
      else await loadTasks();
      toast('Решение подтверждено');
    } catch (error) { toast(error.message, true); }
    return;
  }
  state.action = action;
  $('#messageTitle').textContent = action === 'solution' ? 'Решение проблемы' : 'Причина возврата';
  $('#actionMessage').placeholder = action === 'solution' ? 'Опишите выполненное решение' : 'Что осталось неисправным?';
  $('#actionMessage').value = '';
  $('#messageSubmit').textContent = action === 'solution' ? 'Отправить на проверку' : 'Вернуть в работу';
  $('#messageDialog').showModal();
});

$('#repairCatalog').addEventListener('click', async () => {
  $('#catalogDialog').showModal();
  try {
    const club = $('#catalogClub').value || state.meta.clubs[0];
    await loadRepairCatalog(club, true);
    renderMigration(await api('/api/repairs/migration-review'));
  } catch (error) { toast(error.message, true); }
});
$('#catalogClub').addEventListener('change', () => loadRepairCatalog($('#catalogClub').value, true).catch((error) => toast(error.message, true)));
$('#catalogDialog').addEventListener('click', async (event) => {
  const mapping = event.target.closest('[data-map-task]');
  if (mapping) {
    try { await openMapping(Number(mapping.dataset.mapTask)); }
    catch (error) { toast(error.message, true); }
    return;
  }
  const entry = event.target.closest('[data-entry-id]');
  if (!entry) return;
  try {
    await api(`/api/repairs/catalog/${entry.dataset.kind}/${entry.dataset.entryId}`, {
      method: 'PATCH', body: JSON.stringify({ active: entry.dataset.active === '1' }),
    });
    await loadRepairCatalog($('#catalogClub').value, true);
  } catch (error) { toast(error.message, true); }
});
$('#mappingItem').addEventListener('change', updateMappingDetails);
$('#mappingForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const locations = [...document.querySelectorAll('#mappingLocations input:checked')].map((input) => Number(input.value));
  try {
    await api(`/api/repairs/${state.mappingTask.ID}/mapping`, {
      method: 'POST', body: JSON.stringify({
        item_id: Number($('#mappingItem').value),
        detail_id: Number($('#mappingDetail').value) || null,
        location_ids: locations,
      }),
    });
    $('#mappingDialog').close();
    renderMigration(await api('/api/repairs/migration-review'));
    toast('Старая заявка сопоставлена');
  } catch (error) { toast(error.message, true); }
});
async function submitCatalogForm(event, path, extra = {}) {
  event.preventDefault();
  const data = new FormData(event.target);
  try {
    await api(path, { method: 'POST', body: JSON.stringify({ ...extra, name: data.get('name') }) });
    event.target.reset();
    await loadRepairCatalog($('#catalogClub').value, true);
    toast('Справочник обновлён');
  } catch (error) { toast(error.message, true); }
}
$('#addItemForm').addEventListener('submit', (event) => submitCatalogForm(event, '/api/repairs/catalog/items'));
$('#addDetailForm').addEventListener('submit', (event) => submitCatalogForm(event, '/api/repairs/catalog/details', { item_id: Number($('#detailItem').value) }));
$('#addLocationForm').addEventListener('submit', (event) => submitCatalogForm(event, '/api/repairs/catalog/locations', { club: $('#catalogClub').value }));
$('#messageForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const action = state.action;
  const taskId = state.selected.id;
  try {
    await api(`/api/problems/${taskId}/${action}`, {
      method: 'POST', body: JSON.stringify({ message: $('#actionMessage').value }),
    });
    $('#messageDialog').close();
    $('#detailDialog').close();
    if (state.boardView === 'analytics') await loadProblemAnalytics();
    else await loadTasks();
    toast(action === 'solution' ? 'Решение отправлено на проверку' : 'Проблема возвращена в работу');
  } catch (error) { toast(error.message, true); }
});

async function init() {
  try {
    [state.me, state.meta] = await Promise.all([api('/api/me'), api('/api/problems-meta')]);
    $('#problemUserName').textContent = `Команда OMG VR · ${state.me.name}`;
    $('#problemUserBadge').textContent = state.me.role_name;
    renderFilters();
    applyUrlFilters();
    $('#repairCatalog').classList.toggle('hidden', !state.meta.can_edit_repair_catalog);
    $('#boardViewTabs').classList.toggle('hidden', !state.meta.can_view_analytics);
    const currentYear = new Date().getFullYear();
    const monthOptions = [];
    for (let year = currentYear; year >= 2024; year -= 1) {
      const lastMonth = year === currentYear ? new Date().getMonth() : 11;
      for (let month = lastMonth; month >= 0; month -= 1) {
        const value = `${year}-${String(month + 1).padStart(2, '0')}`;
        monthOptions.push(`<option value="${value}">${russianMonths[month]} ${year}</option>`);
      }
    }
    $('#analyticsMonth').innerHTML = monthOptions.join('');
    $('#analyticsMonth').value = localMonth();
    $('#analyticsYear').innerHTML = Array.from(
      { length: currentYear - 2023 }, (_, index) => currentYear - index,
    ).map((year) => `<option value="${year}">${year}</option>`).join('');
    await loadTasks();
    if (new URLSearchParams(window.location.search).get('new') === '1') {
      $('#createDialog').showModal();
    }
  } catch (error) {
    $('#problemList').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`;
  }
}
init();
