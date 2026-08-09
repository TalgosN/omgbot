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
  myKpi: null,
  freshness: null,
  selected: null,
  analytics: null,
  analyticsEmployees: new Set(),
  settings: null,
  filters: {
    club: '',
    role: '',
    zone: '',
    condition: '',
    attention: false,
  },
};

const analyticsCache = new Map();
const chartColors = ['#9b72ff', '#5ee7c4', '#ff78aa', '#ffd166', '#5ab8ff'];
const metricLabels = {
  reviews: 'Отзывы',
  forms: 'Анкеты',
  extensions: 'Продления',
  certificates: 'Сертификаты',
  subscriptions: 'Абонементы',
  initiatives: 'Инициативы',
  shifts: 'Смены',
};
const settingsMetricUnits = {
  'Отзывы': 'на одну смену',
  'Анкеты': 'на одну смену',
  'Продления': 'на одну смену',
  'Сертификаты': 'на одну смену',
  'Абонементы': 'на одну смену',
  'Инициативы': '% к итоговому KPI за одну инициативу',
};

const $ = (selector) => document.querySelector(selector);
const employeeList = $('#employeeList');
const employeeDialog = $('#employeeDialog');
const penaltyDialog = $('#penaltyDialog');
const entriesDialog = $('#entriesDialog');
const monthCloseDialog = $('#monthCloseDialog');

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

function numericDayLabel(day) {
  const [year, month, date] = day.split('-');
  return `${date}.${month}.${year}`;
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
  button.title = closed && state.monthStatus?.snapshot
    ? `Snapshot: ${state.monthStatus.snapshot.summary?.participants || 0} участников`
    : '';
}

function renderMonthClosePreview(payload) {
  const summary = payload.summary || {};
  const zones = summary.zones || {};
  const warnings = payload.warnings || [];
  $('#monthCloseTitle').textContent = `Закрыть ${monthLabel(state.month)}?`;
  $('#monthCloseContent').innerHTML = `
    <div class="close-summary">
      <div><span>Участников</span><strong>${summary.participants || 0}</strong></div>
      <div><span>Средний KPI</span><strong>${percent(summary.average_pct)}</strong></div>
      <div><span>Зоны</span><strong>${zones['🟢'] || 0} · ${zones['🟡'] || 0} · ${zones['🔴'] || 0}</strong></div>
    </div>
    <p class="close-period">Данные по ${dayLabel(payload.date, { year: true })}</p>
    <h3 class="section-title">Предупреждения</h3>
    <div class="close-warnings">
      ${warnings.length ? warnings.map((warning) => {
    const names = warning.employees
      .slice(0, 4)
      .map((employee) => escapeHtml(employee.nickname || employee.login))
      .join(', ');
    const remaining = warning.count - Math.min(warning.count, 4);
    return `
          <article class="close-warning">
            <div>
              <strong>${escapeHtml(warning.label)}</strong>
              <span>${warning.count}</span>
            </div>
            <small>${names}${remaining > 0 ? ` и ещё ${remaining}` : ''}</small>
          </article>
        `;
  }).join('') : `
        <div class="close-ready">
          Критичных предупреждений по данным месяца нет.
        </div>
      `}
    </div>
    <p class="close-note">
      Предупреждения не блокируют закрытие. При повторном закрытии предыдущий
      snapshot будет заменён текущими данными.
    </p>
  `;
}

async function openMonthCloseCheck() {
  $('#monthCloseContent').innerHTML = '<div class="loading-card"></div>';
  $('#confirmMonthClose').disabled = true;
  monthCloseDialog.showModal();
  try {
    const params = new URLSearchParams({ month: state.month });
    const payload = await api(`/api/month-close-check?${params}`);
    renderMonthClosePreview(payload);
    $('#confirmMonthClose').disabled = false;
  } catch (error) {
    $('#monthCloseContent').innerHTML = `
      <div class="empty">${escapeHtml(error.message)}</div>
    `;
    showToast(error.message, true);
  }
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

function renderFreshness() {
  const freshness = state.freshness || {};
  const calculated = freshness.calculated_at
    ? new Intl.DateTimeFormat('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(freshness.calculated_at))
    : '—';
  const recordDate = (value) => (value ? numericDayLabel(value) : 'записей нет');
  $('#freshness').innerHTML = `
    <div>
      <span class="freshness-dot"></span>
      <div>
        <strong>Рассчитано ${calculated}</strong>
        <small>KPI-запись: ${recordDate(freshness.latest_metric_date)}
          · смена: ${recordDate(freshness.latest_shift_date)}</small>
      </div>
    </div>
    <button id="refreshKpi" type="button">Обновить</button>
  `;
}

function explanationMetric(item, compact = false) {
  const target = item.target == null
    ? `Каждая инициатива добавляет ${percent(item.bonus_per_item)} к итоговому KPI`
    : `Норма на ${number(state.myKpi?.shifts || 0)} смен: ${number(item.target)}`;
  const needed = item.target != null && item.needed > 0
    ? `<small class="metric-gap">До нормы: ${number(item.needed)}</small>`
    : '<small class="metric-done">Норма выполнена</small>';
  return `
    <button class="explanation-metric${compact ? ' compact' : ''}" type="button"
      data-metric="${item.key}">
      <div>
        <span>${escapeHtml(item.label)}</span>
        <small>${target}</small>
      </div>
      <div class="explanation-values">
        <strong>${number(item.fact)} · ${percent(item.ratio)}</strong>
        <em>Вклад ${signedPercent(item.contribution_pct)}</em>
        ${item.target == null ? '' : needed}
      </div>
    </button>
  `;
}

function renderMyKpi() {
  const container = $('#myKpi');
  if (state.me?.can_edit_settings) {
    const participants = state.employees.filter((item) => Number(item.shifts || 0) > 0);
    const average = participants.length
      ? participants.reduce((sum, item) => sum + Number(item.total_pct || 0), 0)
        / participants.length
      : 0;
    const activePenaltiesCount = state.penalties.filter(
      (item) => item.status === 'active',
    ).length;
    const redZone = participants.filter((item) => item.zone === '🔴').length;
    const noShifts = state.employees.length - participants.length;
    const movements = participants
      .filter((item) => item.kpi_change_7d != null)
      .sort((a, b) => Number(b.kpi_change_7d) - Number(a.kpi_change_7d));
    const growth = movements.find((item) => Number(item.kpi_change_7d) > 0);
    const decline = [...movements].reverse().find(
      (item) => Number(item.kpi_change_7d) < 0,
    );
    const movementCard = (label, employee, tone) => `
      <div class="owner-movement ${tone}">
        <span>${label}</span>
        <strong>${employee ? escapeHtml(employee.nickname) : 'Нет данных'}</strong>
        <small>${employee ? signedPercent(employee.kpi_change_7d) : 'за последние 7 дней'}</small>
      </div>
    `;
    container.innerHTML = `
      <article class="owner-dashboard">
        <div class="owner-dashboard-head">
          <p class="eyebrow">Сводка владельца · ${dayLabel(state.day)}</p>
          <h2>Команда сегодня</h2>
        </div>
        <div class="owner-summary-grid">
          <div><span>В рейтинге</span><strong>${participants.length}</strong></div>
          <div><span>Средний KPI</span><strong>${percent(average)}</strong></div>
          <div><span>Красная зона</span><strong>${redZone}</strong></div>
          <div><span>Штрафы</span><strong>${activePenaltiesCount}</strong></div>
          <div><span>Без смен</span><strong>${noShifts}</strong></div>
        </div>
        <div class="owner-movements">
          ${movementCard('Лучший рост', growth, 'positive')}
          ${movementCard('Снижение', decline, 'negative')}
        </div>
        <button class="secondary-button open-owner-settings owner-settings-button" type="button">
          <span>Настройки KPI</span><span>→</span>
        </button>
      </article>
    `;
    return;
  }
  const employee = state.myKpi;
  if (!employee) {
    container.innerHTML = `
      <div class="empty my-empty">
        KPI-профиль не найден. Проверьте Telegram-логин в разделе «Аккаунт».
      </div>
    `;
    return;
  }
  const explanation = employee.explanation || {};
  const pace = explanation.pace || {};
  const metrics = explanation.metrics || [];
  const paceText = pace.available
    ? percent(pace.projected_pct)
    : 'Нет смен';
  const greenText = pace.gap_to_green_pct > 0
    ? `До среднего команды: ${percent(pace.gap_to_green_pct)}`
    : 'Не ниже среднего команды';
  const adjustments = [
    explanation.penalty_impact_pct
      ? `<span class="negative">Штрафы −${percent(explanation.penalty_impact_pct)}</span>`
      : '',
    explanation.stream_bonus_pct
      ? `<span class="positive">Трансляция +${percent(explanation.stream_bonus_pct)}</span>`
      : '',
  ].filter(Boolean).join('');

  container.innerHTML = `
    <article class="my-hero">
      <div class="my-hero-head">
        <div>
          <p class="eyebrow">${escapeHtml(employee.login)} · ${dayLabel(state.day)}</p>
          <h2>${escapeHtml(employee.nickname)}</h2>
        </div>
        <span class="zone-badge">${employee.zone}</span>
      </div>
      <div class="my-score-grid">
        <div>
          <span>Мой KPI</span>
          <strong>${percent(employee.total_pct)}</strong>
          ${kpiMovement(employee)}
        </div>
        <div>
          <span>Место</span>
          <strong>${employee.rank ?? '—'}</strong>
          ${rankMovement(employee)}
        </div>
        <div>
          <span>Смены</span>
          <strong>${number(employee.shifts)}</strong>
          <small>взвешенные ${number(employee.weighted_shifts)}</small>
        </div>
      </div>
      <div class="pace-card">
        <div>
          <span>Прогноз по фактически отработанным сменам</span>
          <strong>${paceText}</strong>
        </div>
        <small>${greenText}</small>
      </div>
      ${adjustments ? `<div class="adjustments">${adjustments}</div>` : ''}
      <button class="secondary-button open-my-detail" type="button">
        Открыть полную карточку
      </button>
    </article>
    <section class="explanation-card">
      <div class="explanation-heading">
        <div>
          <p class="eyebrow">Расшифровка</p>
          <h2>Из чего сложился KPI</h2>
        </div>
        <strong>${percent(explanation.total_pct)}</strong>
      </div>
      <p class="formula-note">
        Основные показатели делятся на норму для фактических смен.
        Нажмите показатель, чтобы увидеть исходные записи.
      </p>
      <div class="explanation-list">
        ${metrics.map((item) => explanationMetric(item)).join('')}
      </div>
      <div class="formula-total">
        <span>Вклад показателей</span>
        <strong>${percent(explanation.metric_contribution_pct)}</strong>
      </div>
    </section>
  `;
}

function renderManagerFilters() {
  const panel = $('#managerFilters');
  if (!state.me?.can_manage) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const clubs = [...new Set(
    state.employees.flatMap((employee) => employee.clubs || []),
  )].sort((a, b) => a.localeCompare(b, 'ru'));
  const roles = [...new Map(
    state.employees
      .filter((employee) => employee.role != null)
      .map((employee) => [String(employee.role), employee.role_name]),
  ).entries()];
  $('#clubFilter').innerHTML = [
    '<option value="">Все клубы</option>',
    ...clubs.map((club) => (
      `<option value="${escapeHtml(club)}">${escapeHtml(club)}</option>`
    )),
  ].join('');
  $('#roleFilter').innerHTML = [
    '<option value="">Все роли</option>',
    ...roles.map(([role, label]) => (
      `<option value="${role}">${escapeHtml(label)}</option>`
    )),
  ].join('');
  $('#clubFilter').value = state.filters.club;
  $('#roleFilter').value = state.filters.role;
  $('#zoneFilter').value = state.filters.zone;
  $('#stateFilter').value = state.filters.condition;
  const attentionCount = state.employees.filter((item) => item.needs_attention).length;
  $('#attentionCount').textContent = attentionCount;
  $('#attentionToggle').classList.toggle('active', state.filters.attention);
  $('#ratingTitle').textContent = state.filters.attention
    ? 'Требует внимания'
    : 'Рейтинг команды';
}

function renderEmployees() {
  const query = $('#searchInput').value.trim().toLowerCase();
  const rows = state.employees
    .filter((item) => `${item.nickname} ${item.login}`.toLowerCase().includes(query))
    .filter((item) => (
      !state.me?.can_manage
      || (
        (!state.filters.attention || item.needs_attention)
        && (!state.filters.club || (item.clubs || []).includes(state.filters.club))
        && (
          !state.filters.role
          || String(item.role) === state.filters.role
        )
        && (!state.filters.zone || item.zone === state.filters.zone)
        && (
          !state.filters.condition
          || (
            state.filters.condition === 'no_shifts'
              ? Number(item.shifts || 0) <= 0
              : (item.attention_reasons || []).some(
                (reason) => reason.key === state.filters.condition,
              )
          )
        )
      )
    ))
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
    const attention = state.me?.can_manage
      ? (item.attention_reasons || []).map((reason) => (
        `<span>${escapeHtml(reason.label)}</span>`
      )).join('')
      : '';
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
        ${hashtagChips(item)}
        ${penaltyCount ? `<div class="employee-alert">−${penaltyCount * 10}% · штрафов: ${penaltyCount}</div>` : ''}
        ${attention ? `<div class="attention-reasons">${attention}</div>` : ''}
      </article>
    `;
  }).join('');
}

function hashtagValue(item) {
  if (item.total_value == null) return `×${item.count}`;
  const units = {
    hours: 'ч',
    hour: 'ч',
  };
  return `×${item.count} · ${number(item.total_value)} ${units[item.value_unit] || item.value_unit}`;
}

function hashtagChips(employee, clickable = false) {
  const hashtags = employee.extra_hashtags || [];
  if (!hashtags.length) return '';
  return `
    <div class="employee-hashtags">
      ${hashtags.map((item) => (
        clickable
          ? `<button type="button" data-hashtag="${escapeHtml(item.hashtag)}">
              ${escapeHtml(item.hashtag)} <b>${escapeHtml(hashtagValue(item))}</b>
            </button>`
          : `<span>${escapeHtml(item.hashtag)} <b>${escapeHtml(hashtagValue(item))}</b></span>`
      )).join('')}
    </div>
  `;
}

function metric(label, key, fact, ratio, explanation = null) {
  const details = explanation
    ? `Норма: ${explanation.target == null ? `${percent(explanation.bonus_per_item)} за инициативу` : number(explanation.target)}
       · вклад ${signedPercent(explanation.contribution_pct)}`
    : 'Показать записи';
  return `
    <button class="metric-row" type="button" data-metric="${key}">
      <span>${label}<small>${details}</small></span>
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
  const explanation = Object.fromEntries(
    (employee.explanation?.metrics || []).map((item) => [item.key, item]),
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
    ${metric('Отзывы', 'reviews', employee.reviews, employee.reviews_pct, explanation.reviews)}
    ${metric('Анкеты', 'forms', employee.forms, employee.forms_pct, explanation.forms)}
    ${metric('Продления', 'extensions', employee.extensions, employee.extensions_pct, explanation.extensions)}
    ${metric('Сертификаты', 'certificates', employee.certificates, employee.certificates_pct, explanation.certificates)}
    ${metric('Абонементы', 'subscriptions', employee.subscriptions, employee.subscriptions_pct, explanation.subscriptions)}
    ${metric('Инициативы', 'initiatives', employee.initiatives, employee.initiatives_pct, explanation.initiatives)}
    ${(employee.extra_hashtags || []).length ? `
      <h3 class="section-title">Другие активности</h3>
      ${hashtagChips(employee, true)}
    ` : ''}
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
  const hashtag = metricKey.startsWith('hashtag:')
    ? metricKey.slice('hashtag:'.length)
    : '';
  const label = hashtag || metricLabels[metricKey] || metricKey;
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
            ${item.value == null ? '' : `<strong>${number(item.value)}${
              item.value_unit === 'hours' ? ' ч' : ''
            }</strong>`}
          </div>
          ${item.description ? `<p>${escapeHtml(item.description)}</p>` : ''}
          ${(item.club || item.status) ? `
            <small>${[item.club, item.status].filter(Boolean).map(escapeHtml).join(' · ')}</small>
          ` : ''}
          ${item.source ? `<small>Источник: ${escapeHtml(item.source)}</small>` : ''}
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
    state.myKpi = payload.my_kpi;
    state.freshness = payload.freshness;
    $('#datePicker').value = state.day;
    $('#dateDisplay').textContent = numericDayLabel(state.day);
    renderSummary();
    renderStatus();
    renderFreshness();
    renderMyKpi();
    renderManagerFilters();
    renderEmployees();
  } catch (error) {
    employeeList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#myKpi').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

function chartOptions() {
  const individual = $('#analyticsScope').value === 'individual';
  return individual
    ? [
      ['kpi', 'KPI'],
      ['rank', 'Место в рейтинге'],
      ['shifts', 'Смены'],
      ['metric', 'Показатель'],
    ]
    : [
      ['kpi', 'Средний KPI'],
      ['shifts', 'Смены команды'],
      ['metric', 'Показатель команды'],
      ['zones', 'Зоны рейтинга'],
      ['top', 'Топ сотрудников'],
    ];
}

function updateChartOptions() {
  const select = $('#analyticsChart');
  const previous = select.value;
  const options = chartOptions();
  select.innerHTML = options
    .map(([value, label]) => `<option value="${value}">${label}</option>`)
    .join('');
  if (options.some(([value]) => value === previous)) select.value = previous;
  $('#employeePicker').hidden = $('#analyticsScope').value !== 'individual';
  renderAnalytics();
}

function analyticsLabel(value, mode) {
  if (mode === 'daily') {
    const [, month, day] = value.split('-');
    return `${day}.${month}`;
  }
  const [year, month] = value.split('-');
  return `${month}.${year.slice(2)}`;
}

function employeeAt(point, login) {
  return point.employees.find((employee) => employee.login === login);
}

function selectedAnalyticsEmployees() {
  return state.analytics?.employees.filter(
    (employee) => state.analyticsEmployees.has(employee.login),
  ) || [];
}

function renderEmployeeChips() {
  const employees = state.analytics?.employees || [];
  if (!state.analyticsEmployees.size && employees.length) {
    const ownLogin = String(state.me?.login || '').toLowerCase();
    const initial = employees.find((employee) => employee.login === ownLogin) || employees[0];
    state.analyticsEmployees.add(initial.login);
  }
  $('#employeeChips').innerHTML = employees.map((employee) => `
    <button
      type="button"
      class="employee-chip${state.analyticsEmployees.has(employee.login) ? ' selected' : ''}"
      data-login="${escapeHtml(employee.login)}"
    >${escapeHtml(employee.nickname)}</button>
  `).join('');
}

function lineChart(series, labels, options = {}) {
  const width = 720;
  const height = 310;
  const pad = { left: 48, right: 18, top: 20, bottom: 38 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const values = series.flatMap((item) => item.values)
    .filter((value) => value != null && Number.isFinite(Number(value)))
    .map(Number);
  if (!values.length) return '<div class="empty">За период нет данных</div>';

  let min = options.rank ? Math.min(...values) : Math.min(0, ...values);
  let max = Math.max(...values);
  if (min === max) {
    min = options.rank ? Math.max(0, min - 1) : 0;
    max += 1;
  }
  const x = (index) => pad.left + (
    labels.length > 1 ? index * plotWidth / (labels.length - 1) : plotWidth / 2
  );
  const y = (value) => {
    const ratio = (Number(value) - min) / (max - min);
    return options.rank
      ? pad.top + ratio * plotHeight
      : pad.top + (1 - ratio) * plotHeight;
  };
  const format = options.percent ? percent : (value) => number(value);

  const grid = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const gridY = pad.top + ratio * plotHeight;
    const value = options.rank
      ? min + ratio * (max - min)
      : max - ratio * (max - min);
    return `
      <line x1="${pad.left}" y1="${gridY}" x2="${width - pad.right}" y2="${gridY}" />
      <text x="${pad.left - 8}" y="${gridY + 4}" text-anchor="end">${escapeHtml(format(value))}</text>
    `;
  }).join('');

  const paths = series.map((item, seriesIndex) => {
    const points = item.values
      .map((value, index) => (
        value == null ? null : `${x(index)},${y(value)}`
      ))
      .filter(Boolean);
    if (!points.length) return '';
    const lastIndex = [...item.values].map((value, index) => (
      value == null ? null : index
    )).filter((value) => value != null).at(-1);
    const color = chartColors[seriesIndex % chartColors.length];
    const flags = (item.flags || []).map((flag, index) => {
      if (!flag || item.values[index] == null) return '';
      return `
        ${flag.penalties ? `<circle cx="${x(index)}" cy="${y(item.values[index])}" r="8" fill="none" stroke="#ff5c8a" stroke-width="2" />` : ''}
        ${flag.stream ? `<circle cx="${x(index)}" cy="${y(item.values[index])}" r="11" fill="none" stroke="#6ef2b2" stroke-width="2" />` : ''}
      `;
    }).join('');
    return `
      <polyline points="${points.join(' ')}" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" />
      ${flags}
      <circle cx="${x(lastIndex)}" cy="${y(item.values[lastIndex])}" r="5" fill="${color}" stroke="#08083b" stroke-width="3" />
    `;
  }).join('');

  const labelIndexes = [...new Set([0, Math.floor((labels.length - 1) / 2), labels.length - 1])];
  const xLabels = labelIndexes.map((index) => `
    <text x="${x(index)}" y="${height - 10}" text-anchor="${index === 0 ? 'start' : index === labels.length - 1 ? 'end' : 'middle'}">
      ${escapeHtml(labels[index])}
    </text>
  `).join('');

  return `
    <svg class="analytics-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(options.title || 'График')}">
      <g class="chart-grid">${grid}${xLabels}</g>
      <g>${paths}</g>
    </svg>
  `;
}

function barChart(items) {
  const max = Math.max(...items.map((item) => item.value), 0.01);
  return `
    <div class="ranking-bars">
      ${items.map((item, index) => `
        <div class="ranking-bar">
          <div><span>#${index + 1} ${escapeHtml(item.name)}</span><strong>${percent(item.value)}</strong></div>
          <i><b style="width:${Math.max(2, item.value / max * 100)}%"></b></i>
        </div>
      `).join('')}
    </div>
  `;
}

function renderChartLegend(series, showFlags = false) {
  const flags = showFlags ? `
    <span><i class="penalty-ring"></i>штраф</span>
    <span><i class="stream-ring"></i>трансляция</span>
  ` : '';
  $('#chartLegend').innerHTML = series.map((item, index) => `
    <span><i style="background:${chartColors[index % chartColors.length]}"></i>${escapeHtml(item.name)}</span>
  `).join('') + flags;
}

function renderAnalyticsSummary(scope, latest, selected) {
  if (!latest) {
    $('#analyticsSummary').innerHTML = '';
    return;
  }
  if (scope === 'team') {
    $('#analyticsSummary').innerHTML = `
      <div><span>Сотрудников</span><strong>${latest.team.employees}</strong></div>
      <div><span>Средний KPI</span><strong>${percent(latest.team.kpi)}</strong></div>
      <div><span>Смен</span><strong>${number(latest.team.shifts)}</strong></div>
      <div><span>Зоны</span><strong>🟢 ${latest.team.zones['🟢']} · 🟡 ${latest.team.zones['🟡']} · 🔴 ${latest.team.zones['🔴']}</strong></div>
    `;
    return;
  }
  const cards = selected.map((employee) => {
    const row = employeeAt(latest, employee.login);
    if (!row) return '';
    const controls = [
      row.penalties ? `−${row.penalties * 10}%` : '',
      row.stream ? '+5% трансляция' : '',
    ].filter(Boolean).join(' · ');
    return `
      <div>
        <span>${escapeHtml(employee.nickname)}</span>
        <strong>${percent(row.kpi)} · #${row.rank ?? '—'}</strong>
        ${controls ? `<small>${controls}</small>` : ''}
      </div>
    `;
  }).join('');
  $('#analyticsSummary').innerHTML = cards || '<div class="empty">Выберите сотрудника</div>';
}

function renderAnalytics() {
  if (!state.analytics) return;
  const scope = $('#analyticsScope').value;
  const chart = $('#analyticsChart').value || 'kpi';
  const weighted = $('#analyticsWeighted').checked;
  const metric = $('#analyticsMetric').value;
  const labels = state.analytics.points.map(
    (point) => analyticsLabel(point.label, state.analytics.mode),
  );
  const selected = selectedAnalyticsEmployees();
  let series = [];
  let title = '';
  let percentValues = false;
  let rankValues = false;
  let customChart = '';

  $('#metricField').hidden = chart !== 'metric';
  $('#weightedField').hidden = !['kpi', 'shifts'].includes(chart);
  $('#chartEyebrow').textContent = scope === 'team' ? 'Команда' : 'Сравнение сотрудников';

  if (scope === 'team') {
    if (chart === 'kpi') {
      title = weighted ? 'Средний взвешенный KPI' : 'Средний KPI';
      percentValues = true;
      series = [{
        name: title,
        values: state.analytics.points.map(
          (point) => point.team[weighted ? 'weighted_kpi' : 'kpi'],
        ),
      }];
    } else if (chart === 'shifts') {
      title = weighted ? 'Взвешенные смены команды' : 'Смены команды';
      series = [{
        name: title,
        values: state.analytics.points.map(
          (point) => point.team[weighted ? 'weighted_shifts' : 'shifts'],
        ),
      }];
    } else if (chart === 'metric') {
      title = `${metricLabels[metric]} команды`;
      series = [{
        name: metricLabels[metric],
        values: state.analytics.points.map((point) => point.team[metric]),
      }];
    } else if (chart === 'zones') {
      title = 'Распределение по зонам';
      series = ['🟢', '🟡', '🔴'].map((zone) => ({
        name: zone,
        values: state.analytics.points.map((point) => point.team.zones[zone]),
      }));
    } else {
      title = 'Топ сотрудников';
      const latest = state.analytics.points.at(-1);
      const top = [...(latest?.employees || [])]
        .filter((employee) => employee.shifts > 0)
        .sort((left, right) => right.kpi - left.kpi)
        .slice(0, 5)
        .map((employee) => ({ name: employee.nickname, value: employee.kpi }));
      customChart = top.length
        ? barChart(top)
        : '<div class="empty">За период нет данных</div>';
    }
  } else {
    if (chart === 'kpi') {
      title = weighted ? 'Взвешенный KPI' : 'KPI';
      percentValues = true;
    } else if (chart === 'rank') {
      title = 'Место в рейтинге';
      rankValues = true;
    } else if (chart === 'shifts') {
      title = weighted ? 'Взвешенные смены' : 'Смены';
    } else {
      title = metricLabels[metric];
    }
    series = selected.map((employee) => ({
      name: employee.nickname,
      values: state.analytics.points.map((point) => {
        const row = employeeAt(point, employee.login);
        if (!row) return null;
        if (chart === 'kpi') return row[weighted ? 'weighted_kpi' : 'kpi'];
        if (chart === 'rank') return row.rank;
        if (chart === 'shifts') return row[weighted ? 'weighted_shifts' : 'shifts'];
        return row[metric];
      }),
      flags: state.analytics.points.map((point) => {
        if (state.analytics.mode !== 'monthly') return null;
        const row = employeeAt(point, employee.login);
        return row ? { penalties: row.penalties, stream: row.stream } : null;
      }),
    }));
  }

  $('#chartTitle').textContent = title;
  const latestValues = series
    .map((item) => [...item.values].reverse().find((value) => value != null))
    .filter((value) => value != null);
  $('#chartCurrent').textContent = series.length === 1 && latestValues.length
    ? (percentValues ? percent(latestValues[0]) : number(latestValues[0]))
    : '';
  $('#analyticsChartArea').innerHTML = customChart || lineChart(series, labels, {
    percent: percentValues,
    rank: rankValues,
    title,
  });
  renderChartLegend(
    series,
    scope === 'individual' && state.analytics.mode === 'monthly',
  );
  renderAnalyticsSummary(
    scope,
    state.analytics.points.at(-1),
    selected,
  );
}

async function loadAnalytics(force = false) {
  const mode = $('#analyticsMode').value;
  const month = $('#analyticsMonth').value || state.month;
  const selectedDate = mode === 'daily' && month === state.month ? state.day : '';
  const cacheKey = `${mode}:${month}:${selectedDate}`;
  $('#analyticsChartArea').innerHTML = '<div class="loading-card chart-loading"></div>';
  try {
    if (!force && analyticsCache.has(cacheKey)) {
      state.analytics = analyticsCache.get(cacheKey);
    } else {
      const params = new URLSearchParams({ mode, month });
      if (selectedDate) params.set('date', selectedDate);
      state.analytics = await api(`/api/kpi/analytics?${params}`);
      analyticsCache.set(cacheKey, state.analytics);
    }
    const available = new Set(state.analytics.employees.map((employee) => employee.login));
    state.analyticsEmployees = new Set(
      [...state.analyticsEmployees].filter((login) => available.has(login)),
    );
    renderEmployeeChips();
    renderAnalytics();
  } catch (error) {
    $('#analyticsChartArea').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

function renderSettings() {
  if (!state.settings) return;
  $('#settingsMetrics').innerHTML = state.settings.metrics.map((item) => {
    const initiative = item.kind === 'initiative_bonus';
    const value = initiative ? item.value * 100 : item.value;
    return `
      <label class="setting-field">
        <span>${escapeHtml(item.metric)}</span>
        <div class="setting-input">
          <input
            type="number"
            min="0.001"
            step="0.001"
            value="${value}"
            data-setting-metric="${escapeHtml(item.metric)}"
            data-kind="${escapeHtml(item.kind)}"
            required
          >
          <b>${initiative ? '%' : '×'}</b>
        </div>
        <small>${escapeHtml(settingsMetricUnits[item.metric] || '')}</small>
      </label>
    `;
  }).join('');

  $('#settingsClubs').innerHTML = state.settings.clubs.map((item) => `
    <article class="club-setting" data-setting-club="${escapeHtml(item.club)}">
      <strong>${escapeHtml(item.club)}</strong>
      <label>
        <span>Будни</span>
        <input type="number" min="0" step="0.01" value="${item.weekday_weight}" data-weight="weekday_weight" required>
      </label>
      <label>
        <span>Выходные</span>
        <input type="number" min="0" step="0.01" value="${item.weekend_weight}" data-weight="weekend_weight" required>
      </label>
    </article>
  `).join('');
}

async function loadSettings() {
  const month = $('#settingsMonth').value || state.month;
  $('#settingsMetrics').innerHTML = '<div class="loading-card"></div>';
  $('#settingsClubs').innerHTML = '<div class="loading-card"></div>';
  try {
    state.settings = await api(
      `/api/kpi/settings?month=${encodeURIComponent(month)}`,
    );
    renderSettings();
  } catch (error) {
    $('#settingsMetrics').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#settingsClubs').innerHTML = '';
    showToast(error.message, true);
  }
}

async function initialize() {
  $('#datePicker').value = state.day;
  $('#dateDisplay').textContent = numericDayLabel(state.day);
  $('#analyticsMonth').value = state.month;
  updateChartOptions();
  try {
    state.me = await api('/api/me');
    $('#kpiUserName').textContent = `${state.me.name} · Команда OMG VR`;
    $('#userBadge').textContent = state.me.role_name;
    $('#userBadge').classList.remove('skeleton');
    if (state.me.can_edit_settings) {
      $('#settingsTab').hidden = false;
      document.querySelector('.view-tabs').classList.add('owner-tabs');
      $('#settingsMonth').value = state.month;
    }
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
  $('#dateDisplay').textContent = numericDayLabel(state.day);
  $('#analyticsMonth').value = state.month;
  state.analytics = null;
  loadData();
}

$('#previousDay').addEventListener('click', () => moveDay(-1));
$('#nextDay').addEventListener('click', () => moveDay(1));
$('#datePicker').addEventListener('change', (event) => {
  if (!event.target.value) return;
  state.day = event.target.value;
  state.month = state.day.slice(0, 7);
  $('#dateDisplay').textContent = numericDayLabel(state.day);
  $('#analyticsMonth').value = state.month;
  state.analytics = null;
  loadData();
});
$('#searchInput').addEventListener('input', renderEmployees);

$('#managerFilters').addEventListener('change', (event) => {
  const mapping = {
    clubFilter: 'club',
    roleFilter: 'role',
    zoneFilter: 'zone',
    stateFilter: 'condition',
  };
  const key = mapping[event.target.id];
  if (!key) return;
  state.filters[key] = event.target.value;
  renderEmployees();
});

$('#attentionToggle').addEventListener('click', () => {
  state.filters.attention = !state.filters.attention;
  renderManagerFilters();
  renderEmployees();
});

$('#resetFilters').addEventListener('click', () => {
  state.filters = {
    club: '',
    role: '',
    zone: '',
    condition: '',
    attention: false,
  };
  $('#searchInput').value = '';
  renderManagerFilters();
  renderEmployees();
});

document.querySelector('.view-tabs').addEventListener('click', async (event) => {
  const tab = event.target.closest('[data-view]');
  if (!tab) return;
  document.querySelectorAll('.view-tab').forEach(
    (button) => button.classList.toggle('active', button === tab),
  );
  const view = tab.dataset.view;
  const analytics = view === 'analytics';
  const settings = view === 'settings';
  $('#myView').hidden = view !== 'my';
  $('#ratingView').hidden = view !== 'rating';
  $('#analyticsView').hidden = !analytics;
  $('#settingsView').hidden = !settings;
  document.querySelector('.period-panel').hidden = analytics || settings;
  if (analytics && !state.analytics) await loadAnalytics();
  if (settings && !state.settings) await loadSettings();
});

$('#myKpi').addEventListener('click', async (event) => {
  if (event.target.closest('.open-owner-settings')) {
    document.querySelector('[data-view="settings"]')?.click();
    return;
  }
  if (!state.myKpi) return;
  const metricButton = event.target.closest('[data-metric]');
  if (metricButton) {
    state.selected = state.myKpi;
    await showMetricEntries(metricButton.dataset.metric);
    return;
  }
  if (event.target.closest('.open-my-detail')) renderDialog(state.myKpi);
});

$('#freshness').addEventListener('click', (event) => {
  if (event.target.closest('#refreshKpi')) loadData();
});

$('#analyticsMode').addEventListener('change', () => loadAnalytics());
$('#analyticsMonth').addEventListener('change', (event) => {
  if (event.target.value) loadAnalytics();
});
$('#analyticsScope').addEventListener('change', updateChartOptions);
$('#analyticsChart').addEventListener('change', renderAnalytics);
$('#analyticsMetric').addEventListener('change', renderAnalytics);
$('#analyticsWeighted').addEventListener('change', renderAnalytics);
$('#settingsMonth').addEventListener('change', (event) => {
  if (!event.target.value) return;
  state.settings = null;
  loadSettings();
});
$('#settingsForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const metrics = {};
  document.querySelectorAll('[data-setting-metric]').forEach((input) => {
    const rawValue = input.valueAsNumber;
    metrics[input.dataset.settingMetric] = (
      input.dataset.kind === 'initiative_bonus'
        ? rawValue / 100
        : rawValue
    );
  });
  const clubs = {};
  document.querySelectorAll('[data-setting-club]').forEach((row) => {
    clubs[row.dataset.settingClub] = {
      weekday_weight: row.querySelector('[data-weight="weekday_weight"]').valueAsNumber,
      weekend_weight: row.querySelector('[data-weight="weekend_weight"]').valueAsNumber,
    };
  });
  const saveButton = $('#saveSettings');
  saveButton.disabled = true;
  saveButton.textContent = 'Сохраняю…';
  try {
    state.settings = await api('/api/kpi/settings', {
      method: 'PUT',
      body: JSON.stringify({
        month: $('#settingsMonth').value,
        metrics,
        clubs,
      }),
    });
    analyticsCache.clear();
    state.analytics = null;
    renderSettings();
    showToast(`Настройки действуют с ${monthLabel($('#settingsMonth').value)}`);
    await loadData();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = 'Сохранить настройки';
  }
});
$('#employeeChips').addEventListener('click', (event) => {
  const chip = event.target.closest('[data-login]');
  if (!chip) return;
  const login = chip.dataset.login;
  if (state.analyticsEmployees.has(login)) {
    if (state.analyticsEmployees.size === 1) {
      showToast('Оставьте хотя бы одного сотрудника', true);
      return;
    }
    state.analyticsEmployees.delete(login);
  } else if (state.analyticsEmployees.size >= 5) {
    showToast('Можно сравнить не больше пяти сотрудников', true);
    return;
  } else {
    state.analyticsEmployees.add(login);
  }
  renderEmployeeChips();
  renderAnalytics();
});

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
  const hashtagButton = event.target.closest('[data-hashtag]');
  if (hashtagButton) {
    await showMetricEntries(`hashtag:${hashtagButton.dataset.hashtag}`);
    return;
  }
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
      analyticsCache.clear();
      state.analytics = null;
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
      analyticsCache.clear();
      state.analytics = null;
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
    analyticsCache.clear();
    state.analytics = null;
    await loadData();
  } catch (error) {
    showToast(error.message, true);
  }
});

$('#monthStatusButton').addEventListener('click', async () => {
  const closed = Boolean(state.monthStatus?.is_closed);
  if (!closed) {
    await openMonthCloseCheck();
    return;
  }
  if (!window.confirm(`Переоткрыть ${monthLabel(state.month)}?`)) return;
  try {
    state.monthStatus = await api('/api/month-status', {
      method: 'POST',
      body: JSON.stringify({ month: state.month, is_closed: false }),
    });
    renderStatus();
    analyticsCache.clear();
    state.analytics = null;
    showToast('Месяц снова открыт');
  } catch (error) {
    showToast(error.message, true);
  }
});

$('#confirmMonthClose').addEventListener('click', async () => {
  const button = $('#confirmMonthClose');
  button.disabled = true;
  try {
    state.monthStatus = await api('/api/month-status', {
      method: 'POST',
      body: JSON.stringify({ month: state.month, is_closed: true }),
    });
    monthCloseDialog.close();
    renderStatus();
    analyticsCache.clear();
    state.analytics = null;
    showToast('Месяц закрыт, snapshot сохранён');
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
});

initialize();
