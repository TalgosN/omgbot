const tg = window.Telegram?.WebApp;
const shiftActions = document.querySelector('#shiftActions');
const externalLink = document.querySelector('#openExternalShift');
const configLink = document.querySelector('#openShiftConfig');
const reportProblemLink = document.querySelector('#reportProblem');
const errorCard = document.querySelector('#shiftError');
const shiftReportTest = document.querySelector('#shiftReportTest');

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

function renderEmployeeDashboard(dashboard) {
  if (!dashboard) return;
  const shifts = dashboard.upcoming_shifts || [];
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
  const response = await fetch('/api/shift', {
    headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Не удалось открыть OMG Shift');
  document.querySelector('#shiftUserName').textContent = `Команда OMG VR · ${payload.user_name}`;
  document.querySelector('#shiftRole').textContent = payload.role_name;
  shiftReportTest.hidden = !payload.shift_report_available;
  if (!payload.external_url) throw new Error('Адрес OMG Shift не настроен');

  externalLink.href = payload.external_url;
  configLink.hidden = !payload.can_manage;
  renderEmployeeDashboard(payload.employee_dashboard);
  shiftActions.classList.toggle('manager', payload.can_manage);
  shiftActions.hidden = false;
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

loadShift().catch((error) => {
  document.querySelector('#shiftRole').textContent = 'OMG VR';
  errorCard.textContent = error.message;
  errorCard.hidden = false;
});
