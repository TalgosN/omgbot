const tg = window.Telegram?.WebApp;
const state = {
  me: null,
  month: new Date().toISOString().slice(0, 7),
  employees: [],
  penalties: [],
  monthStatus: null,
  selected: null,
};

const $ = (selector) => document.querySelector(selector);
const employeeList = $('#employeeList');
const employeeDialog = $('#employeeDialog');
const penaltyDialog = $('#penaltyDialog');

tg?.ready();
tg?.expand();
if (tg) {
  tg.setHeaderColor('#0b1118');
  tg.setBackgroundColor('#0b1118');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function number(value, digits = 1) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(Number(value || 0));
}

function money(value) {
  return `${number(value, 1)} ₽`;
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
    <div class="summary-card"><span>Сотрудников</span><strong>${state.employees.length}</strong></div>
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
  employeeList.innerHTML = rows.map((item) => `
    <article class="employee-card" data-login="${escapeHtml(item.login)}">
      <div class="employee-head">
        <div>
          <div class="employee-name">${escapeHtml(item.nickname)}</div>
          <div class="employee-login">${escapeHtml(item.login)}</div>
        </div>
        <div class="kpi-value"><span class="zone">${item.zone}</span>${percent(item.total_pct)}</div>
      </div>
      <div class="employee-meta">
        <div><span>Место</span><strong class="${item.rank == null ? 'rank-none' : ''}">${item.rank ?? '—'}</strong></div>
        <div><span>Смены</span><strong>${number(item.shifts)}</strong></div>
        <div><span>Штрафы</span><strong>${activePenalties(item.login).length}</strong></div>
      </div>
    </article>
  `).join('');
}

function metric(label, fact, ratio) {
  return `<div class="metric-row"><span>${label}</span><strong>${number(fact)} · ${percent(ratio)}</strong></div>`;
}

function renderDialog(employee) {
  state.selected = employee;
  $('#dialogLogin').textContent = employee.login;
  $('#dialogName').textContent = employee.nickname;
  const penalties = state.penalties.filter(
    (item) => item.employee_login.toLowerCase() === employee.login.toLowerCase(),
  );
  $('#dialogContent').innerHTML = `
    <div class="detail-grid">
      <div class="detail-card"><span>Итоговый KPI</span><strong>${employee.zone} ${percent(employee.total_pct)}</strong></div>
      <div class="detail-card"><span>Рейтинг</span><strong>${employee.rank ?? '—'}</strong></div>
      <div class="detail-card"><span>Обычные смены</span><strong>${number(employee.shifts)}</strong></div>
      <div class="detail-card"><span>Взвешенные смены</span><strong>${number(employee.weighted_shifts)}</strong></div>
      <div class="detail-card"><span>Взвешенный KPI</span><strong>${percent(employee.weighted_pct)}</strong></div>
      <div class="detail-card"><span>Сумма</span><strong>${money(employee.amount)}</strong></div>
    </div>
    <h3 class="section-title">Показатели</h3>
    ${metric('Отзывы', employee.reviews, employee.reviews_pct)}
    ${metric('Анкеты', employee.forms, employee.forms_pct)}
    ${metric('Продления', employee.extensions, employee.extensions_pct)}
    ${metric('Сертификаты', employee.certificates, employee.certificates_pct)}
    ${metric('Абонементы', employee.subscriptions, employee.subscriptions_pct)}
    ${metric('Инициативы', employee.initiatives, employee.initiatives_pct)}
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
      `).join('') : '<div class="empty">Штрафов нет</div>'}
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

async function loadData() {
  employeeList.innerHTML = '<div class="loading-card"></div><div class="loading-card"></div>';
  try {
    const payload = await api(`/api/kpi?month=${encodeURIComponent(state.month)}`);
    state.employees = payload.employees;
    state.penalties = payload.penalties;
    state.monthStatus = payload.month_status;
    renderSummary();
    renderStatus();
    renderEmployees();
  } catch (error) {
    employeeList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

async function initialize() {
  $('#monthPicker').value = state.month;
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

function moveMonth(offset) {
  const [year, month] = state.month.split('-').map(Number);
  const next = new Date(year, month - 1 + offset, 1);
  state.month = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, '0')}`;
  $('#monthPicker').value = state.month;
  loadData();
}

$('#previousMonth').addEventListener('click', () => moveMonth(-1));
$('#nextMonth').addEventListener('click', () => moveMonth(1));
$('#monthPicker').addEventListener('change', (event) => {
  if (!event.target.value) return;
  state.month = event.target.value;
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
