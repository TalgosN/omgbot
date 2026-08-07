const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const state = { me: null, meta: null, status: 'work', tasks: [], selected: null, action: null };
tg?.ready();
tg?.expand();
if (tg) {
  tg.setHeaderColor('#f4f0ff');
  tg.setBackgroundColor('#f7f4ff');
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
}

function renderList() {
  const club = $('#clubFilter').value;
  const type = $('#typeFilter').value;
  const tasks = state.tasks.filter((task) => (!club || task.club === club) && (!type || task.type === type));
  $('#problemList').innerHTML = tasks.length ? tasks.map((task) => `
    <button class="problem-card" type="button" data-id="${task.id}">
      <div class="problem-card-head"><h3>${escapeHtml(task.title)}${task.has_photo ? '<span class="photo-mark">▧</span>' : ''}</h3><span class="type-badge">${escapeHtml(task.type)}</span></div>
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
    $('#detailContent').innerHTML = `
      <div class="detail-description"><span>Описание</span><p>${escapeHtml(task.description)}</p></div>
      ${task.has_photo ? '<div id="detailPhoto" class="empty-card">Загрузка фото…</div>' : ''}
      <div class="detail-feedback"><span>История решения</span><p>${escapeHtml(task.feedback || 'Ожидает решения…')}</p></div>
      ${actions ? `<div class="detail-actions">${actions}</div>` : ''}
    `;
    $('#detailDialog').showModal();
    if (task.has_photo) loadProblemPhoto(task.id);
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
document.addEventListener('click', (event) => {
  if (event.target.closest('.close-dialog')) event.target.closest('dialog')?.close();
});
$('#createForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter || $('#createForm button[type="submit"]');
  button.disabled = true;
  try {
    await api('/api/problems', { method: 'POST', body: new FormData(event.target) });
    event.target.reset();
    $('#createDialog').close();
    state.status = 'work';
    document.querySelectorAll('#problemTabs button').forEach((item) => item.classList.toggle('active', item.dataset.status === 'work'));
    await loadTasks();
    toast('Проблема добавлена анонимно');
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});
$('#detailDialog').addEventListener('click', async (event) => {
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
    await loadTasks();
  } catch (error) {
    $('#problemList').innerHTML = `<div class="empty-card">${escapeHtml(error.message)}</div>`;
  }
}
init();
