const tg = window.Telegram?.WebApp;
const dialog = document.querySelector('#taskDialog');
const state = { scope: 'active', club: '', tasks: [], attachments: [], reportUrls: [], reportRequest: 0, current: null };

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#0d0913');
tg?.setBackgroundColor('#0d0913');
tg?.BackButton?.show();
tg?.BackButton?.onClick(() => window.location.assign('/shift'));

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

function shortDate(value) {
  if (!value) return '—';
  const [year, month, day] = String(value).slice(0, 10).split('-');
  return `${day}.${month}.${year}`;
}

function clock(value) {
  return String(value || '').slice(11, 16) || '—';
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), 'X-Telegram-Init-Data': tg?.initData || '' };
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Не удалось выполнить действие');
  return payload;
}

async function authenticatedBlob(path) {
  const response = await fetch(path, {
    headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || 'Не удалось загрузить вложение');
  }
  return response.blob();
}

function status(task) {
  if (task.status === 'completed') return { label: 'Выполнена', className: 'completed' };
  if (task.status === 'skipped') return { label: 'Пропущена', className: 'skipped' };
  if (task.overdue) return { label: 'Просрочена', className: 'overdue' };
  if (task.status === 'in_progress') return { label: 'В работе', className: 'progress' };
  return { label: 'Новая', className: 'pending' };
}

function renderSummary(summary) {
  document.querySelector('#activeCount').textContent = summary.active || 0;
  document.querySelector('#overdueCount').textContent = summary.overdue || 0;
  document.querySelector('#completedCount').textContent = summary.completed || 0;
  document.querySelector('#skippedCount').textContent = summary.skipped || 0;
}

function renderClubFilters(clubs, canManage) {
  const panel = document.querySelector('#clubFilters');
  panel.hidden = !canManage || clubs.length < 2;
  if (panel.hidden) return;
  panel.innerHTML = ['Все', ...clubs].map((club) => `
    <button type="button" class="${(!state.club && club === 'Все') || state.club === club ? 'active' : ''}" data-club="${club === 'Все' ? '' : escapeHtml(club)}">${escapeHtml(club)}</button>
  `).join('');
}

function renderTasks() {
  const visible = state.club
    ? state.tasks.filter((task) => task.club === state.club)
    : state.tasks;
  const groups = new Map();
  visible.forEach((task) => {
    const key = state.scope === 'history'
      ? `${task.date}:${task.template_id}`
      : String(task.template_id);
    if (!groups.has(key)) groups.set(key, { key, tasks: [], title: task.title, date: task.date });
    groups.get(key).tasks.push(task);
  });
  document.querySelector('#taskList').innerHTML = visible.length
    ? [...groups.values()].map((group) => {
      const completed = group.tasks.filter((task) => task.status === 'completed');
      const skipped = group.tasks.filter((task) => task.status === 'skipped');
      const overdue = group.tasks.filter((task) => task.overdue);
      const performers = [...new Set(completed.map((task) => task.completed_by_name || task.completed_by_login).filter(Boolean))];
      const groupClass = completed.length === group.tasks.length
        ? 'completed'
        : overdue.length
          ? 'overdue'
          : skipped.length === group.tasks.length
            ? 'skipped'
            : 'progress';
      const groupLabel = completed.length === group.tasks.length
        ? 'Всё готово'
        : `${completed.length}/${group.tasks.length} готово`;
      const performerLine = performers.length
        ? `Выполнили: ${performers.join(', ')}`
        : 'Пока никто не завершил';
      const rows = group.tasks.map((task) => {
        const itemStatus = status(task);
        const actor = task.completed_by_name || task.skipped_by_name || task.started_by_login;
        return `<button class="task-club-row ${itemStatus.className}" type="button" data-task="${task.id}">
          <span><strong>${escapeHtml(task.club)}</strong><small>${itemStatus.label}${actor ? ` · ${escapeHtml(actor)}` : ''}</small></span>
          <b>${task.status === 'completed' ? '✓' : task.status === 'skipped' ? '—' : clock(task.due_at)}</b>
        </button>`;
      }).join('');
      const directTask = group.tasks.length === 1 ? group.tasks[0] : null;
      return `<article class="task-group ${groupClass}">
        <button class="task-group-toggle" type="button" data-group="${escapeHtml(group.key)}" data-direct-task="${directTask?.id || ''}" aria-expanded="false">
          <span class="task-group-icon">${completed.length === group.tasks.length ? '✓' : completed.length}</span>
          <span class="task-group-copy">
            <small>${state.scope === 'history' ? shortDate(group.date) : `${group.tasks.length} клуб.`}</small>
            <strong>${escapeHtml(group.title)}</strong>
            <em>${escapeHtml(performerLine)}</em>
          </span>
          <span class="task-group-progress"><b>${directTask ? status(directTask).label : `${groupLabel} · открыть`}</b><i>${directTask ? '→' : '⌄'}</i></span>
        </button>
        <div class="task-group-details" hidden>${rows}</div>
      </article>`;
    }).join('')
    : `<div class="task-empty">${state.scope === 'active' ? 'На сегодня активных задач нет. Всё готово ✓' : 'За последние 30 дней задач не найдено.'}</div>`;
}

async function loadTasks(scope = state.scope) {
  state.scope = scope;
  const notice = document.querySelector('#taskNotice');
  notice.hidden = true;
  document.querySelector('#taskList').innerHTML = '<div class="task-empty">Загружаю задачи…</div>';
  try {
    const payload = await api(`/api/shift/tasks?scope=${scope}`);
    state.tasks = payload.tasks || [];
    document.querySelector('#taskRole').textContent = payload.can_manage ? 'Контроль выполнения' : 'Моя смена';
    renderSummary(payload.summary || {});
    renderClubFilters(payload.clubs || [], payload.can_manage);
    renderTasks();
    return payload;
  } catch (error) {
    notice.textContent = error.message;
    notice.hidden = false;
    document.querySelector('#taskList').innerHTML = '';
    return null;
  }
}

function clearAttachments() {
  state.attachments.forEach((item) => URL.revokeObjectURL(item.url));
  state.attachments = [];
  renderAttachments();
}

function clearReportMedia() {
  state.reportRequest += 1;
  state.reportUrls.forEach((url) => URL.revokeObjectURL(url));
  state.reportUrls = [];
  document.querySelector('#taskReportMedia').innerHTML = '';
  document.querySelector('#taskReportGallery').hidden = true;
}

async function loadTaskReport(task) {
  const requestId = ++state.reportRequest;
  const gallery = document.querySelector('#taskReportGallery');
  const mediaPanel = document.querySelector('#taskReportMedia');
  const telegramLink = document.querySelector('#taskReportTelegram');
  gallery.hidden = false;
  mediaPanel.innerHTML = '<div class="task-report-loading">Загружаю фотографии…</div>';
  telegramLink.hidden = true;
  const report = await api(`/api/shift/tasks/${task.id}/report`);
  if (requestId !== state.reportRequest || state.current?.id !== task.id) return;
  document.querySelector('#taskReportMediaCount').textContent = report.media.length
    ? `${report.media.length} влож.`
    : '';
  if (report.report_url) {
    telegramLink.href = report.report_url;
    telegramLink.hidden = false;
  }
  if (!report.media.length) {
    mediaPanel.innerHTML = '<div class="task-report-loading">Вложений в приложении нет</div>';
    return;
  }
  const loaded = await Promise.all(report.media.map(async (media) => ({
    media,
    url: URL.createObjectURL(await authenticatedBlob(media.url)),
  })));
  if (requestId !== state.reportRequest || state.current?.id !== task.id) {
    loaded.forEach((item) => URL.revokeObjectURL(item.url));
    return;
  }
  state.reportUrls = loaded.map((item) => item.url);
  mediaPanel.innerHTML = loaded.map(({ media, url }, index) => {
    const label = report.requirements?.[index] || `Вложение ${index + 1}`;
    const preview = media.media_type === 'video'
      ? `<video src="${url}" controls playsinline preload="metadata" aria-label="Видео ${index + 1}"></video>`
      : `<a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="Фото ${index + 1}"></a>`;
    return `<figure>${preview}<figcaption>${escapeHtml(label)}</figcaption></figure>`;
  }).join('');
}

function renderAttachments() {
  const task = state.current;
  if (!task) return;
  const required = Number(task.required_attachments || 1);
  document.querySelector('#attachmentCounter').textContent = `${state.attachments.length} из ${required}`;
  document.querySelector('#attachmentList').innerHTML = state.attachments.map((item, index) => {
    const requirement = task.requirements?.[index] || `Вложение ${index + 1}`;
    const preview = item.file.type.startsWith('video/')
      ? `<video src="${item.url}" muted playsinline></video>`
      : `<img src="${item.url}" alt="">`;
    return `<div class="attachment-row">
      <div class="attachment-preview">${preview}</div>
      <span><strong>${escapeHtml(requirement)}</strong><small>${escapeHtml(item.file.name)} · ${(item.file.size / 1048576).toFixed(1)} МБ</small></span>
      <button type="button" data-remove="${index}" aria-label="Удалить">×</button>
    </div>`;
  }).join('');
  document.querySelector('#completeTask').disabled = state.attachments.length < required;
}

function addFiles(files) {
  const allowed = new Set(['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/quicktime', 'video/webm']);
  const errorBox = document.querySelector('#dialogError');
  errorBox.hidden = true;
  for (const file of files) {
    if (state.attachments.length >= 10) break;
    const maximum = file.type.startsWith('video/') ? 20 : 6;
    if (!allowed.has(file.type) || file.size > maximum * 1048576) {
      errorBox.textContent = `${file.name}: неподдерживаемый формат или размер больше ${maximum} МБ.`;
      errorBox.hidden = false;
      continue;
    }
    state.attachments.push({ file, url: URL.createObjectURL(file) });
  }
  renderAttachments();
}

function openTask(task) {
  if (state.current?.id !== task.id) clearAttachments();
  clearReportMedia();
  state.current = task;
  const itemStatus = status(task);
  document.querySelector('#dialogStatus').textContent = itemStatus.label;
  document.querySelector('#dialogStatus').className = `status-pill ${itemStatus.className}`;
  document.querySelector('#dialogClub').textContent = task.club;
  document.querySelector('#dialogTitle').textContent = task.title;
  document.querySelector('#dialogMeta').textContent = `${shortDate(task.date)} · ${clock(task.available_at)}–${clock(task.due_at)}`;
  document.querySelector('#dialogInstructions').textContent = task.instructions || 'Выполните задачу и приложите отчёт.';
  document.querySelector('#dialogRequirements').innerHTML = (task.requirements?.length
    ? task.requirements
    : ['Фото или видео результата']).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  const finished = document.querySelector('#finishedInfo');
  const startButton = document.querySelector('#startTask');
  const editor = document.querySelector('#reportEditor');
  document.querySelector('#skipForm').hidden = true;
  document.querySelector('#dialogError').hidden = true;
  if (task.status === 'completed') {
    finished.textContent = `Выполнил: ${task.completed_by_name || task.completed_by_login || 'сотрудник'} · ${shortDate(task.completed_at)} ${clock(task.completed_at)}`;
    finished.hidden = false;
    startButton.hidden = true;
    editor.hidden = true;
    loadTaskReport(task).catch((error) => {
      document.querySelector('#taskReportMedia').innerHTML = `<div class="task-report-loading error">${escapeHtml(error.message)}</div>`;
    });
  } else if (task.status === 'skipped') {
    finished.textContent = `Пропустил: ${task.skipped_by_name || task.skipped_by_login || 'сотрудник'}. Причина: ${task.skip_reason || 'не указана'}`;
    finished.hidden = false;
    startButton.hidden = true;
    editor.hidden = true;
  } else if (!task.can_execute) {
    finished.textContent = task.overdue
      ? 'Задача просрочена. Выполнение доступно сотруднику клуба.'
      : 'Выполнение доступно сотруднику клуба. Здесь открыт режим контроля.';
    finished.hidden = false;
    startButton.hidden = true;
    editor.hidden = true;
  } else {
    finished.hidden = true;
    startButton.hidden = task.status === 'in_progress';
    editor.hidden = task.status !== 'in_progress';
    renderAttachments();
  }
  dialog.showModal();
}

async function startCurrentTask() {
  const button = document.querySelector('#startTask');
  button.disabled = true;
  try {
    state.current = await api(`/api/shift/tasks/${state.current.id}/start`, { method: 'POST' });
    button.hidden = true;
    document.querySelector('#reportEditor').hidden = false;
    renderAttachments();
  } catch (error) {
    document.querySelector('#dialogError').textContent = error.message;
    document.querySelector('#dialogError').hidden = false;
  } finally {
    button.disabled = false;
  }
}

function uploadTask(form, button) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/shift/tasks/${state.current.id}/complete`);
    xhr.setRequestHeader('X-Telegram-Init-Data', tg?.initData || '');
    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable) button.textContent = `Отправляем · ${Math.round(event.loaded / event.total * 100)}%`;
    });
    xhr.addEventListener('load', () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText || '{}'); } catch (_) { /* noop */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
      else reject(new Error(payload.error || 'Не удалось отправить отчёт'));
    });
    xhr.addEventListener('error', () => reject(new Error('Соединение прервалось во время отправки')));
    xhr.send(form);
  });
}

async function completeCurrentTask() {
  const button = document.querySelector('#completeTask');
  const form = new FormData();
  state.attachments.forEach((item) => form.append('media', item.file, item.file.name));
  button.disabled = true;
  const original = button.textContent;
  try {
    await uploadTask(form, button);
    clearAttachments();
    dialog.close();
    await loadTasks(state.scope);
    tg?.HapticFeedback?.notificationOccurred('success');
  } catch (error) {
    document.querySelector('#dialogError').textContent = error.message;
    document.querySelector('#dialogError').hidden = false;
  } finally {
    button.textContent = original;
    button.disabled = false;
  }
}

async function skipCurrentTask() {
  const reason = document.querySelector('#skipReason').value.trim();
  const button = document.querySelector('#skipTask');
  button.disabled = true;
  try {
    await api(`/api/shift/tasks/${state.current.id}/skip`, {
      method: 'POST', body: JSON.stringify({ reason }),
    });
    clearAttachments();
    dialog.close();
    await loadTasks(state.scope);
  } catch (error) {
    document.querySelector('#dialogError').textContent = error.message;
    document.querySelector('#dialogError').hidden = false;
  } finally {
    button.disabled = false;
  }
}

document.querySelector('.task-tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-scope]');
  if (!button) return;
  document.querySelectorAll('.task-tabs button').forEach((item) => item.classList.toggle('active', item === button));
  state.club = '';
  loadTasks(button.dataset.scope);
});
document.querySelector('#clubFilters').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-club]');
  if (!button) return;
  state.club = button.dataset.club;
  document.querySelectorAll('#clubFilters button').forEach((item) => item.classList.toggle('active', item === button));
  renderTasks();
});
document.querySelector('#taskList').addEventListener('click', (event) => {
  const toggle = event.target.closest('[data-group]');
  if (toggle) {
    const directTaskId = Number(toggle.dataset.directTask || 0);
    if (directTaskId) {
      const task = state.tasks.find((item) => item.id === directTaskId);
      if (task) openTask(task);
      return;
    }
    const group = toggle.closest('.task-group');
    const details = group.querySelector('.task-group-details');
    const expanded = toggle.getAttribute('aria-expanded') === 'true';
    toggle.setAttribute('aria-expanded', String(!expanded));
    group.classList.toggle('expanded', !expanded);
    details.hidden = expanded;
    return;
  }
  const card = event.target.closest('[data-task]');
  if (!card) return;
  const task = state.tasks.find((item) => item.id === Number(card.dataset.task));
  if (task) openTask(task);
});
document.querySelector('#closeTaskDialog').addEventListener('click', () => dialog.close());
dialog.addEventListener('close', clearReportMedia);
document.querySelector('#startTask').addEventListener('click', startCurrentTask);
document.querySelector('#openTaskCamera').addEventListener('click', () => document.querySelector('#taskCameraInput').click());
document.querySelector('#pickTaskFiles').addEventListener('click', () => document.querySelector('#taskFileInput').click());
document.querySelector('#taskCameraInput').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });
document.querySelector('#taskFileInput').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });
document.querySelector('#attachmentList').addEventListener('click', (event) => {
  const button = event.target.closest('[data-remove]');
  if (!button) return;
  const [removed] = state.attachments.splice(Number(button.dataset.remove), 1);
  if (removed) URL.revokeObjectURL(removed.url);
  renderAttachments();
});
document.querySelector('#completeTask').addEventListener('click', completeCurrentTask);
document.querySelector('#showSkipForm').addEventListener('click', () => { document.querySelector('#skipForm').hidden = false; });
document.querySelector('#skipTask').addEventListener('click', skipCurrentTask);
document.querySelector('#taskReportTelegram').addEventListener('click', (event) => {
  if (!tg?.openTelegramLink) return;
  event.preventDefault();
  tg.openTelegramLink(event.currentTarget.href);
});

(async () => {
  const payload = await loadTasks('active');
  const requestedId = Number(new URLSearchParams(window.location.search).get('task'));
  if (!payload || !requestedId) return;
  let task = state.tasks.find((item) => item.id === requestedId);
  if (!task) {
    document.querySelectorAll('.task-tabs button').forEach((item) => item.classList.toggle('active', item.dataset.scope === 'history'));
    await loadTasks('history');
    task = state.tasks.find((item) => item.id === requestedId);
  }
  if (task) openTask(task);
})();
