(() => {
  const tg = window.Telegram?.WebApp;
  let identity = String(tg?.initDataUnsafe?.user?.id || 'local');
  let database;
  let queue = Promise.resolve();
  const stateKey = () => `omg-view:${identity}:${location.pathname}`;

  function setIdentity(me) {
    identity = `${tg?.initDataUnsafe?.user?.id || me.login}:${me.login}:${me.preview ? JSON.stringify(me.preview) : 'live'}`;
  }

  function viewState() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(stateKey()) || 'null');
      return saved && Date.now() - saved.savedAt < 12 * 60 * 60 * 1000 ? saved : {};
    } catch (_) { return {}; }
  }

  function saveView(value) {
    try { sessionStorage.setItem(stateKey(), JSON.stringify({ ...value, scroll: window.scrollY, savedAt: Date.now() })); }
    catch (_) { /* Navigation still works when storage is disabled. */ }
  }

  function restoreScroll(saved) {
    if (Number.isFinite(saved.scroll)) requestAnimationFrame(() => requestAnimationFrame(() => window.scrollTo(0, saved.scroll)));
  }

  function openDatabase() {
    if (!database) database = new Promise((resolve, reject) => {
      const request = indexedDB.open('omg-app-drafts', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('drafts');
      request.onerror = () => { database = null; reject(request.error); };
      request.onblocked = () => { database = null; reject(new Error('Хранилище занято другой вкладкой')); };
      request.onsuccess = () => {
        const db = request.result;
        db.onversionchange = () => { db.close(); database = null; };
        resolve(db);
      };
    });
    return database;
  }

  function draftRequest(key, mode, operation) {
    const scopedKey = `${identity}:${key}`;
    const work = queue.catch(() => {}).then(async () => {
      const db = await openDatabase();
      return new Promise((resolve, reject) => {
        const transaction = db.transaction('drafts', mode);
        const request = operation(transaction.objectStore('drafts'), scopedKey);
        transaction.oncomplete = () => resolve(request.result);
        transaction.onerror = transaction.onabort = () => reject(transaction.error || new Error('Не удалось сохранить черновик'));
      });
    });
    queue = work;
    return work;
  }

  const drafts = {
    get: (key) => draftRequest(key, 'readonly', (store, id) => store.get(id)),
    put: (key, value) => draftRequest(key, 'readwrite', (store, id) => store.put(value, id)),
    remove: (key) => draftRequest(key, 'readwrite', (store, id) => store.delete(id)),
  };

  function upload(path, form, progress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', path);
      xhr.timeout = 180000;
      xhr.setRequestHeader('X-Telegram-Init-Data', tg?.initData || '');
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) progress?.(`Загрузка · ${Math.min(99, Math.round(event.loaded / event.total * 100))}%`);
      };
      xhr.upload.onload = () => progress?.('Загружено · ждём подтверждения');
      xhr.onload = () => {
        let payload;
        try { payload = JSON.parse(xhr.responseText); }
        catch (_) { reject(new Error('Ответ сервера не получен. Проверьте результат перед повторной отправкой.')); return; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
        else reject(new Error(payload.error || 'Не удалось отправить. Данные остались в форме.'));
      };
      xhr.onerror = xhr.ontimeout = xhr.onabort = () => reject(new Error('Отправка не подтверждена. Проверьте результат перед повторной отправкой.'));
      xhr.send(form);
    });
  }

  function busy(element, value) {
    element.dataset.busy = String(value);
    element.setAttribute('aria-busy', String(value));
    if (value && element.contains(document.activeElement)) document.activeElement.blur();
    if (value) tg?.enableClosingConfirmation?.();
    else if (!document.querySelector('[data-busy="true"]')) tg?.disableClosingConfirmation?.();
  }

  document.addEventListener('cancel', (event) => {
    if (event.target.dataset.busy === 'true') event.preventDefault();
  }, true);
  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-busy="true"]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('beforeinput', (event) => {
    if (event.target.closest('[data-busy="true"]')) event.preventDefault();
  }, true);
  document.addEventListener('keydown', (event) => {
    if (event.target.matches('input,textarea,select') && event.target.closest('[data-busy="true"]')) {
      if (event.key !== 'Tab') event.preventDefault();
    }
  }, true);
  window.addEventListener('beforeunload', (event) => {
    if (document.querySelector('[data-busy="true"]')) { event.preventDefault(); event.returnValue = ''; }
  });
  window.addEventListener('omg:navigation-back', (event) => {
    if (document.querySelector('[data-busy="true"]')) event.preventDefault();
  });

  function connectionStatus() {
    let banner = document.querySelector('#omgConnectionStatus');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'omgConnectionStatus';
      banner.className = 'omg-connection-status';
      banner.setAttribute('role', 'status');
      banner.textContent = 'Нет сети · отправка будет доступна после подключения';
      document.body.prepend(banner);
    }
    banner.hidden = navigator.onLine;
  }
  window.addEventListener('online', connectionStatus);
  window.addEventListener('offline', connectionStatus);
  connectionStatus();
  window.OmgApp = { setIdentity, viewState, saveView, restoreScroll, drafts, upload, busy };
})();
