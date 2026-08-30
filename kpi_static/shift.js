const tg = window.Telegram?.WebApp;
const shiftActions = document.querySelector('#shiftActions');
const externalLink = document.querySelector('#openExternalShift');
const configLink = document.querySelector('#openShiftConfig');
const reportProblemLink = document.querySelector('#reportProblem');
const errorCard = document.querySelector('#shiftError');
const shiftReportTest = document.querySelector('#shiftReportTest');
const scheduleState = { date: null, view: 'mine', data: null };
const consumablesState = {
  data: null, category: 'all', search: '', loaded: false, photoUrls: new Map(),
};
let employeeDashboardData = null;
let activeTodayDate = null;
let shiftReportAvailable = false;
let canSelectReportClub = false;

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#e9e3f3');
tg?.setBackgroundColor('#e9e3f3');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

function number(value) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
    .format(Number(value || 0));
}

function dateLabel(value) {
  const [year, month, day] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric', month: 'long', weekday: 'short',
  }).format(new Date(year, month - 1, day));
}

function relativeDateLabel(value, today) {
  const selected = new Date(`${value}T00:00:00`);
  const current = new Date(`${today}T00:00:00`);
  const days = Math.round((selected - current) / 86400000);
  if (days === 0) return 'Сегодня';
  if (days === 1) return 'Завтра';
  return `Через ${days} дн.`;
}

function shortDate(value) {
  if (!value) return '—';
  const [year, month, day] = String(value).slice(0, 10).split('-');
  return `${day}.${month}.${year}`;
}

function clock(value) {
  const match = String(value || '').match(/(?:T|\s)(\d{2}:\d{2})|^(\d{2}:\d{2})/);
  return match ? (match[1] || match[2]) : '—';
}

function shiftTime(shift) {
  if (shift.start && shift.end) return `${shift.start}–${shift.end}`;
  return `${number(shift.duration)} ч`;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), 'X-Telegram-Init-Data': tg?.initData || '' };
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || 'Не удалось загрузить данные');
    error.payload = payload;
    throw error;
  }
  return payload;
}

function reportState(report) {
  if (report.state === 'completed') return { label: 'Готово', icon: '✓', className: 'done' };
  if (report.state === 'sending') return { label: 'Повторить отправку', icon: '→', className: 'progress' };
  if (report.state === 'in_progress') return { label: 'Продолжить', icon: '→', className: 'progress' };
  return { label: 'Начать', icon: '→', className: 'idle' };
}

function reportAction(report, club) {
  const state = reportState(report);
  const body = `<span><small>${escapeHtml(report.action_label)}</small><strong>${state.icon} ${state.label}</strong></span>`;
  if (report.state === 'completed') {
    return `<div class="today-report ${state.className}">${body}</div>`;
  }
  const href = `/shift-report?action=${encodeURIComponent(report.action)}&club=${encodeURIComponent(club)}`;
  return `<a class="today-report ${state.className}" href="${href}">${body}</a>`;
}

function bookingRows(group) {
  const bookings = group?.bookings || [];
  if (!bookings.length) return '<div class="today-booking-empty">Броней сегодня нет</div>';
  return bookings.slice(0, 4).map((booking) => `
    <div class="today-booking">
      <strong>${clock(booking.start)}–${clock(booking.end)}</strong>
      <span>${escapeHtml(booking.format || 'Бронь')}</span>
      <b>${number(booking.participants)} гост.</b>
    </div>
  `).join('');
}

function renderToday(contexts, today) {
  const section = document.querySelector('#shiftToday');
  document.querySelector('#shiftTodayDate').textContent = shortDate(today);
  if (!contexts.length) {
    activeTodayDate = null;
    section.hidden = true;
    shiftReportTest.hidden = !(shiftReportAvailable && canSelectReportClub);
    return;
  }
  activeTodayDate = today;
  document.querySelector('#shiftTodayList').innerHTML = contexts.map((context) => {
    const open = context.reports.open || { action: 'open', action_label: 'Открытие', state: 'not_started' };
    const close = context.reports.close || { action: 'close', action_label: 'Закрытие', state: 'not_started' };
    const opened = String(context.club_status || '').toLocaleLowerCase('ru') === 'открыт';
    const colleagues = (context.on_shift || []).join(', ') || 'Только вы';
    const clubState = context.club_status
      ? `<span class="club-state ${opened ? 'open' : 'closed'}"><i></i>${opened ? 'Открыт' : 'Закрыт'}</span>`
      : '';
    const reports = context.report_available
      ? `<div class="today-report-grid">${reportAction(open, context.club)}${reportAction(close, context.club)}</div>`
      : '';
    const bookings = context.bookings_available
      ? `<div class="today-bookings-head"><span>Брони</span><b>${number(context.bookings?.count)} · ${number(context.bookings?.participants)} гост.</b></div><div class="today-bookings">${bookingRows(context.bookings)}</div>`
      : '';
    return `
      <article class="today-shift-card">
        <div class="today-shift-head">
          <div><p>${shiftTime(context)}</p><h3>${escapeHtml(context.club)}</h3></div>
          ${clubState}
        </div>
        <div class="today-colleagues"><small>На смене</small><strong>${escapeHtml(colleagues)}</strong></div>
        ${reports}${bookings}
      </article>`;
  }).join('');
  section.hidden = false;
  shiftReportTest.hidden = true;
  if (employeeDashboardData) renderEmployeeDashboard(employeeDashboardData);
}

function historyMeta(item) {
  const parts = [];
  if (item.late_minutes > 5) parts.push(`Опоздание ${item.late_minutes} мин`);
  if (item.early_close) parts.push('Раннее закрытие');
  if (item.photo_count) parts.push(`Фото ${item.photo_count}`);
  if (item.cleanliness_photo_count) parts.push(`Чистота ${item.cleanliness_photo_count}`);
  return parts.join(' · ');
}

function renderHistory(items) {
  document.querySelector('#shiftHistoryCount').textContent = items.length;
  document.querySelector('#shiftHistoryList').innerHTML = items.length
    ? items.map((item) => {
      const completed = item.state === 'completed';
      const answers = item.answers || [];
      const details = answers.length ? `
        <div class="history-answers">${answers.map((answer) => `
          <div><small>${escapeHtml(answer.question)}</small><strong>${escapeHtml(answer.answer)}</strong></div>
        `).join('')}</div>` : '';
      return `
        <details class="history-item">
          <summary>
            <span class="history-action ${item.action}">${item.action === 'open' ? '↑' : '↓'}</span>
            <span><strong>${escapeHtml(item.club)} · ${escapeHtml(item.action_label)}</strong><small>${shortDate(item.date)} · ${clock(item.started_at)}${item.source === 'bot' ? ' · через бота' : ''}</small></span>
            <b class="history-state ${completed ? 'done' : 'progress'}">${completed ? '✓' : '…'}</b>
          </summary>
          <div class="history-details">
            <p>${completed ? 'Отчёт завершён' : 'Отчёт ещё не завершён'}${item.finished_at ? ` · ${clock(item.finished_at)}` : ''}</p>
            ${historyMeta(item) ? `<p>${escapeHtml(historyMeta(item))}</p>` : ''}
            ${details}
          </div>
        </details>`;
    }).join('')
    : '<div class="shift-empty">История открытий и закрытий пока пуста</div>';
}

async function loadOverview() {
  const payload = await api('/api/shift/overview');
  renderToday(payload.today || [], payload.date);
  renderHistory(payload.history || []);
}

function parseLocalDate(value) {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, month - 1, day);
}

function isoLocalDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function weekLabel(data) {
  return `${shortDate(data.week_start).slice(0, 5)} — ${shortDate(data.week_end)}`;
}

function scheduleShiftRow(shift) {
  const free = shift.is_free;
  return `
    <div class="schedule-shift ${free ? 'free' : ''} ${shift.is_mine ? 'mine' : ''}">
      <span><strong>${escapeHtml(shift.employee)}</strong>${shift.telegram ? `<small>${escapeHtml(shift.telegram)}</small>` : ''}</span>
      <b>${shift.start && shift.end ? `${escapeHtml(shift.start)}–${escapeHtml(shift.end)}` : `${number(shift.duration)} ч`}</b>
    </div>`;
}

function filteredLocations(day, predicate) {
  return (day.locations || []).map((location) => ({
    ...location,
    shifts: (location.shifts || []).filter(predicate),
  })).filter((location) => location.shifts.length);
}

function renderScheduleByDays(days, predicate) {
  const visible = days.map((day) => ({ ...day, locations: filteredLocations(day, predicate) }))
    .filter((day) => day.locations.length);
  if (!visible.length) return '<div class="shift-empty">На этой неделе подходящих смен нет</div>';
  return visible.map((day) => `
    <article class="schedule-day">
      <div class="schedule-day-head"><strong>${dateLabel(day.date)}</strong><span>${shortDate(day.date).slice(0, 5)}</span></div>
      ${day.locations.map((location) => `
        <div class="schedule-location"><h3>${escapeHtml(location.club)}</h3>${location.shifts.map(scheduleShiftRow).join('')}</div>
      `).join('')}
    </article>`).join('');
}

function renderScheduleByClubs(days) {
  const clubs = new Map();
  days.forEach((day) => (day.locations || []).forEach((location) => {
    if (!clubs.has(location.club)) clubs.set(location.club, []);
    clubs.get(location.club).push({ date: day.date, shifts: location.shifts || [] });
  }));
  if (!clubs.size) return '<div class="shift-empty">На этой неделе смен нет</div>';
  return [...clubs.entries()].map(([club, clubDays]) => `
    <article class="schedule-day schedule-club">
      <div class="schedule-day-head"><strong>${escapeHtml(club)}</strong><span>${clubDays.reduce((sum, day) => sum + day.shifts.length, 0)} смен</span></div>
      ${clubDays.map((day) => `<div class="schedule-location"><h3>${dateLabel(day.date)}</h3>${day.shifts.map(scheduleShiftRow).join('')}</div>`).join('')}
    </article>`).join('');
}

function renderSchedule() {
  const data = scheduleState.data;
  if (!data) return;
  document.querySelector('#scheduleWeekLabel').textContent = weekLabel(data);
  document.querySelector('#scheduleSource').textContent = data.source === 'omg_shift' ? 'LIVE' : 'КЭШ';
  const warning = document.querySelector('#scheduleWarning');
  warning.textContent = data.warning || '';
  warning.hidden = !data.warning;
  const days = data.days || [];
  let content;
  if (scheduleState.view === 'clubs') content = renderScheduleByClubs(days);
  else if (scheduleState.view === 'free') content = renderScheduleByDays(days, (shift) => shift.is_free);
  else if (scheduleState.view === 'mine') content = renderScheduleByDays(days, (shift) => shift.is_mine);
  else content = renderScheduleByDays(days, () => true);
  document.querySelector('#scheduleContent').innerHTML = content;
}

async function loadSchedule(date = scheduleState.date) {
  document.querySelector('#scheduleContent').innerHTML = '<div class="shift-empty">Загружаю расписание…</div>';
  const query = date ? `?date=${encodeURIComponent(date)}` : '';
  const payload = await api(`/api/shift/schedule${query}`);
  scheduleState.data = payload;
  scheduleState.date = payload.week_start;
  renderSchedule();
}

function consumableDate(value) {
  if (!value) return '';
  const parsed = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(parsed);
}

function showConsumablesMessage(text, tone = '') {
  const status = document.querySelector('#consumablesStatus');
  status.textContent = text || '';
  status.className = `consumables-status ${tone}`.trim();
}

function consumableCategoryOptions(selected) {
  return (consumablesState.data?.categories || []).map((category) => `
    <option value="${category.id}" ${Number(selected) === Number(category.id) ? 'selected' : ''}>
      ${escapeHtml(category.emoji)} ${escapeHtml(category.name)}
    </option>
  `).join('');
}

async function loadConsumablePhotos() {
  const images = [...document.querySelectorAll('img[data-consumable-photo]')];
  await Promise.all(images.map(async (img) => {
    const productId = img.dataset.consumablePhoto;
    const version = img.dataset.photoVersion || '';
    const key = `${productId}:${version}`;
    try {
      if (!consumablesState.photoUrls.has(key)) {
        const response = await fetch(`/api/shift/consumables/products/${productId}/photo`, {
          headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
        });
        if (!response.ok) return;
        consumablesState.photoUrls.set(key, URL.createObjectURL(await response.blob()));
      }
      img.src = consumablesState.photoUrls.get(key);
      img.closest('.consumable-photo')?.classList.add('loaded');
    } catch (_error) {
      // The product card keeps its neutral placeholder when a photo is unavailable.
    }
  }));
}

function consumableCard(item) {
  const photo = item.has_photo
    ? `<img data-consumable-photo="${item.product_id}" data-photo-version="${escapeHtml(item.photo_updated_at || '')}" alt="${escapeHtml(item.name)}">`
    : '<span>📦</span>';
  const state = item.is_active
    ? `<span class="stock-state ${item.is_low ? 'low' : 'ok'}">${item.is_low ? 'Мало' : 'В норме'}</span>`
    : '<span class="stock-state archived">В архиве</span>';
  const primaryAction = item.is_active
    ? `<button class="stock-update" type="button" data-consumable-quantity="${item.id}">Изменить остаток</button>`
    : (consumablesState.data.can_manage
      ? `<button class="stock-restore" type="button" data-consumable-restore="${item.id}">Вернуть из архива</button>` : '');
  const management = consumablesState.data.can_manage
    ? `<button class="stock-manage" type="button" data-consumable-manage="${item.id}" aria-label="Настроить">•••</button>` : '';
  return `
    <article class="consumable-card ${item.is_low ? 'low' : ''} ${item.is_active ? '' : 'archived'}">
      <div class="consumable-photo">${photo}</div>
      <div class="consumable-info">
        <div class="consumable-name-row"><div><small>${escapeHtml(item.category_emoji)} ${escapeHtml(item.category_name)}</small><h3>${escapeHtml(item.name)}</h3></div>${management}</div>
        <div class="consumable-stock"><strong>${number(item.quantity)} <small>шт.</small></strong><span>мин. ${number(item.min_limit)}</span>${state}</div>
        ${item.archive_reason ? `<p class="archive-reason">${escapeHtml(item.archive_reason)}</p>` : ''}
        <div class="consumable-card-actions">${primaryAction}<button type="button" data-consumable-history="${item.id}">История</button></div>
      </div>
    </article>`;
}

function renderConsumables() {
  const data = consumablesState.data;
  if (!data) return;
  const clubSelect = document.querySelector('#consumablesClub');
  clubSelect.innerHTML = data.clubs.map((club) => `
    <option value="${escapeHtml(club)}" ${club === data.selected_club ? 'selected' : ''}>${escapeHtml(club)}</option>
  `).join('');
  document.querySelector('#addConsumable').hidden = !data.can_manage;
  document.querySelector('#manageConsumableCategories').hidden = !data.can_manage;
  document.querySelector('#consumablesArchiveLabel').hidden = !data.can_manage;
  const low = Number(data.summary?.low || 0);
  document.querySelector('#consumablesLowCount').textContent = low ? `${low} мало` : 'В норме';
  document.querySelector('#consumablesLowCount').classList.toggle('low', low > 0);
  const categoryButtons = [
    { slug: 'all', emoji: '•', name: 'Все' },
    ...(data.categories || []),
  ];
  document.querySelector('#consumablesCategories').innerHTML = categoryButtons.map((category) => `
    <button type="button" data-consumable-category="${category.slug}" class="${consumablesState.category === category.slug ? 'active' : ''}">
      ${escapeHtml(category.emoji)} ${escapeHtml(category.name)}
    </button>
  `).join('');
  const query = consumablesState.search.toLocaleLowerCase('ru');
  const items = (data.items || []).filter((item) => (
    (consumablesState.category === 'all' || item.category_slug === consumablesState.category)
    && (!query || item.name.toLocaleLowerCase('ru').includes(query))
  ));
  document.querySelector('#consumablesList').innerHTML = items.length
    ? items.map(consumableCard).join('')
    : '<div class="shift-empty">Подходящих позиций нет</div>';
  if (document.querySelector('#shiftConsumables').open) loadConsumablePhotos();
}

async function loadConsumables(club = null) {
  const archived = document.querySelector('#consumablesArchived').checked ? '&archived=1' : '';
  const clubQuery = club ? `club=${encodeURIComponent(club)}` : '';
  const payload = await api(`/api/shift/consumables?${clubQuery}${archived}`);
  consumablesState.data = payload;
  consumablesState.loaded = true;
  renderConsumables();
}

function showConsumableModal(title, body) {
  document.querySelector('#consumableModalTitle').textContent = title;
  document.querySelector('#consumableModalBody').innerHTML = body;
  document.querySelector('#consumableModal').hidden = false;
  document.body.classList.add('modal-open');
}

function closeConsumableModal() {
  document.querySelector('#consumableModal').hidden = true;
  document.body.classList.remove('modal-open');
}

function consumableItem(itemId) {
  return consumablesState.data?.items.find((item) => item.id === Number(itemId));
}

function openQuantityModal(item) {
  showConsumableModal('Новый остаток', `
    <form id="consumableQuantityForm" class="consumable-form" data-item-id="${item.id}">
      <div class="modal-product"><span>${escapeHtml(item.category_emoji)}</span><div><small>${escapeHtml(item.club)}</small><strong>${escapeHtml(item.name)}</strong></div></div>
      <label><span>Сколько сейчас</span><input name="quantity" type="number" min="0" step="1" inputmode="numeric" value="${item.quantity}" required autofocus></label>
      <p class="form-hint">Минимальный остаток: ${number(item.min_limit)} шт.</p>
      <button class="modal-primary" type="submit">Сохранить остаток</button>
    </form>`);
}

function openAddConsumableModal() {
  const data = consumablesState.data;
  showConsumableModal('Добавить товар', `
    <form id="consumableAddForm" class="consumable-form">
      <label><span>Клуб</span><select name="club">${data.clubs.map((club) => `<option ${club === data.selected_club ? 'selected' : ''}>${escapeHtml(club)}</option>`).join('')}</select></label>
      <label><span>Название</span><input name="name" maxlength="100" placeholder="Например, Coca-Cola 0,5" required></label>
      <label><span>Категория</span><select name="category_id">${consumableCategoryOptions()}</select></label>
      <div class="consumable-form-grid">
        <label><span>Остаток</span><input name="quantity" type="number" min="0" step="1" value="0" required></label>
        <label><span>Минимум</span><input name="min_limit" type="number" min="0" step="1" value="5" required></label>
      </div>
      <label class="photo-picker"><span>📷 Фото товара <small>· необязательно</small></span><input name="photo" type="file" accept="image/*"><b>Выбрать фото</b></label>
      <p class="form-hint">Если товар уже есть в другом клубе, приложение использует его общую карточку и фото.</p>
      <button class="modal-primary" type="submit">Добавить в клуб</button>
    </form>`);
}

function openConsumableCategoriesModal() {
  const categories = consumablesState.data?.categories || [];
  showConsumableModal('Категории', `
    <div class="category-manager-list">${categories.map((category) => `<div><span>${escapeHtml(category.emoji)}</span><strong>${escapeHtml(category.name)}</strong></div>`).join('')}</div>
    <form id="consumableCategoryForm" class="consumable-form category-manager-form">
      <p class="form-hint">Новая категория сразу появится в списке при добавлении товара.</p>
      <div class="category-manager-fields">
        <label><span>Эмодзи</span><input name="emoji" maxlength="8" value="📦" required></label>
        <label><span>Название</span><input name="name" maxlength="60" placeholder="Например, Снеки" required></label>
      </div>
      <button class="modal-primary" type="submit">+ Добавить категорию</button>
    </form>`);
}

function openManageConsumableModal(item) {
  showConsumableModal('Настройки товара', `
    <form id="consumableManageForm" class="consumable-form" data-item-id="${item.id}">
      <div class="modal-product"><span>${escapeHtml(item.category_emoji)}</span><div><small>${escapeHtml(item.club)}</small><strong>${escapeHtml(item.name)}</strong></div></div>
      <label><span>Минимальный остаток</span><input name="min_limit" type="number" min="0" step="1" value="${item.min_limit}" required></label>
      <label><span>Категория <small>· общая для всех клубов</small></span><select name="category_id">${consumableCategoryOptions(item.category_id)}</select></label>
      <label class="photo-picker"><span>📷 Фото <small>· общее для всех клубов</small></span><input name="photo" type="file" accept="image/*"><b>${item.has_photo ? 'Заменить фото' : 'Добавить фото'}</b></label>
      <button class="modal-primary" type="submit">Сохранить настройки</button>
      ${item.is_active ? `<div class="archive-box"><label><span>Причина архива <small>· необязательно</small></span><input name="archive_reason" maxlength="300" placeholder="Например, временно не закупаем"></label><button type="button" data-archive-from-modal="${item.id}">Убрать в архив</button></div>` : `<button class="modal-restore" type="button" data-consumable-restore="${item.id}">Вернуть из архива</button>`}
    </form>`);
}

async function compressConsumablePhoto(file) {
  if (!file) return null;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
    if (blob) return new File([blob], 'product.jpg', { type: 'image/jpeg' });
  } catch (_error) {
    // Older WebViews can upload the original supported image.
  }
  if (file.size > 3 * 1024 * 1024) throw new Error('Не удалось сжать фото. Выберите файл меньше 3 МБ.');
  return file;
}

async function reloadConsumables(message = '') {
  await loadConsumables(consumablesState.data?.selected_club);
  if (message) showConsumablesMessage(message, 'success');
}

document.querySelector('#shiftConsumables').addEventListener('toggle', () => {
  if (!document.querySelector('#shiftConsumables').open) return;
  if (!consumablesState.loaded) {
    loadConsumables().catch((error) => {
      document.querySelector('#consumablesList').innerHTML = `<div class="error-card">${escapeHtml(error.message)}</div>`;
    });
    return;
  }
  loadConsumablePhotos();
});

document.querySelector('#consumablesClub').addEventListener('change', (event) => {
  showConsumablesMessage('Загружаю…');
  loadConsumables(event.target.value).then(() => showConsumablesMessage('')).catch((error) => showConsumablesMessage(error.message, 'error'));
});

document.querySelector('#consumablesArchived').addEventListener('change', () => {
  loadConsumables(consumablesState.data?.selected_club).catch((error) => showConsumablesMessage(error.message, 'error'));
});

document.querySelector('#consumablesSearch').addEventListener('input', (event) => {
  consumablesState.search = event.target.value.trim();
  renderConsumables();
});

document.querySelector('#consumablesCategories').addEventListener('click', (event) => {
  const button = event.target.closest('[data-consumable-category]');
  if (!button) return;
  consumablesState.category = button.dataset.consumableCategory;
  renderConsumables();
});

document.querySelector('#addConsumable').addEventListener('click', openAddConsumableModal);
document.querySelector('#manageConsumableCategories').addEventListener('click', openConsumableCategoriesModal);

document.querySelector('#consumablesList').addEventListener('click', async (event) => {
  const quantityButton = event.target.closest('[data-consumable-quantity]');
  const manageButton = event.target.closest('[data-consumable-manage]');
  const restoreButton = event.target.closest('[data-consumable-restore]');
  const historyButton = event.target.closest('[data-consumable-history]');
  if (quantityButton) openQuantityModal(consumableItem(quantityButton.dataset.consumableQuantity));
  if (manageButton) openManageConsumableModal(consumableItem(manageButton.dataset.consumableManage));
  if (restoreButton) {
    restoreButton.disabled = true;
    try {
      await api(`/api/shift/consumables/${restoreButton.dataset.consumableRestore}/restore`, { method: 'POST' });
      await reloadConsumables('Позиция вернута из архива');
    } catch (error) { showConsumablesMessage(error.message, 'error'); }
  }
  if (historyButton) {
    try {
      const payload = await api(`/api/shift/consumables/${historyButton.dataset.consumableHistory}/history`);
      const labels = { quantity: 'Остаток', created: 'Добавлено', settings: 'Настройки', archived: 'Архив', restored: 'Восстановлено', photo: 'Фото' };
      showConsumableModal('История', `<div class="consumable-history-head"><strong>${escapeHtml(payload.item.name)}</strong><span>${escapeHtml(payload.item.club)}</span></div><div class="consumable-history-list">${payload.events.length ? payload.events.map((item) => `<div><span><strong>${escapeHtml(labels[item.event_type] || item.event_type)}</strong><small>${escapeHtml(item.actor || '—')}</small></span><span><b>${escapeHtml(item.details || '')}</b><small>${escapeHtml(consumableDate(item.created_at))}</small></span></div>`).join('') : '<p>История пока пуста</p>'}</div>`);
    } catch (error) { showConsumablesMessage(error.message, 'error'); }
  }
});

document.querySelector('#consumableModal').addEventListener('click', async (event) => {
  if (event.target.closest('[data-close-consumable-modal]')) closeConsumableModal();
  const restoreButton = event.target.closest('[data-consumable-restore]');
  if (restoreButton) {
    restoreButton.disabled = true;
    try {
      await api(`/api/shift/consumables/${restoreButton.dataset.consumableRestore}/restore`, { method: 'POST' });
      closeConsumableModal();
      await reloadConsumables('Позиция вернута из архива');
    } catch (error) { restoreButton.disabled = false; alert(error.message); }
  }
  const archiveButton = event.target.closest('[data-archive-from-modal]');
  if (archiveButton) {
    archiveButton.disabled = true;
    const form = archiveButton.closest('form');
    try {
      await api(`/api/shift/consumables/${archiveButton.dataset.archiveFromModal}/archive`, {
        method: 'POST', body: JSON.stringify({ reason: form.elements.archive_reason.value.trim() }),
      });
      closeConsumableModal();
      await reloadConsumables('Позиция убрана в архив');
    } catch (error) { archiveButton.disabled = false; alert(error.message); }
  }
});

document.querySelector('#consumableModal').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    if (form.id === 'consumableQuantityForm') {
      const quantity = Number(form.elements.quantity.value);
      const payload = await api(`/api/shift/consumables/${form.dataset.itemId}/quantity`, {
        method: 'POST', body: JSON.stringify({ quantity }),
      });
      closeConsumableModal();
      await reloadConsumables(payload.warnings?.[0] || 'Остаток обновлён');
    } else if (form.id === 'consumableCategoryForm') {
      await api('/api/shift/consumables/categories', {
        method: 'POST',
        body: JSON.stringify({
          emoji: form.elements.emoji.value.trim(),
          name: form.elements.name.value.trim(),
        }),
      });
      closeConsumableModal();
      await reloadConsumables('Категория добавлена');
    } else if (form.id === 'consumableAddForm') {
      const data = new FormData(form);
      const selectedPhoto = form.elements.photo.files[0];
      if (selectedPhoto) data.set('photo', await compressConsumablePhoto(selectedPhoto));
      try {
        await api('/api/shift/consumables', { method: 'POST', body: data });
      } catch (error) {
        if (error.payload?.conflict === 'archived' && window.confirm(error.message)) {
          await api(`/api/shift/consumables/${error.payload.item_id}/restore`, { method: 'POST' });
        } else throw error;
      }
      const club = form.elements.club.value;
      closeConsumableModal();
      await loadConsumables(club);
      showConsumablesMessage('Позиция добавлена', 'success');
    } else if (form.id === 'consumableManageForm') {
      const itemId = form.dataset.itemId;
      await api(`/api/shift/consumables/${itemId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          min_limit: Number(form.elements.min_limit.value),
          category_id: Number(form.elements.category_id.value),
        }),
      });
      const selectedPhoto = form.elements.photo.files[0];
      if (selectedPhoto) {
        const photoData = new FormData();
        photoData.set('photo', await compressConsumablePhoto(selectedPhoto));
        await api(`/api/shift/consumables/${itemId}/photo`, { method: 'POST', body: photoData });
      }
      closeConsumableModal();
      await reloadConsumables('Настройки сохранены');
    }
    tg?.HapticFeedback?.notificationOccurred('success');
  } catch (error) {
    submit.disabled = false;
    alert(error.message);
  }
});

function renderEmployeeDashboard(dashboard) {
  if (!dashboard) return;
  employeeDashboardData = dashboard;
  const shifts = (dashboard.upcoming_shifts || []).filter(
    (shift) => shift.date !== activeTodayDate,
  );
  const nearest = shifts[0];
  const shiftTime = (shift) => shift.start && shift.end
    ? `${shift.start}–${shift.end} · `
    : '';
  document.querySelector('#nearestShift').innerHTML = nearest ? `
    <p class="eyebrow">${relativeDateLabel(nearest.date, dashboard.today)}</p>
    <h2>${escapeHtml(nearest.club)}</h2>
    <strong>${dateLabel(nearest.date)}</strong>
    <span>${shiftTime(nearest)}${number(nearest.duration)} ч</span>
  ` : `
    <p class="eyebrow">Ближайшая смена</p>
    <h2>Смен пока нет</h2>
    <span>В опубликованном расписании новых смен не найдено</span>
  `;
  document.querySelector('#upcomingShiftList').innerHTML = shifts.length > 1
    ? shifts.slice(1).map((shift) => `
      <article class="upcoming-shift">
        <div><strong>${escapeHtml(shift.club)}</strong><span>${dateLabel(shift.date)}</span></div>
        <small>${shiftTime(shift)}${number(shift.duration)} ч</small>
      </article>
    `).join('')
    : '<div class="shift-empty">Других смен в расписании пока нет</div>';
  const summary = dashboard.month_summary || {};
  document.querySelector('#shiftMonthSummary').innerHTML = `
    <div><span>Смен</span><strong>${number(summary.shifts)}</strong></div>
    <div><span>Часов</span><strong>${number(summary.hours)}</strong></div>
  `;
  document.querySelector('#employeeShiftDashboard').hidden = false;
  reportProblemLink.hidden = false;
}

async function loadShift() {
  const payload = await api('/api/shift');
  document.querySelector('#shiftUserName').textContent = `Команда OMG VR · ${payload.user_name}`;
  document.querySelector('#shiftRole').textContent = payload.role_name;
  shiftReportAvailable = Boolean(payload.shift_report_available);
  canSelectReportClub = Boolean(payload.can_select_report_club);
  shiftReportTest.hidden = true;
  externalLink.hidden = !payload.external_url;
  if (payload.external_url) externalLink.href = payload.external_url;
  configLink.hidden = !payload.can_manage;
  if (!payload.employee_dashboard) {
    scheduleState.view = 'days';
    document.querySelectorAll('#scheduleTabs button').forEach((button) => {
      button.classList.toggle('active', button.dataset.view === 'days');
    });
  }
  renderEmployeeDashboard(payload.employee_dashboard);
  shiftActions.classList.toggle('manager', payload.can_manage);
  shiftActions.hidden = false;
}

document.querySelector('#scheduleTabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-view]');
  if (!button) return;
  scheduleState.view = button.dataset.view;
  document.querySelectorAll('#scheduleTabs button').forEach((item) => {
    item.classList.toggle('active', item === button);
  });
  renderSchedule();
});

document.querySelector('#previousWeek').addEventListener('click', () => {
  const target = parseLocalDate(scheduleState.data?.week_start || isoLocalDate(new Date()));
  target.setDate(target.getDate() - 7);
  loadSchedule(isoLocalDate(target)).catch(showSectionError);
});

document.querySelector('#nextWeek').addEventListener('click', () => {
  const target = parseLocalDate(scheduleState.data?.week_start || isoLocalDate(new Date()));
  target.setDate(target.getDate() + 7);
  loadSchedule(isoLocalDate(target)).catch(showSectionError);
});

document.querySelector('#currentWeek').addEventListener('click', () => {
  loadSchedule(isoLocalDate(new Date())).catch(showSectionError);
});

function showSectionError(error) {
  document.querySelector('#scheduleContent').innerHTML = `<div class="error-card">${escapeHtml(error.message)}</div>`;
}

externalLink.addEventListener('click', (event) => {
  const url = externalLink.href;
  if (!url || url.endsWith('#')) return;
  if (tg?.openLink) {
    event.preventDefault();
    try {
      tg.openLink(url);
    } catch (_error) {
      window.open(url, '_blank', 'noopener');
    }
  }
});

async function initializeShift() {
  try {
    await loadShift();
  } catch (error) {
    document.querySelector('#shiftRole').textContent = 'OMG VR';
    errorCard.textContent = error.message;
    errorCard.hidden = false;
  }
  loadOverview().catch((error) => {
    document.querySelector('#shiftHistoryList').innerHTML = `<div class="error-card">${escapeHtml(error.message)}</div>`;
  });
  loadSchedule().catch(showSectionError);
}

initializeShift();
