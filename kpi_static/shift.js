const tg = window.Telegram?.WebApp;
const shiftActions = document.querySelector('#shiftActions');
const externalLink = document.querySelector('#openExternalShift');
const configLink = document.querySelector('#openShiftConfig');
const reportProblemLink = document.querySelector('#reportProblem');
const errorCard = document.querySelector('#shiftError');
const shiftReportTest = document.querySelector('#shiftReportTest');
const scheduleState = { date: null, view: 'mine', data: null };
const schedulePanel = document.querySelector('#shiftSchedule');
const consumablesLink = document.querySelector('#shiftConsumablesLink');
const consumablesLinkStatus = document.querySelector('#consumablesLinkStatus');
let employeeDashboardData = null;
let activeTodayDate = null;
let shiftReportAvailable = false;
let canSelectReportClub = false;

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#052c32');
tg?.setBackgroundColor('#052c32');

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
    const availableActions = context.available_actions || ['open', 'close'];
    const reports = context.report_available
      ? `<div class="today-report-grid">${availableActions.includes('open') ? reportAction(open, context.club) : ''}${availableActions.includes('close') ? reportAction(close, context.club) : ''}</div>`
      : '';
    const bookings = context.bookings_available
      ? `<div class="today-bookings-head"><span>Брони</span><b>${number(context.bookings?.count)} · ${number(context.bookings?.participants)} гост.</b></div><div class="today-bookings">${bookingRows(context.bookings)}</div>`
      : '';
    return `
      <article class="today-shift-card">
        <div class="today-shift-head">
          <div><p>${context.date !== today ? `Вчера · ${shiftTime(context)}` : shiftTime(context)}</p><h3>${escapeHtml(context.club)}</h3></div>
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
  loadConsumablesShortcut(payload.today?.[0]?.club).catch(() => {});
}

async function loadConsumablesShortcut(club = null) {
  const query = club ? `?club=${encodeURIComponent(club)}` : '';
  const payload = await api(`/api/shift/consumables${query}`);
  if (payload.selected_club) {
    consumablesLink.href = `/shift/consumables?club=${encodeURIComponent(payload.selected_club)}`;
  }
  const low = Number(payload.summary?.low || 0);
  const active = Number(payload.summary?.active || 0);
  consumablesLinkStatus.textContent = low ? `Мало: ${low} →` : `${active} позиций →`;
  consumablesLinkStatus.classList.toggle('warning', low > 0);
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
  const shiftsCount = days.reduce((total, day) => total + (day.locations || [])
    .reduce((dayTotal, location) => dayTotal + (location.shifts || []).length, 0), 0);
  document.querySelector('#scheduleSummaryCount').textContent = `Смен: ${shiftsCount}`;
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
    <div class="nearest-shift-meta">
      <strong>${dateLabel(nearest.date)}</strong>
      <span>${shiftTime(nearest)}${number(nearest.duration)} ч</span>
    </div>
  ` : `
    <p class="eyebrow">Ближайшая смена</p>
    <h2>Смен пока нет</h2>
    <div class="nearest-shift-meta"><span>В опубликованном расписании новых смен не найдено</span></div>
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

try {
  schedulePanel.open = window.localStorage.getItem('omgShiftScheduleOpen') === '1';
} catch (_error) {
  schedulePanel.open = false;
}
schedulePanel.addEventListener('toggle', () => {
  try {
    window.localStorage.setItem('omgShiftScheduleOpen', schedulePanel.open ? '1' : '0');
  } catch (_error) {
    // Private WebViews can block local storage; the accordion still works.
  }
});

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
