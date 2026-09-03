const tg = window.Telegram?.WebApp;
const consumablesState = {
  data: null, category: 'all', search: '', loaded: false, photoUrls: new Map(),
};
const errorCard = document.querySelector('#consumablesError');

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#0d0913');
tg?.setBackgroundColor('#0d0913');

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
  }[char]));
}

function number(value) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 })
    .format(Number(value || 0));
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}), 'X-Telegram-Init-Data': tg?.initData || '' };
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || 'Не удалось загрузить данные');
    error.payload = payload;
    throw error;
  }
  return payload;
}

function consumableDate(value) {
  if (!value) return '';
  const parsed = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  }).format(parsed);
}

function showConsumablesMessage(text, tone = '') {
  const status = document.querySelector('#consumablesStatus');
  status.textContent = text || '';
  status.className = `consumables-status ${tone}`.trim();
}

function consumableCategoryOptions(selected) {
  return (consumablesState.data?.categories || []).map((category) => `
    <option value="${category.id}" ${Number(selected) === Number(category.id) ? 'selected' : ''}>
      ${escapeHtml(category.emoji)} ${escapeHtml(category.name)}
    </option>
  `).join('');
}

async function loadConsumablePhotos() {
  const images = [...document.querySelectorAll('img[data-consumable-photo]')];
  await Promise.all(images.map(async (img) => {
    const productId = img.dataset.consumablePhoto;
    const version = img.dataset.photoVersion || '';
    const key = `${productId}:${version}`;
    try {
      if (!consumablesState.photoUrls.has(key)) {
        const response = await fetch(`/api/shift/consumables/products/${productId}/photo`, {
          headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
        });
        if (!response.ok) return;
        consumablesState.photoUrls.set(key, URL.createObjectURL(await response.blob()));
      }
      img.src = consumablesState.photoUrls.get(key);
      img.closest('.consumable-photo')?.classList.add('loaded');
    } catch (_error) {
      // The product card keeps its neutral placeholder when a photo is unavailable.
    }
  }));
}

function consumableCard(item) {
  const photo = item.has_photo
    ? `<img data-consumable-photo="${item.product_id}" data-photo-version="${escapeHtml(item.photo_updated_at || '')}" alt="${escapeHtml(item.name)}">`
    : '<span>📦</span>';
  const state = item.is_active
    ? `<span class="stock-state ${item.is_low ? 'low' : 'ok'}">${item.is_low ? 'Мало' : 'В норме'}</span>`
    : '<span class="stock-state archived">В архиве</span>';
  const primaryAction = item.is_active
    ? `<button class="stock-update" type="button" data-consumable-quantity="${item.id}">Изменить остаток</button>`
    : (consumablesState.data.can_manage
      ? `<button class="stock-restore" type="button" data-consumable-restore="${item.id}">Вернуть из архива</button>` : '');
  const management = consumablesState.data.can_manage
    ? `<button class="stock-manage" type="button" data-consumable-manage="${item.id}" aria-label="Настроить">•••</button>` : '';
  return `
    <article class="consumable-card ${item.is_low ? 'low' : ''} ${item.is_active ? '' : 'archived'}">
      <div class="consumable-photo">${photo}</div>
      <div class="consumable-info">
        <div class="consumable-name-row"><div><small>${escapeHtml(item.category_emoji)} ${escapeHtml(item.category_name)}</small><h3>${escapeHtml(item.name)}</h3></div>${management}</div>
        <div class="consumable-stock"><strong>${number(item.quantity)} <small>шт.</small></strong><span>мин. ${number(item.min_limit)}</span>${state}</div>
        ${item.archive_reason ? `<p class="archive-reason">${escapeHtml(item.archive_reason)}</p>` : ''}
        <div class="consumable-card-actions">${primaryAction}<button type="button" data-consumable-history="${item.id}">История</button></div>
      </div>
    </article>`;
}

function renderConsumables() {
  const data = consumablesState.data;
  if (!data) return;
  const clubSelect = document.querySelector('#consumablesClub');
  clubSelect.innerHTML = data.clubs.map((club) => `
    <option value="${escapeHtml(club)}" ${club === data.selected_club ? 'selected' : ''}>${escapeHtml(club)}</option>
  `).join('');
  document.querySelector('#addConsumable').hidden = !data.can_manage;
  document.querySelector('#manageConsumableCategories').hidden = !data.can_manage;
  document.querySelector('#consumablesArchiveLabel').hidden = !data.can_manage;
  document.querySelector('#consumablesArchiveSummary').hidden = !data.can_manage;
  document.querySelector('.consumables-overview').classList.toggle('employee', !data.can_manage);
  const low = Number(data.summary?.low || 0);
  document.querySelector('#consumablesActiveCount').textContent = number(data.summary?.active);
  document.querySelector('#consumablesLowCount').textContent = number(low);
  document.querySelector('#consumablesArchivedCount').textContent = number(data.summary?.archived);
  const categoryButtons = [
    { slug: 'all', emoji: '•', name: 'Все' },
    ...(data.categories || []),
  ];
  document.querySelector('#consumablesCategories').innerHTML = categoryButtons.map((category) => `
    <button type="button" data-consumable-category="${category.slug}" class="${consumablesState.category === category.slug ? 'active' : ''}">
      ${escapeHtml(category.emoji)} ${escapeHtml(category.name)}
    </button>
  `).join('');
  const query = consumablesState.search.toLocaleLowerCase('ru');
  const items = (data.items || []).filter((item) => (
    (consumablesState.category === 'all' || item.category_slug === consumablesState.category)
    && (!query || item.name.toLocaleLowerCase('ru').includes(query))
  ));
  document.querySelector('#consumablesList').innerHTML = items.length
    ? items.map(consumableCard).join('')
    : '<div class="shift-empty">Подходящих позиций нет</div>';
  loadConsumablePhotos();
}

async function loadConsumables(club = null) {
  const archived = document.querySelector('#consumablesArchived').checked ? '&archived=1' : '';
  const clubQuery = club ? `club=${encodeURIComponent(club)}` : '';
  const payload = await api(`/api/shift/consumables?${clubQuery}${archived}`);
  consumablesState.data = payload;
  consumablesState.loaded = true;
  renderConsumables();
}

function showConsumableModal(title, body) {
  document.querySelector('#consumableModalTitle').textContent = title;
  document.querySelector('#consumableModalBody').innerHTML = body;
  document.querySelector('#consumableModal').hidden = false;
  document.body.classList.add('modal-open');
}

function closeConsumableModal() {
  document.querySelector('#consumableModal').hidden = true;
  document.body.classList.remove('modal-open');
}

function consumableItem(itemId) {
  return consumablesState.data?.items.find((item) => item.id === Number(itemId));
}

function openQuantityModal(item) {
  showConsumableModal('Новый остаток', `
    <form id="consumableQuantityForm" class="consumable-form" data-item-id="${item.id}">
      <div class="modal-product"><span>${escapeHtml(item.category_emoji)}</span><div><small>${escapeHtml(item.club)}</small><strong>${escapeHtml(item.name)}</strong></div></div>
      <label><span>Сколько сейчас</span><input name="quantity" type="number" min="0" step="1" inputmode="numeric" value="${item.quantity}" required autofocus></label>
      <p class="form-hint">Минимальный остаток: ${number(item.min_limit)} шт.</p>
      <button class="modal-primary" type="submit">Сохранить остаток</button>
    </form>`);
}

function openAddConsumableModal() {
  const data = consumablesState.data;
  showConsumableModal('Добавить товар', `
    <form id="consumableAddForm" class="consumable-form">
      <label><span>Клуб</span><select name="club">${data.clubs.map((club) => `<option ${club === data.selected_club ? 'selected' : ''}>${escapeHtml(club)}</option>`).join('')}</select></label>
      <label><span>Название</span><input name="name" maxlength="100" placeholder="Например, Coca-Cola 0,5" required></label>
      <label><span>Категория</span><select name="category_id">${consumableCategoryOptions()}</select></label>
      <div class="consumable-form-grid">
        <label><span>Остаток</span><input name="quantity" type="number" min="0" step="1" value="0" required></label>
        <label><span>Минимум</span><input name="min_limit" type="number" min="0" step="1" value="5" required></label>
      </div>
      <label class="photo-picker"><span>📷 Фото товара <small>· необязательно</small></span><input name="photo" type="file" accept="image/*"><b>Выбрать фото</b></label>
      <p class="form-hint">Если товар уже есть в другом клубе, приложение использует его общую карточку и фото.</p>
      <button class="modal-primary" type="submit">Добавить в клуб</button>
    </form>`);
}

function openConsumableCategoriesModal() {
  const categories = consumablesState.data?.categories || [];
  showConsumableModal('Категории', `
    <div class="category-manager-list">${categories.map((category) => `<div><span>${escapeHtml(category.emoji)}</span><strong>${escapeHtml(category.name)}</strong></div>`).join('')}</div>
    <form id="consumableCategoryForm" class="consumable-form category-manager-form">
      <p class="form-hint">Новая категория сразу появится в списке при добавлении товара.</p>
      <div class="category-manager-fields">
        <label><span>Эмодзи</span><input name="emoji" maxlength="8" value="📦" required></label>
        <label><span>Название</span><input name="name" maxlength="60" placeholder="Например, Снеки" required></label>
      </div>
      <button class="modal-primary" type="submit">+ Добавить категорию</button>
    </form>`);
}

function openManageConsumableModal(item) {
  showConsumableModal('Настройки товара', `
    <form id="consumableManageForm" class="consumable-form" data-item-id="${item.id}">
      <div class="modal-product"><span>${escapeHtml(item.category_emoji)}</span><div><small>${escapeHtml(item.club)}</small><strong>${escapeHtml(item.name)}</strong></div></div>
      <label><span>Минимальный остаток</span><input name="min_limit" type="number" min="0" step="1" value="${item.min_limit}" required></label>
      <label><span>Категория <small>· общая для всех клубов</small></span><select name="category_id">${consumableCategoryOptions(item.category_id)}</select></label>
      <label class="photo-picker"><span>📷 Фото <small>· общее для всех клубов</small></span><input name="photo" type="file" accept="image/*"><b>${item.has_photo ? 'Заменить фото' : 'Добавить фото'}</b></label>
      <button class="modal-primary" type="submit">Сохранить настройки</button>
      ${item.is_active ? `<div class="archive-box"><label><span>Причина архива <small>· необязательно</small></span><input name="archive_reason" maxlength="300" placeholder="Например, временно не закупаем"></label><button type="button" data-archive-from-modal="${item.id}">Убрать в архив</button></div>` : `<button class="modal-restore" type="button" data-consumable-restore="${item.id}">Вернуть из архива</button>`}
    </form>`);
}

async function compressConsumablePhoto(file) {
  if (!file) return null;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1280 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
    if (blob) return new File([blob], 'product.jpg', { type: 'image/jpeg' });
  } catch (_error) {
    // Older WebViews can upload the original supported image.
  }
  if (file.size > 3 * 1024 * 1024) throw new Error('Не удалось сжать фото. Выберите файл меньше 3 МБ.');
  return file;
}

async function reloadConsumables(message = '') {
  await loadConsumables(consumablesState.data?.selected_club);
  if (message) showConsumablesMessage(message, 'success');
}

document.querySelector('#consumablesClub').addEventListener('change', (event) => {
  showConsumablesMessage('Загружаю…');
  loadConsumables(event.target.value).then(() => showConsumablesMessage('')).catch((error) => showConsumablesMessage(error.message, 'error'));
});

document.querySelector('#consumablesArchived').addEventListener('change', () => {
  loadConsumables(consumablesState.data?.selected_club).catch((error) => showConsumablesMessage(error.message, 'error'));
});

document.querySelector('#consumablesSearch').addEventListener('input', (event) => {
  consumablesState.search = event.target.value.trim();
  renderConsumables();
});

document.querySelector('#consumablesCategories').addEventListener('click', (event) => {
  const button = event.target.closest('[data-consumable-category]');
  if (!button) return;
  consumablesState.category = button.dataset.consumableCategory;
  renderConsumables();
});

document.querySelector('#addConsumable').addEventListener('click', openAddConsumableModal);
document.querySelector('#manageConsumableCategories').addEventListener('click', openConsumableCategoriesModal);

document.querySelector('#consumablesList').addEventListener('click', async (event) => {
  const quantityButton = event.target.closest('[data-consumable-quantity]');
  const manageButton = event.target.closest('[data-consumable-manage]');
  const restoreButton = event.target.closest('[data-consumable-restore]');
  const historyButton = event.target.closest('[data-consumable-history]');
  if (quantityButton) openQuantityModal(consumableItem(quantityButton.dataset.consumableQuantity));
  if (manageButton) openManageConsumableModal(consumableItem(manageButton.dataset.consumableManage));
  if (restoreButton) {
    restoreButton.disabled = true;
    try {
      await api(`/api/shift/consumables/${restoreButton.dataset.consumableRestore}/restore`, { method: 'POST' });
      await reloadConsumables('Позиция вернута из архива');
    } catch (error) { showConsumablesMessage(error.message, 'error'); }
  }
  if (historyButton) {
    try {
      const payload = await api(`/api/shift/consumables/${historyButton.dataset.consumableHistory}/history`);
      const labels = { quantity: 'Остаток', created: 'Добавлено', settings: 'Настройки', archived: 'Архив', restored: 'Восстановлено', photo: 'Фото' };
      showConsumableModal('История', `<div class="consumable-history-head"><strong>${escapeHtml(payload.item.name)}</strong><span>${escapeHtml(payload.item.club)}</span></div><div class="consumable-history-list">${payload.events.length ? payload.events.map((item) => `<div><span><strong>${escapeHtml(labels[item.event_type] || item.event_type)}</strong><small>${escapeHtml(item.actor || '—')}</small></span><span><b>${escapeHtml(item.details || '')}</b><small>${escapeHtml(consumableDate(item.created_at))}</small></span></div>`).join('') : '<p>История пока пуста</p>'}</div>`);
    } catch (error) { showConsumablesMessage(error.message, 'error'); }
  }
});

document.querySelector('#consumableModal').addEventListener('click', async (event) => {
  if (event.target.closest('[data-close-consumable-modal]')) closeConsumableModal();
  const restoreButton = event.target.closest('[data-consumable-restore]');
  if (restoreButton) {
    restoreButton.disabled = true;
    try {
      await api(`/api/shift/consumables/${restoreButton.dataset.consumableRestore}/restore`, { method: 'POST' });
      closeConsumableModal();
      await reloadConsumables('Позиция вернута из архива');
    } catch (error) { restoreButton.disabled = false; alert(error.message); }
  }
  const archiveButton = event.target.closest('[data-archive-from-modal]');
  if (archiveButton) {
    archiveButton.disabled = true;
    const form = archiveButton.closest('form');
    try {
      await api(`/api/shift/consumables/${archiveButton.dataset.archiveFromModal}/archive`, {
        method: 'POST', body: JSON.stringify({ reason: form.elements.archive_reason.value.trim() }),
      });
      closeConsumableModal();
      await reloadConsumables('Позиция убрана в архив');
    } catch (error) { archiveButton.disabled = false; alert(error.message); }
  }
});

document.querySelector('#consumableModal').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector('[type="submit"]');
  submit.disabled = true;
  try {
    if (form.id === 'consumableQuantityForm') {
      const quantity = Number(form.elements.quantity.value);
      const payload = await api(`/api/shift/consumables/${form.dataset.itemId}/quantity`, {
        method: 'POST', body: JSON.stringify({ quantity }),
      });
      closeConsumableModal();
      await reloadConsumables(payload.warnings?.[0] || 'Остаток обновлён');
    } else if (form.id === 'consumableCategoryForm') {
      await api('/api/shift/consumables/categories', {
        method: 'POST',
        body: JSON.stringify({
          emoji: form.elements.emoji.value.trim(),
          name: form.elements.name.value.trim(),
        }),
      });
      closeConsumableModal();
      await reloadConsumables('Категория добавлена');
    } else if (form.id === 'consumableAddForm') {
      const data = new FormData(form);
      const selectedPhoto = form.elements.photo.files[0];
      if (selectedPhoto) data.set('photo', await compressConsumablePhoto(selectedPhoto));
      try {
        await api('/api/shift/consumables', { method: 'POST', body: data });
      } catch (error) {
        if (error.payload?.conflict === 'archived' && window.confirm(error.message)) {
          await api(`/api/shift/consumables/${error.payload.item_id}/restore`, { method: 'POST' });
        } else throw error;
      }
      const club = form.elements.club.value;
      closeConsumableModal();
      await loadConsumables(club);
      showConsumablesMessage('Позиция добавлена', 'success');
    } else if (form.id === 'consumableManageForm') {
      const itemId = form.dataset.itemId;
      await api(`/api/shift/consumables/${itemId}`, {
        method: 'PATCH',
        body: JSON.stringify({
          min_limit: Number(form.elements.min_limit.value),
          category_id: Number(form.elements.category_id.value),
        }),
      });
      const selectedPhoto = form.elements.photo.files[0];
      if (selectedPhoto) {
        const photoData = new FormData();
        photoData.set('photo', await compressConsumablePhoto(selectedPhoto));
        await api(`/api/shift/consumables/${itemId}/photo`, { method: 'POST', body: photoData });
      }
      closeConsumableModal();
      await reloadConsumables('Настройки сохранены');
    }
    tg?.HapticFeedback?.notificationOccurred('success');
  } catch (error) {
    submit.disabled = false;
    alert(error.message);
  }
});

async function initializeConsumables() {
  try {
    const user = await api('/api/shift');
    document.querySelector('#consumablesUserName').textContent = `Команда OMG VR · ${user.user_name}`;
    document.querySelector('#consumablesRole').textContent = user.role_name;
  } catch (error) {
    document.querySelector('#consumablesRole').textContent = 'OMG VR';
    errorCard.textContent = error.message;
    errorCard.hidden = false;
    return;
  }
  try {
    const selectedClub = new URLSearchParams(window.location.search).get('club');
    await loadConsumables(selectedClub);
  } catch (error) {
    document.querySelector('#consumablesList').innerHTML = `<div class="error-card">${escapeHtml(error.message)}</div>`;
  }
}

tg?.BackButton?.show();
tg?.BackButton?.onClick(() => window.location.assign('/shift'));
window.addEventListener('pagehide', () => tg?.BackButton?.hide());

initializeConsumables();
