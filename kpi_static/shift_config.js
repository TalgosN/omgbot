const tg = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
tg?.ready();
tg?.expand();
tg?.setHeaderColor('#0d0913');
tg?.setBackgroundColor('#0d0913');

let state = null;
let selectedClub = 0;
let selectedAction = 'open';
let selectedVariant = 0;
let dirty = false;

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

function notice(text, error = false) {
  const element = $('#notice');
  element.textContent = text;
  element.classList.toggle('error', error);
  element.hidden = false;
  window.clearTimeout(notice.timer);
  notice.timer = window.setTimeout(() => { element.hidden = true; }, 5000);
}

function currentVariants() { return state.clubs[selectedClub].actions[selectedAction]; }
function currentVariant() { return currentVariants()[selectedVariant]; }
function markDirty() { dirty = true; $('#saveButton').textContent = 'Сохранить •'; }

function move(list, index, direction) {
  const target = index + direction;
  if (target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
  markDirty(); renderItems();
}

function rowActions(list, index) {
  const wrapper = document.createElement('div');
  wrapper.className = 'row-actions';
  [['↑', -1], ['↓', 1]].forEach(([label, direction]) => {
    const button = document.createElement('button');
    button.className = 'icon-button'; button.textContent = label;
    button.onclick = () => move(list, index, direction);
    wrapper.append(button);
  });
  const remove = document.createElement('button');
  remove.className = 'icon-button remove'; remove.textContent = '×';
  remove.onclick = () => { list.splice(index, 1); markDirty(); renderItems(); };
  wrapper.append(remove);
  return wrapper;
}

function renderQuestions(target, list) {
  target.innerHTML = '';
  if (!list.length) {
    target.innerHTML = '<div class="empty">Добавьте хотя бы один вопрос</div>';
    return;
  }
  list.forEach((item, index) => {
    const row = document.createElement('div'); row.className = 'edit-row question';
    const number = document.createElement('span'); number.className = 'number'; number.textContent = index + 1;
    const fields = document.createElement('div'); fields.className = 'question-fields';
    const input = document.createElement('textarea'); input.rows = 1; input.value = item.text;
    input.placeholder = 'Текст вопроса';
    input.oninput = () => { item.text = input.value; markDirty(); };
    const checklist = document.createElement('textarea'); checklist.rows = 1;
    checklist.value = item.checklist || '';
    checklist.placeholder = 'Пункт чек-листа перед вопросами (необязательно)';
    checklist.oninput = () => { item.checklist = checklist.value; markDirty(); };
    fields.append(input, checklist);
    const select = document.createElement('select');
    select.innerHTML = '<option value="text">Текст</option><option value="photo">Фото</option><option value="num">Число</option>';
    select.value = item.type;
    select.onchange = () => {
      if (select.value === 'photo' && list.filter((question) => question.type === 'photo').length >= 10) {
        select.value = item.type;
        notice('В одном наборе можно добавить не более 10 вопросов с фото', true);
        return;
      }
      item.type = select.value;
      markDirty();
    };
    row.append(number, fields, select);
    row.append(rowActions(list, index)); target.append(row);
  });
}

function renderItems() {
  renderQuestions($('#questionList'), currentVariant().questions);
}

function renderVariants() {
  const tabs = $('#variantTabs'); tabs.innerHTML = '';
  currentVariants().forEach((_variant, index) => {
    const button = document.createElement('button');
    button.className = `variant-tab${index === selectedVariant ? ' active' : ''}`;
    button.textContent = String.fromCharCode(65 + index);
    button.onclick = () => { selectedVariant = index; renderVariants(); renderItems(); };
    tabs.append(button);
  });
  $('#removeVariant').disabled = currentVariants().length === 1;
}

function render() {
  const select = $('#clubSelect'); select.innerHTML = '';
  state.clubs.forEach((club, index) => {
    const option = document.createElement('option'); option.value = index; option.textContent = club.name; select.append(option);
  });
  select.value = selectedClub;
  document.querySelectorAll('.action-tab').forEach((button) => button.classList.toggle('active', button.dataset.action === selectedAction));
  renderVariants(); renderItems(); $('#editor').hidden = false;
}

async function loadHistory() {
  const data = await api('/api/shift-config/history');
  $('#historyList').innerHTML = data.versions.map((version) => {
    const date = new Date(version.created_at).toLocaleString('ru-RU');
    const action = version.action === 'initial_import' ? 'Исходная версия' : version.action.startsWith('rollback:') ? 'Откат' : 'Сохранение';
    return `<div class="history-row"><div><strong>${action}</strong><small>${date} · ${version.actor_login}</small></div>${version.is_current ? '<small>Текущая</small>' : `<button class="soft rollback" data-id="${version.ID}">Вернуть</button>`}</div>`;
  }).join('');
  document.querySelectorAll('.rollback').forEach((button) => {
    button.onclick = async () => {
      if (!window.confirm('Сразу вернуть эту версию во все сценарии?')) return;
      try {
        state = await api(`/api/shift-config/history/${button.dataset.id}/rollback`, { method: 'POST', body: JSON.stringify({ version: state.version }) });
        selectedClub = 0; selectedVariant = 0; dirty = false; $('#saveButton').textContent = 'Сохранить'; render(); await loadHistory(); notice('Версия восстановлена и уже применяется в боте');
      } catch (error) { notice(error.message, true); }
    };
  });
}

$('#clubSelect').onchange = (event) => { selectedClub = Number(event.target.value); selectedVariant = 0; renderVariants(); renderItems(); };
document.querySelectorAll('.action-tab').forEach((button) => {
  button.onclick = () => { selectedAction = button.dataset.action; selectedVariant = 0; render(); };
});
$('#addVariant').onclick = () => {
  if (currentVariants().length >= 26) return notice('Можно создать не более 26 наборов', true);
  currentVariants().push({ questions: [{ text: '', type: 'text', checklist: '' }] });
  selectedVariant = currentVariants().length - 1; markDirty(); renderVariants(); renderItems();
};
$('#removeVariant').onclick = () => {
  if (currentVariants().length === 1 || !window.confirm('Удалить этот набор?')) return;
  currentVariants().splice(selectedVariant, 1); selectedVariant = Math.max(0, selectedVariant - 1); markDirty(); renderVariants(); renderItems();
};
$('#addQuestion').onclick = () => { currentVariant().questions.push({ text: '', type: 'text', checklist: '' }); markDirty(); renderItems(); };
$('#previewButton').onclick = () => {
  const variant = currentVariant();
  const list = (items, renderItem) => items.length ? `<ol>${items.map(renderItem).join('')}</ol>` : '<p class="hint">Нет пунктов</p>';
  const checklist = variant.questions.map((item) => item.checklist || '').filter((item) => item.trim());
  $('#previewContent').innerHTML = `<p><strong>${state.clubs[selectedClub].name}</strong> · ${selectedAction === 'open' ? 'Открытие' : 'Закрытие'} · Набор ${String.fromCharCode(65 + selectedVariant)}</p><div class="preview-block"><h3>Чек-лист</h3>${list(checklist, (item) => `<li>${escapeHtml(item)}</li>`)}</div><div class="preview-block"><h3>Вопросы</h3>${list(variant.questions, (item) => `<li>${escapeHtml(item.text)} <small>(${item.type})</small></li>`)}</div>`;
  $('#previewDialog').showModal();
};
$('#closePreview').onclick = () => $('#previewDialog').close();
$('#saveButton').onclick = async () => {
  const button = $('#saveButton'); button.disabled = true;
  try {
    state = await api('/api/shift-config', { method: 'PUT', body: JSON.stringify(state) });
    dirty = false; button.textContent = 'Сохранить'; await loadHistory(); notice('Сохранено. Новые смены уже используют эту версию.');
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
};
function escapeHtml(value) { return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;' }[char])); }
window.addEventListener('beforeunload', (event) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('omg:navigation-back', (event) => {
  event.preventDefault();
  window.location.assign('/shift');
});

(async () => {
  try { state = await api('/api/shift-config'); render(); await loadHistory(); }
  catch (error) { notice(error.message, true); }
})();
