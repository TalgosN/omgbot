const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
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

function percent(value) { return `${Math.round(Number(value || 0) * 100)}%`; }
function number(value) { return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Number(value || 0)); }
function dateLabel(value) {
  const [year, month, day] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'short' })
    .format(new Date(year, month - 1, day));
}
async function api(path) {
  const response = await fetch(path, { headers: { 'X-Telegram-Init-Data': tg?.initData || '' } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Ошибка сервера');
  return payload;
}

function renderPersonal(data) {
  if (data.role === 3) return;
  $('#personalSection').hidden = false;
  const kpi = data.personal_kpi;
  $('#personalSummary').innerHTML = `
    <div class="summary-tile"><span>KPI за месяц</span><strong>${kpi ? percent(kpi.total_pct) : '—'}</strong></div>
    <div class="summary-tile"><span>Смен за месяц</span><strong>${kpi ? number(kpi.shifts) : '0'}</strong></div>
  `;
  $('#kpiModuleText').textContent = kpi
    ? `${number(kpi.shifts)} смен · ${percent(kpi.total_pct)}`
    : 'Открыть доску KPI';
  $('#shiftList').innerHTML = data.upcoming_shifts.length
    ? data.upcoming_shifts.map((shift) => `
      <article class="shift-card">
        <div><strong>${escapeHtml(shift.club)}</strong><span>${dateLabel(shift.date)}</span></div>
        <small>${number(shift.duration)} ч</small>
      </article>
    `).join('')
    : '<div class="empty-card">Ближайших смен в расписании нет</div>';
}

function renderManagement(data) {
  const summary = data.management;
  if (!summary) return;
  $('#managementSection').hidden = false;
  $('#managementSummary').innerHTML = `
    <div class="summary-tile"><span>Средний KPI</span><strong>${percent(summary.average_pct)}</strong></div>
    <div class="summary-tile"><span>В рейтинге</span><strong>${summary.participants}</strong></div>
    <div class="summary-tile"><span>Красная зона</span><strong>${summary.red_zone}</strong></div>
    <div class="summary-tile"><span>Проблемы</span><strong>${summary.problems.work}</strong><small>в работе</small></div>
    <div class="summary-tile"><span>Проверка</span><strong>${summary.problems.review}</strong><small>ждут решения</small></div>
    <div class="summary-tile"><span>Штрафы</span><strong>${summary.active_penalties}</strong></div>
  `;
  $('#problemModuleText').textContent = `${summary.problems.work} в работе · ${summary.problems.review} на проверке`;
}

function renderClubs(data) {
  if (!data.clubs.length) return;
  $('#clubsSection').hidden = false;
  $('#clubList').innerHTML = data.clubs.map((club) => {
    const opened = club.status === 'Открыт';
    const people = club.on_shift.length ? club.on_shift.join(', ') : 'Никого';
    return `
      <article class="club-card">
        <div class="club-head">
          <h3>${escapeHtml(club.club)}</h3>
          <span class="club-status ${opened ? 'open' : 'closed'}">${opened ? '● Открыт' : '● Закрыт'}</span>
        </div>
        <div class="club-meta">
          <div title="${escapeHtml(people)}"><span>Сегодня на смене</span><strong>${escapeHtml(people)}</strong></div>
          <div><span>Проблемы</span><strong>${club.problems.work} · 👀 ${club.problems.review}</strong></div>
          <div><span>Красная зона</span><strong>${club.red_zone}</strong></div>
        </div>
      </article>
    `;
  }).join('');
}

async function load() {
  try {
    const [me, data] = await Promise.all([api('/api/me'), api('/api/home')]);
    $('#userBadge').textContent = me.role_name;
    $('#welcomeTitle').textContent = `Привет, ${me.name}`;
    $('#welcomeDate').textContent = new Intl.DateTimeFormat('ru-RU', {
      day: 'numeric', month: 'long', year: 'numeric',
    }).format(new Date());
    renderPersonal(data);
    renderManagement(data);
    renderClubs(data);
  } catch (error) {
    $('#homeError').hidden = false;
    $('#homeError').textContent = error.message;
  }
}

load();
