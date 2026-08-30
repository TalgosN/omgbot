const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const state = { data: null, view: 'records' };

tg?.ready();
tg?.expand();
if (tg) {
  tg.setHeaderColor('#120b20');
  tg.setBackgroundColor('#0e0818');
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

async function api(path) {
  const response = await fetch(path, {
    headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Ошибка сервера');
  return payload;
}

function holderLabel(record) {
  const holders = record.holders || [];
  if (!holders.length) return '—';
  const first = holders[0];
  return `${escapeHtml(first.name)}${holders.length > 1 ? ` <span>и ещё ${holders.length - 1}</span>` : ''}`;
}

function recordCard(record) {
  const holderLogins = record.holders.map((holder) => escapeHtml(holder.login)).join(' · ');
  return `<article class="record-card">
    <small>${escapeHtml(record.title)}</small>
    <b>${escapeHtml(record.value_label)}</b>
    <div class="record-holder"><strong>${holderLabel(record)}</strong><span>${record.context ? `${escapeHtml(record.context)} · ` : ''}${holderLogins}</span></div>
  </article>`;
}

function renderRecords() {
  const records = state.data.records || [];
  $('#recordGrid').innerHTML = records.length
    ? records.map(recordCard).join('')
    : '<div class="records-empty">Для рекордов пока не хватает данных</div>';
  const archive = state.data.archive_records || [];
  $('#archiveSection').hidden = !archive.length;
  $('#archiveCount').textContent = archive.length;
  $('#archiveRecordGrid').innerHTML = archive.map(recordCard).join('');
}

function achievementCard(item) {
  const tierClass = item.tier?.key || 'locked';
  const tierLabel = item.tier?.label || 'Не открыто';
  const medal = item.tier?.icon || '◇';
  const progressText = item.next_label
    ? `${escapeHtml(item.value_label)} / ${escapeHtml(item.next_label)}`
    : `${escapeHtml(item.value_label)} · максимум`;
  return `<article class="achievement-card ${tierClass}">
    <div class="achievement-medal">${medal}</div>
    <div class="achievement-main">
      <div class="achievement-head"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(tierLabel)}</span></div>
      <p>${escapeHtml(item.description)}</p>
      <div class="achievement-progress-head"><span>Прогресс</span><b>${progressText}</b></div>
      <div class="achievement-track"><i style="width:${Math.round(item.progress * 100)}%"></i></div>
      <div class="achievement-thresholds">${item.thresholds.map((threshold) => `
        <span class="${item.level >= threshold.level ? 'earned' : ''}">${threshold.icon} ${escapeHtml(threshold.value_label)}</span>
      `).join('')}</div>
    </div>
  </article>`;
}

function renderAchievements() {
  $('#achievementCategories').innerHTML = state.data.categories.map((category) => `
    <section class="achievement-category">
      <h3>${escapeHtml(category.title)}</h3>
      <div class="achievement-grid">${category.achievements.map(achievementCard).join('')}</div>
    </section>
  `).join('');
  const earned = state.data.categories.flatMap((category) => category.achievements).filter((item) => item.level > 0);
  const ownShelf = state.data.user.login === state.data.viewer?.login;
  $('#mineTab').textContent = ownShelf ? 'Мои' : 'Полка';
  $('#mineEyebrow').textContent = ownShelf ? 'Уже получено' : state.data.user.name;
  $('#mineTitle').textContent = ownShelf ? 'Моя полка' : 'Достижения сотрудника';
  $('#mineCount').textContent = `${earned.length} получено`;
  $('#mineAchievements').innerHTML = earned.length
    ? earned.sort((left, right) => right.level - left.level).map(achievementCard).join('')
    : '<div class="records-empty">Первая ачивка уже близко</div>';
}

function teamMemberCard(member, rank = null) {
  const selected = member.login === state.data.user.login;
  const progress = member.total ? Math.round((member.score / (member.total * 4)) * 100) : 0;
  return `<button class="team-member ${selected ? 'selected' : ''}" type="button" data-login="${escapeHtml(member.login)}">
    <span class="team-member-name"><strong>${rank ? `<em>#${rank}</em>` : ''}${escapeHtml(member.name)}</strong><span>${escapeHtml(member.login)}</span></span>
    <span class="team-member-total"><b>${member.earned}/${member.total}</b><span>ачивок</span></span>
    <span class="team-progress"><i style="width:${progress}%"></i></span>
    <span class="team-medals"><span>🥉 ${member.bronze}</span><span>🥈 ${member.silver}</span><span>🥇 ${member.gold}</span><span>💎 ${member.diamond}</span></span>
  </button>`;
}

function teamComparator(sort) {
  const fields = sort === 'diamond'
    ? ['diamond', 'gold', 'score']
    : sort === 'gold'
      ? ['gold', 'diamond', 'score']
      : ['score', 'diamond', 'gold'];
  return (left, right) => {
    for (const field of fields) {
      if (right[field] !== left[field]) return right[field] - left[field];
    }
    return left.name.localeCompare(right.name, 'ru');
  };
}

function renderTeam() {
  const canManage = Boolean(state.data.can_manage);
  $('#teamTab').hidden = !canManage;
  $('#recordsTabs').classList.toggle('has-team', canManage);
  if (!canManage) return;

  const members = state.data.team || [];
  const active = members.filter((member) => member.active);
  $('#teamSummary').innerHTML = `
    <div><span>В команде</span><b>${active.length}</b></div>
    <div><span>Золотых уровней</span><b>${active.reduce((sum, member) => sum + member.gold, 0)}</b></div>
    <div><span>Алмазных уровней</span><b>${active.reduce((sum, member) => sum + member.diamond, 0)}</b></div>`;

  const query = $('#teamSearch').value.trim().toLocaleLowerCase('ru');
  const comparator = teamComparator($('#teamSort').value);
  const visible = members.filter((member) => (
    !query || `${member.name} ${member.login}`.toLocaleLowerCase('ru').includes(query)
  ));
  const activeVisible = visible.filter((member) => member.active).sort(comparator);
  const archiveVisible = visible.filter((member) => !member.active).sort(comparator);
  $('#teamList').innerHTML = activeVisible.length
    ? activeVisible.map((member, index) => teamMemberCard(member, index + 1)).join('')
    : '<div class="records-empty">Сотрудники не найдены</div>';
  $('#teamArchive').hidden = !archiveVisible.length;
  $('#teamArchiveCount').textContent = archiveVisible.length;
  $('#teamArchiveList').innerHTML = archiveVisible.map((member) => teamMemberCard(member)).join('');
}

function renderSummary() {
  const { summary, user } = state.data;
  $('#recordsUserName').textContent = `Команда OMG VR · ${user.name}`;
  $('#recordsUserBadge').textContent = user.active ? 'В команде' : 'Архив';
  $('#earnedCount').textContent = summary.earned;
  $('#achievementCount').textContent = summary.total;
  $('#medalSummary').innerHTML = `
    <div><span>🥉</span><b>${summary.bronze}</b></div>
    <div><span>🥈</span><b>${summary.silver}</b></div>
    <div><span>🥇</span><b>${summary.gold}</b></div>
    <div><span>💎</span><b>${summary.diamond}</b></div>`;
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll('#recordsTabs button').forEach((button) => {
    button.classList.toggle('active', button.dataset.view === view);
  });
  $('#recordsView').hidden = view !== 'records';
  $('#achievementsView').hidden = view !== 'achievements';
  $('#mineView').hidden = view !== 'mine';
  $('#teamView').hidden = view !== 'team';
}

$('#recordsTabs').addEventListener('click', (event) => {
  const button = event.target.closest('[data-view]');
  if (button) switchView(button.dataset.view);
});

$('#teamSearch').addEventListener('input', renderTeam);
$('#teamSort').addEventListener('change', renderTeam);
$('#teamView').addEventListener('click', async (event) => {
  const member = event.target.closest('[data-login]');
  if (!member) return;
  await loadDashboard(member.dataset.login);
  switchView('mine');
});

function renderDashboard() {
  renderSummary();
  renderRecords();
  renderAchievements();
  renderTeam();
}

async function loadDashboard(login = '') {
  $('#recordsError').hidden = true;
  const query = login ? `?login=${encodeURIComponent(login)}` : '';
  try {
    state.data = await api(`/api/records${query}`);
    renderDashboard();
  } catch (error) {
    $('#recordsError').hidden = false;
    $('#recordsError').textContent = error.message;
  }
}

async function init() {
  await loadDashboard();
}

init();
