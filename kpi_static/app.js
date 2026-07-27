const tg = window.Telegram?.WebApp;

function localIsoDate(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

const state = {
  me: null,
  day: localIsoDate(),
  month: localIsoDate().slice(0, 7),
  employees: [],
  penalties: [],
  monthStatus: null,
  selected: null,
};

const metricLabels = {
  reviews: 'Отзывы',
  forms: 'Анкеты',
  extensions: 'Продления',
  certificates: 'Сертификаты',
  subscriptions: 'Абонементы',
  initiatives: 'Инициативы',
  bs: 'БС',
  shifts: 'Смены',
};

const $ = (selector) => document.querySelector(selector);
const employeeList = $('#employeeList');
const employeeDialog = $('#employeeDialog');
const penaltyDialog = $('#penaltyDialog');
const entriesDialog = $('#entriesDialog');

tg?.ready();
tg?.expand();
if (tg) {
  tg.setHeaderColor('#01032b');
  tg.setBackgroundColor('#01032b');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function signedPercent(value) {
  const rounded = Math.round(Number(value || 0) * 100);
  return `${rounded > 0 ? '+' : ''}${rounded}%`;
}

function number(value, digits = 1) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits })
    .format(Number(value || 0));
}

function dayLabel(day, options = {}) {
  const [year, month, date] = day.split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'long',
    year: options.year ? 'numeric' : undefined,
  }).format(new Date(year, month - 1, date));
}

function monthLabel(month) {
  const [year, monthNumber] = month.split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric' })
    .format(new Date(year, monthNumber - 1, 1));
}

function showToast(message, error = false) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.className = `toast visible${error ? ' error' : ''}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = 'toast'; }, 2600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': tg?.initData || '',
      ...(options.headers || {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Ошибка сервера');
  return payload;
}

function activePenalties(login) {
  return state.penalties.filter((item) => (
    item.employee_login.toLowerCase() === login.toLowerCase()
    && item.status === 'active'
  ));
}

function renderSummary() {
  const participants = state.employees.filter((item) => item.shifts > 0);
  const average = participants.length
    ? participants.reduce((sum, item) => sum + item.total_pct, 0) / participants.length
    : 0;
  const penalties = state.penalties.filter((item) => item.status === 'active').length;
  $('#summary').innerHTML = `
    <div class="summary-card"><span>В рейтинге</span><strong>${participants.length}</strong></div>
    <div class="summary-card"><span>Средний KPI</span><strong>${percent(average)}</strong></div>
    <div class="summary-card"><span>Штрафов</span><strong>${penalties}</strong></div>
  `;
}

function renderStatus() {
  const button = $('#monthStatusButton');
  if (!state.me?.can_manage) {
    button.hidden = true;
    return;
  }
  button.hidden = false;
  const closed = Boolean(state.monthStatus?.is_closed);
  button.className = `status-button${closed ? ' closed' : ''}`;
  button.textContent = closed ? '✓ Месяц закрыт' : 'Закрыть месяц';
}

function rankMovement(item) {
  if (!item.rank_change) return '';
  const direction = item.rank_change > 0 ? 'up' : 'down';
  const arrow = item.rank_change > 0 ? '↑' : '↓';
  return `<small class="movement ${direction}">${arrow}${Math.abs(item.rank_change)} за день</small>`;
}

function kpiMovement(item) {
  if (!item.kpi_change) return '';
  const direction = item.kpi_change > 0 ? 'up' : 'down';
  return `<small class="movement ${direction}">${signedPercent(item.kpi_change)}</small>`;
}

function renderEmployees() {
  const query = $('#searchInput').value.trim().toLowerCase();
  const rows = state.employees
    .filter((item) => `${item.nickname} ${item.login}`.toLowerCase().includes(query))
    .sort((a, b) => {
      if (a.rank == null && b.rank == null) return a.nickname.localeCompare(b.nickname, 'ru');
      if (a.rank == null) return 1;
      if (b.rank == null) return -1;
      return a.rank - b.rank;
    });

  $('#resultCount').textContent = `${rows.length} из ${state.employees.length}`;
  if (!rows.length) {
    employeeList.innerHTML = '<div class="empty">Ничего не найдено</div>';
    return;
  }
  employeeList.innerHTML = rows.map((item) => {
    const penaltyCount = activePenalties(item.login).length;
    return `
      <article class="employee-card" data-login="${escapeHtml(item.login)}">
        <div class="employee-head">
          <div>
            <div class="employee-name">${escapeHtml(item.nickname)}</div>
            <div class="employee-login">${escapeHtml(item.login)}</div>
          </div>
          <span class="zone-badge" title="Положение относительно среднего">${item.zone}</span>
        </div>
        <div class="employee-stats">
          <div class="employee-stat">
            <span>Рейтинг</span>
            <strong>${item.rank ?? '—'}</strong>
            ${rankMovement(item)}
          </div>
          <div class="employee-stat featured">
            <span>KPI</span>
            <strong>${percent(item.total_pct)} <em>(${percent(item.weighted_pct)})</em></strong>
            ${kpiMovement(item)}
          </div>
          <div class="employee-stat">
            <span>Смен</span>
            <strong>${number(item.shifts)} <em>(${number(item.weighted_shifts)})</em></strong>
          </div>
        </div>
        ${penaltyCount ? `<div class="employee-alert">−${penaltyCount * 10}% · штрафов: ${penaltyCount}</div>` : ''}
      </article>
    `;
  }).join('');
}

function metric(label, key, fact, ratio) {
  return `
    <button class="metric-row" type="button" data-metric="${key}">
      <span>${label}<small>Показать записи</small></span>
      <strong>${number(fact)} · ${percent(ratio)}</strong>
      <b aria-hidden="true">›</b>
    </button>
  `;
}

function renderDialog(employee) {
  state.selected = employee;
  $('#dialogLogin').textContent = `${employee.login} · на ${dayLabel(state.day)}`;
  $('#dialogName').textContent = employee.nickname;
  const penalties = state.penalties.filter(
    (item) => item.employee_login.toLowerCase() === employee.login.toLowerCase(),
  );
  $('#dialogContent').innerHTML = `
    <div class="detail-grid">
      <button class="detail-card clickable" type="button" data-metric="shifts">
        <span>Смены</span>
        <strong>${number(employee.shifts)} <em>(${number(employee.weighted_shifts)})</em></strong>
        <small>Открыть записи</small>
      </button>
      <div class="detail-card">
        <span>Рейтинг</span>
        <strong>${employee.rank ?? '—'} ${rankMovement(employee)}</strong>
      </div>
      <div class="detail-card detail-kpi">
        <span>Итоговый KPI</span>
        <strong>${employee.zone} ${percent(employee.total_pct)} <em>(${percent(employee.weighted_pct)})</em></strong>
        ${kpiMovement(employee)}
      </div>
    </div>
    <p class="weighted-note">Значения в скобках — взвешенные.</p>
    <h3 class="section-title">Показатели</h3>
    ${metric('Отзывы', 'reviews', employee.reviews, employee.reviews_pct)}
    ${metric('Анкеты', 'forms', employee.forms, employee.forms_pct)}
    ${metric('Продления', 'extensions', employee.extensions, employee.extensions_pct)}
    ${metric('Сертификаты', 'certificates', employee.certificates, employee.certificates_pct)}
    ${metric('Абонементы', 'subscriptions', employee.subscriptions, employee.subscriptions_pct)}
    ${metric('Инициативы', 'initiatives', employee.initiatives, employee.initiatives_pct)}
    ${metric('БС', 'bs', employee.bs, employee.bs_pct)}
    <h3 class="section-title">Штрафы</h3>
    <div class="penalty-list">
      ${penalties.length ? penalties.map((item) => `
        <div class="penalty-item ${item.status === 'cancelled' ? 'cancelled' : ''}">
          <span class="penalty-meta">${item.status === 'active' ? '−10% KPI' : 'Отменён'} · ${escapeHtml(item.created_by_login || '—')}</span>
          <p>${escapeHtml(item.reason)}</p>
          ${state.me.can_manage && item.status === 'active'
            ? `<button class="danger-button cancel-penalty" data-id="${item.id}">Отменить</button>`
            : ''}
        </div>
      `).join('') : '<div class="empty compact">Штрафов нет</div>'}
    </div>
    ${state.me.can_manage ? `
      <div class="manager-actions">
        <label class="stream-toggle">
          <span>Трансляция +5%</span>
          <input id="streamToggle" type="checkbox" ${employee.stream ? 'checked' : ''}>
        </label>
        <button id="addPenalty" class="danger-button">Добавить штраф</button>
        <button class="secondary-button close-dialog">Закрыть</button>
      </div>
    ` : ''}
  `;
  employeeDialog.showModal();
}

async function showMetricEntries(metricKey) {
  const label = metricLabels[metricKey] || metricKey;
  $('#entriesEmployee').textContent = state.selected.nickname;
  $('#entriesTitle').textContent = label;
  $('#entriesPeriod').textContent = `С начала ${monthLabel(state.month)} по ${dayLabel(state.day, { year: true })}`;
  $('#entriesContent').innerHTML = '<div class="loading-card entries-loading"></div>';
  entriesDialog.showModal();
  try {
    const params = new URLSearchParams({
      month: state.month,
      date: state.day,
      employee_login: state.selected.login,
      metric: metricKey,
    });
    const payload = await api(`/api/kpi/details?${params}`);
    $('#entriesContent').innerHTML = payload.entries.length
      ? payload.entries.map((item) => `
        <article class="entry">
          <div class="entry-head">
            <time>${dayLabel(item.date, { year: true })}</time>
            <strong>${number(item.value)}</strong>
          </div>
          ${item.description ? `<p>${escapeHtml(item.description)}</p>` : ''}
          ${(item.club || item.status) ? `
            <small>${[item.club, item.status].filter(Boolean).map(escapeHtml).join(' · ')}</small>
          ` : ''}
        </article>
      `).join('')
      : '<div class="empty">До выбранной даты записей нет</div>';
  } catch (error) {
    $('#entriesContent').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function loadData() {
  employeeList.innerHTML = '<div class="loading-card"></div><div class="loading-card"></div>';
  try {
    const params = new URLSearchParams({ month: state.month, date: state.day });
    const payload = await api(`/api/kpi?${params}`);
    state.day = payload.date;
    state.month = payload.month;
    state.employees = payload.employees;
    state.penalties = payload.penalties;
    state.monthStatus = payload.month_status;
    $('#datePicker').value = state.day;
    renderSummary();
    renderStatus();
    renderEmployees();
  } catch (error) {
    employeeList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

async function initialize() {
  $('#datePicker').value = state.day;
  try {
    state.me = await api('/api/me');
    $('#userBadge').textContent = `${state.me.name} · ${state.me.role_name}`;
    $('#userBadge').classList.remove('skeleton');
    await loadData();
  } catch (error) {
    employeeList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

function moveDay(offset) {
  const [year, month, day] = state.day.split('-').map(Number);
  const next = new Date(year, month - 1, day + offset);
  state.day = localIsoDate(next);
  state.month = state.day.slice(0, 7);
  $('#datePicker').value = state.day;
  loadData();
}

$('#previousDay').addEventListener('click', () => moveDay(-1));
$('#nextDay').addEventListener('click', () => moveDay(1));
$('#datePicker').addEventListener('change', (event) => {
  if (!event.target.value) return;
  state.day = event.target.value;
  state.month = state.day.slice(0, 7);
  loadData();
});
$('#searchInput').addEventListener('input', renderEmployees);

employeeList.addEventListener('click', (event) => {
  const card = event.target.closest('.employee-card');
  if (!card) return;
  const employee = state.employees.find((item) => item.login === card.dataset.login);
  if (employee) renderDialog(employee);
});

document.addEventListener('click', (event) => {
  if (event.target.closest('.close-dialog')) {
    event.target.closest('dialog')?.close();
  }
});

employeeDialog.addEventListener('click', async (event) => {
  const metricButton = event.target.closest('[data-metric]');
  if (metricButton) {
    await showMetricEntries(metricButton.dataset.metric);
    return;
  }
  if (event.target.id === 'addPenalty') {
    $('#penaltyEmployee').textContent = state.selected.nickname;
    $('#penaltyReason').value = '';
    penaltyDialog.showModal();
  }
  if (event.target.id === 'streamToggle') {
    try {
      await api('/api/streams', {
        method: 'POST',
        body: JSON.stringify({
          month: state.month,
          employee_login: state.selected.login,
          enabled: event.target.checked,
        }),
      });
      showToast('Отметка трансляции сохранена');
      employeeDialog.close();
      await loadData();
    } catch (error) {
      event.target.checked = !event.target.checked;
      showToast(error.message, true);
    }
  }
  const cancelButton = event.target.closest('.cancel-penalty');
  if (cancelButton) {
    const reason = window.prompt('Причина отмены штрафа');
    if (!reason) return;
    try {
      await api(`/api/penalties/${cancelButton.dataset.id}/cancel`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      });
      showToast('Штраф отменён');
      employeeDialog.close();
      await loadData();
    } catch (error) {
      showToast(error.message, true);
    }
  }
});

$('#penaltyForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/penalties', {
      method: 'POST',
      body: JSON.stringify({
        month: state.month,
        employee_login: state.selected.login,
        reason: $('#penaltyReason').value,
      }),
    });
    penaltyDialog.close();
    employeeDialog.close();
    showToast('Штраф −10% добавлен');
    await loadData();
  } catch (error) {
    showToast(error.message, true);
  }
});

$('#monthStatusButton').addEventListener('click', async () => {
  const closed = Boolean(state.monthStatus?.is_closed);
  const action = closed ? 'открыть' : 'закрыть';
  if (!window.confirm(`${action[0].toUpperCase()}${action.slice(1)} ${monthLabel(state.month)}?`)) return;
  try {
    state.monthStatus = await api('/api/month-status', {
      method: 'POST',
      body: JSON.stringify({ month: state.month, is_closed: !closed }),
    });
    renderStatus();
    showToast(closed ? 'Месяц снова открыт' : 'Месяц закрыт');
  } catch (error) {
    showToast(error.message, true);
  }
});

initialize();
