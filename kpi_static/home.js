const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
let bookingsLoading = false;
let homeData = null;
tg?.ready();
tg?.expand();
if (tg) {
  tg.setHeaderColor('#e9e3f3');
  tg.setBackgroundColor('#e9e3f3');
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
function timeLabel(value) { return value ? value.slice(11, 16) : '??:??'; }
function guestLabel(value) {
  const count = Number(value || 0);
  const tail = count % 100;
  const last = count % 10;
  const word = tail >= 11 && tail <= 14 ? 'гостей' : last === 1 ? 'гость' : last >= 2 && last <= 4 ? 'гостя' : 'гостей';
  return `${number(count)} ${word}`;
}
function bookingCountLabel(value) {
  const count = Number(value || 0);
  const tail = count % 100;
  const last = count % 10;
  const word = tail >= 11 && tail <= 14 ? 'броней' : last === 1 ? 'бронь' : last >= 2 && last <= 4 ? 'брони' : 'броней';
  return `${count} ${word}`;
}
function bookingIsPast(booking) {
  const boundary = booking.end || booking.start;
  return boundary ? new Date(boundary).getTime() < Date.now() : false;
}
function bookingIsActive(booking) {
  const start = booking.start ? new Date(booking.start).getTime() : NaN;
  const end = booking.end ? new Date(booking.end).getTime() : NaN;
  const now = Date.now();
  return Number.isFinite(start) && Number.isFinite(end) && start <= now && now <= end;
}
function syncTime(value) {
  if (!value) return 'Нет свежих данных';
  return `Обновлено ${new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))}`;
}
async function api(path) {
  const response = await fetch(path, { headers: { 'X-Telegram-Init-Data': tg?.initData || '' } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Ошибка сервера');
  return payload;
}

function bookingRows(bookings, callcenter = false) {
  if (!bookings.length) return '<div class="booking-empty">Броней нет</div>';
  return `<div class="booking-rows">${bookings.map((booking) => `
    <article class="booking-row${bookingIsPast(booking) ? ' past' : ''}">
      <div class="booking-time">${timeLabel(booking.start)} - ${timeLabel(booking.end)}</div>
      <div class="booking-info">
        <strong>${escapeHtml(booking.format || 'Без формата')}</strong>
        ${callcenter ? `<span>${escapeHtml(booking.club || '')} · ${dateLabel(booking.date)}</span>` : ''}
      </div>
      ${callcenter
    ? `<div><div class="booking-guests">👥 ${guestLabel(booking.participants)}</div><a class="booking-order" href="${escapeHtml(booking.url)}" target="_blank" rel="noopener">№${escapeHtml(booking.number)}</a></div>`
    : `<div class="booking-guests">👥 ${guestLabel(booking.participants)}</div>`}
    </article>
  `).join('')}</div>`;
}

function renderBookings(data) {
  const section = $('#bookingsSection');
  if (data.mode === 'management') {
    section.hidden = true;
    renderClubs(homeData, data);
    return;
  }
  if (data.mode === 'clubs' && !data.groups.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  $('#bookingsFreshness').textContent = syncTime(data.last_synced_at);
  $('#bookingsWarning').hidden = !data.stale;
  $('#bookingsWarning').textContent = data.stale
    ? '⚠️ Данные давно не обновлялись. Показано последнее успешное состояние.'
    : '';

  if (data.mode === 'callcenter') {
    $('#bookingsEyebrow').textContent = 'Ближайшие 21 день';
    $('#bookingsTitle').textContent = 'Без предоплаты';
    $('#bookingsList').className = 'booking-list';
    $('#bookingsList').innerHTML = bookingRows(data.bookings, true);
    return;
  }

  $('#bookingsEyebrow').textContent = 'Сегодня';
  $('#bookingsTitle').textContent = 'Брони клуба';
  $('#bookingsList').className = 'booking-list';
  $('#bookingsList').innerHTML = data.groups.map((group) => `
    <details class="booking-group" open>
      <summary>
        <div><strong>${escapeHtml(group.club)}</strong><span>👥 ${guestLabel(group.participants)}</span></div>
        <div class="booking-count">${bookingCountLabel(group.count)}</div>
      </summary>
      ${bookingRows(group.bookings)}
    </details>
  `).join('');
}

async function loadBookings() {
  if (bookingsLoading) return;
  bookingsLoading = true;
  try {
    renderBookings(await api('/api/bookings/today'));
  } catch (error) {
    if (homeData?.management) {
      renderClubs(homeData);
      $('#clubsWarning').hidden = false;
      $('#clubsWarning').textContent = `Не удалось обновить брони: ${error.message}`;
    } else {
      $('#bookingsSection').hidden = false;
      $('#bookingsWarning').hidden = false;
      $('#bookingsWarning').textContent = `Не удалось обновить брони: ${error.message}`;
    }
  } finally {
    bookingsLoading = false;
  }
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
    ? `${number(kpi.shifts)} смен · KPI ${percent(kpi.total_pct)}`
    : 'Открыть доску KPI';
  $('#shiftModuleText').textContent = data.upcoming_shifts.length
    ? `${dateLabel(data.upcoming_shifts[0].date)} · ${data.upcoming_shifts[0].club}`
    : 'Ближайших смен нет';
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
  if (data.role === 3) {
    $('#kpiModuleText').textContent = `Средний KPI ${percent(summary.average_pct)}`;
  }
}

function renderClubs(data, bookingsData = null) {
  if (!data?.clubs.length) return;
  $('#clubsSection').hidden = false;
  $('#clubsFreshness').textContent = bookingsData
    ? syncTime(bookingsData.last_synced_at)
    : 'Брони загружаются…';
  $('#clubsWarning').hidden = !bookingsData?.stale;
  $('#clubsWarning').textContent = bookingsData?.stale
    ? '⚠️ Данные броней давно не обновлялись. Показано последнее успешное состояние.'
    : '';
  const groups = new Map(
    (bookingsData?.groups || []).map((group) => [group.club, group]),
  );
  const clubs = [...data.clubs].sort((left, right) => {
    const statusOrder = Number(left.status === 'Открыт') - Number(right.status === 'Открыт');
    if (statusOrder) return statusOrder;
    const leftProblems = Number(left.problems.work) + Number(left.problems.review);
    const rightProblems = Number(right.problems.work) + Number(right.problems.review);
    return rightProblems - leftProblems || left.club.localeCompare(right.club, 'ru');
  });
  $('#clubList').innerHTML = clubs.map((club) => {
    const opened = club.status === 'Открыт';
    const people = club.on_shift.length ? club.on_shift.join(', ') : 'Никого';
    const group = groups.get(club.club) || {
      count: 0, participants: 0, bookings: [],
    };
    const activeBooking = group.bookings.some(bookingIsActive);
    const bookingStatus = !opened ? '' : bookingsData
      ? `<span class="club-booking-status ${activeBooking ? 'active' : 'empty'}">${activeBooking ? '🔥 Идёт бронь' : '● Броней сейчас нет'}</span>`
      : '<span class="club-booking-status loading">Брони загружаются…</span>';
    return `
      <details class="club-card">
        <summary>
          <div class="club-head">
            <h3>${escapeHtml(club.club)}</h3>
          </div>
          <div class="club-overview">
            <div class="club-shift-summary" title="${escapeHtml(people)}">
              <span>На смене</span>
              <strong>${escapeHtml(people)}</strong>
            </div>
            <div class="club-live-statuses">
              <span class="club-status ${opened ? 'open' : 'closed'}">${opened ? '● Открыт' : '● Закрыт'}</span>
              ${bookingStatus}
            </div>
          </div>
          <div class="club-meta">
            <div><span>Брони</span><strong>${bookingsData ? bookingCountLabel(group.count) : '—'}</strong></div>
            <div><span>Проблемы</span><strong>${club.problems.work} · 👀 ${club.problems.review}</strong></div>
          </div>
          <div class="club-expand">${bookingsData ? (group.count ? 'Показать брони' : 'Броней на сегодня нет') : 'Брони загружаются…'} <span>›</span></div>
        </summary>
        ${bookingsData ? bookingRows(group.bookings) : ''}
      </details>
    `;
  }).join('');
}

async function load() {
  try {
    const [me, data] = await Promise.all([api('/api/me'), api('/api/home')]);
    homeData = data;
    $('#userBadge').textContent = me.role_name;
    $('#welcomeTitle').textContent = `Привет, ${me.name}`;
    $('#welcomeDate').textContent = new Intl.DateTimeFormat('ru-RU', {
      day: 'numeric', month: 'long', year: 'numeric',
    }).format(new Date());
    renderPersonal(data);
    renderManagement(data);
    renderClubs(data);
    await loadBookings();
  } catch (error) {
    $('#homeError').hidden = false;
    $('#homeError').textContent = error.message;
  }
}

load();
setInterval(loadBookings, 60 * 1000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) loadBookings();
});
