const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const state = { me: null, meta: null, status: 'work', tasks: [], selected: null, action: null, repairCatalog: null, migration: null, mappingTask: null };
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

function renderFilters() {
  const clubOptions = state.meta.clubs.map((club) => `<option value="${escapeHtml(club)}">${escapeHtml(club)}</option>`).join('');
  const typeOptions = state.meta.types.map((type) => `<option value="${escapeHtml(type)}">${escapeHtml(type)}</option>`).join('');
  $('#clubFilter').insertAdjacentHTML('beforeend', clubOptions);
  $('#typeFilter').insertAdjacentHTML('beforeend', typeOptions);
  $('#createClub').innerHTML = `<option value="">Выберите клуб</option>${clubOptions}`;
  $('#createType').innerHTML = `<option value="">Выберите тип</option>${typeOptions}`;
  $('#catalogClub').innerHTML = clubOptions;
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
      <div class="problem-card-head"><h3>${escapeHtml(task.title)}${task.has_photo ? '<span class="photo-mark">▧</span>' : ''}${task.has_video ? '<span class="photo-mark">▶</span>' : ''}</h3><span class="type-badge">${escapeHtml(task.type)}</span></div>
      <p>${escapeHtml(task.club)} · ${dateLabel(task.date)}</p>
    </button>
  `).join('') : '<div class="empty-card">В этом разделе задач нет</div>';
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
$('#clubFilter').addEventListener('change', renderList);
$('#typeFilter').addEventListener('change', renderList);
$('#problemList').addEventListener('click', (event) => {
  const card = event.target.closest('[data-id]');
  if (card) openTask(Number(card.dataset.id));
});
$('#newProblem').addEventListener('click', () => $('#createDialog').showModal());
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
    if (data.get('type') === 'Ремонт') {
      data.set('repair_location_ids', JSON.stringify(selectedRepairLocations().map((entry) => entry.id)));
    }
    await api('/api/problems', { method: 'POST', body: data });
    event.target.reset();
    await updateCreateFields();
    $('#createDialog').close();
    state.status = 'work';
    document.querySelectorAll('#problemTabs button').forEach((item) => item.classList.toggle('active', item.dataset.status === 'work'));
    await loadTasks();
    toast('Проблема добавлена анонимно');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
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
      await loadTasks();
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
    await loadTasks();
    toast(action === 'solution' ? 'Решение отправлено на проверку' : 'Проблема возвращена в работу');
  } catch (error) { toast(error.message, true); }
});

async function init() {
  try {
    [state.me, state.meta] = await Promise.all([api('/api/me'), api('/api/problems-meta')]);
    renderFilters();
    $('#repairCatalog').classList.toggle('hidden', !state.meta.can_edit_repair_catalog);
    await loadTasks();
    if (new URLSearchParams(window.location.search).get('new') === '1') {
      $('#createDialog').showModal();
    }
  } catch (error) {
    $('#problemList').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`;
  }
}
init();
