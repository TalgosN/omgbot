(() => {
  const tg = window.Telegram?.WebApp;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
    }[char]));
  }

  function shortDate(value) {
    const [year, month, day] = String(value || '').slice(0, 10).split('-');
    return year && month && day ? `${day}.${month}.${year}` : '—';
  }

  function shiftTime(shift) {
    if (shift.start && shift.end) return `${shift.start}–${shift.end}`;
    return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Number(shift.duration || 0))} ч`;
  }

  async function api(path, options = {}) {
    const headers = {
      'X-Telegram-Init-Data': tg?.initData || '',
      ...(options.headers || {}),
    };
    if (options.body && !(options.body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, { ...options, headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'Не удалось выполнить действие');
    return payload;
  }

  function notify(message) {
    if (tg?.showAlert) tg.showAlert(message);
    else window.alert(message);
  }

  function installStyles() {
    const style = document.createElement('style');
    style.textContent = `
      .employee-preview-banner{position:sticky;top:0;z-index:9998;display:grid;padding:max(9px,env(safe-area-inset-top)) 12px 10px;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:9px;border-bottom:1px solid rgba(255,255,255,.18);color:#fff;background:linear-gradient(105deg,#c85a19,#8f3513);box-shadow:0 8px 24px rgba(39,15,8,.25);font-family:Montserrat,sans-serif}
      .employee-preview-banner strong,.employee-preview-banner span{display:block}.employee-preview-banner strong{font-size:12px}.employee-preview-banner span{margin-top:3px;color:rgba(255,255,255,.78);font-size:9px;line-height:1.35}.employee-preview-actions{display:flex;gap:6px}.employee-preview-actions button{min-height:34px;padding:0 9px;border:1px solid rgba(255,255,255,.22);border-radius:10px;color:#fff;background:rgba(255,255,255,.1);font-size:9px;font-weight:800}.employee-preview-actions button:last-child{background:rgba(25,8,4,.24)}
      .employee-preview-entry{display:flex;width:100%;min-height:48px;margin:12px 0 3px;padding:0 14px;align-items:center;justify-content:space-between;border:1px solid rgba(112,70,217,.2);border-radius:16px;color:#4d2aa9;background:rgba(255,255,255,.68);box-shadow:0 10px 28px rgba(76,51,140,.1);font:800 11px Montserrat,sans-serif}.employee-preview-entry span{font-size:17px}
      .employee-preview-dialog{width:min(520px,calc(100% - 24px));max-height:calc(100dvh - 28px);padding:0;overflow:hidden;border:1px solid rgba(112,70,217,.2);border-radius:23px;color:#24203c;background:#f3eef9;box-shadow:0 28px 90px rgba(40,24,75,.38);font-family:Montserrat,sans-serif}.employee-preview-dialog::backdrop{background:rgba(19,10,36,.68);backdrop-filter:blur(5px)}.employee-preview-dialog-head{display:flex;padding:18px 18px 13px;align-items:flex-start;justify-content:space-between;gap:12px}.employee-preview-dialog-head p{margin:0 0 5px;color:#7046d9;font-size:8px;font-weight:800;letter-spacing:.09em;text-transform:uppercase}.employee-preview-dialog-head h2{margin:0;font-size:21px}.employee-preview-close{width:36px;height:36px;border:0;border-radius:12px;color:#4d2aa9;background:#e7ddf7;font-size:20px}.employee-preview-search{width:calc(100% - 36px);height:44px;margin:0 18px 12px;padding:0 13px;border:1px solid rgba(112,70,217,.18);border-radius:13px;outline:0;color:#24203c;background:#fff;font:650 11px Montserrat,sans-serif}.employee-preview-list{display:grid;max-height:min(62dvh,560px);padding:0 12px 16px;gap:8px;overflow:auto}.employee-preview-shift{display:grid;width:100%;min-height:68px;padding:11px 12px;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:10px;border:1px solid rgba(112,70,217,.12);border-radius:15px;color:#24203c;text-align:left;background:#fff;font-family:Montserrat,sans-serif}.employee-preview-shift strong,.employee-preview-shift span,.employee-preview-shift small{display:block}.employee-preview-shift strong{font-size:13px}.employee-preview-shift span{margin-top:4px;color:#756f8d;font-size:9px}.employee-preview-shift small{color:#7046d9;font-size:9px;font-weight:800;text-align:right}.employee-preview-empty{padding:24px;color:#756f8d;font-size:11px;text-align:center}
      @media(max-width:390px){.employee-preview-banner{grid-template-columns:1fr}.employee-preview-actions button{flex:1}.employee-preview-actions{width:100%}}
    `;
    document.head.appendChild(style);
  }

  function renderBanner(preview) {
    document.documentElement.classList.add('employee-preview-active');
    const banner = document.createElement('aside');
    banner.className = 'employee-preview-banner';
    banner.innerHTML = `
      <div><strong>🧪 Тест: ${escapeHtml(preview.employee_name)}</strong><span>${escapeHtml(preview.club)} · ${shortDate(preview.date)} · ${shiftTime(preview)} · рабочие записи отключены</span></div>
      <div class="employee-preview-actions"><button type="button" data-preview-notify>Проверить уведомление</button><button type="button" data-preview-exit>Выйти</button></div>`;
    document.body.prepend(banner);
    banner.querySelector('[data-preview-exit]').addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await api('/api/employee-preview/stop', { method: 'POST', body: '{}' });
        window.location.assign('/');
      } catch (error) {
        button.disabled = false;
        notify(error.message);
      }
    });
    banner.querySelector('[data-preview-notify]').addEventListener('click', async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      const label = button.textContent;
      button.textContent = 'Отправляем…';
      try {
        const result = await api('/api/employee-preview/notification', { method: 'POST', body: '{}' });
        notify(result.tasks ? 'Тестовое уведомление о задаче отправлено тебе в ЛС.' : 'Тестовое уведомление о смене отправлено тебе в ЛС.');
      } catch (error) {
        notify(error.message);
      } finally {
        button.disabled = false;
        button.textContent = label;
      }
    });
  }

  function renderShiftList(dialog, shifts, filter = '') {
    const list = dialog.querySelector('[data-preview-list]');
    const query = filter.trim().toLocaleLowerCase('ru');
    const visible = shifts.filter((shift) => !query || [
      shift.employee_name, shift.employee_login, shift.club, shortDate(shift.date),
    ].some((value) => String(value || '').toLocaleLowerCase('ru').includes(query)));
    list.innerHTML = visible.length ? visible.map((shift, index) => `
      <button class="employee-preview-shift" type="button" data-preview-shift="${index}">
        <span><strong>${escapeHtml(shift.employee_name)}</strong><span>${escapeHtml(shift.club)} · ${escapeHtml(shift.employee_login)}</span></span>
        <small>${shortDate(shift.date)}<br>${shiftTime(shift)}</small>
      </button>`).join('') : '<div class="employee-preview-empty">Подходящих смен не найдено.</div>';
    list.querySelectorAll('[data-preview-shift]').forEach((button) => {
      button.addEventListener('click', async () => {
        const shift = visible[Number(button.dataset.previewShift)];
        button.disabled = true;
        try {
          await api('/api/employee-preview/start', {
            method: 'POST',
            body: JSON.stringify({
              employee_login: shift.employee_login,
              date: shift.date,
              club: shift.club,
            }),
          });
          window.location.reload();
        } catch (error) {
          button.disabled = false;
          notify(error.message);
        }
      });
    });
  }

  function bindEntry(entry) {
    entry.hidden = false;
    entry.addEventListener('click', async () => {
      entry.disabled = true;
      try {
        const { shifts } = await api('/api/employee-preview/shifts');
        const dialog = document.createElement('dialog');
        dialog.className = 'employee-preview-dialog';
        dialog.innerHTML = `
          <div class="employee-preview-dialog-head"><div><p>Только для владельца</p><h2>Выбрать смену</h2></div><button class="employee-preview-close" type="button" aria-label="Закрыть">×</button></div>
          <input class="employee-preview-search" type="search" placeholder="Сотрудник, клуб или дата">
          <div class="employee-preview-list" data-preview-list></div>`;
        document.body.appendChild(dialog);
        renderShiftList(dialog, shifts || []);
        dialog.querySelector('.employee-preview-search').addEventListener('input', (event) => renderShiftList(dialog, shifts || [], event.target.value));
        dialog.querySelector('.employee-preview-close').addEventListener('click', () => dialog.close());
        dialog.addEventListener('close', () => dialog.remove());
        dialog.showModal();
      } catch (error) {
        notify(error.message);
      } finally {
        entry.disabled = false;
      }
    });
  }

  function installEntry() {
    const explicitEntries = document.querySelectorAll('[data-employee-preview-entry]');
    if (explicitEntries.length) {
      explicitEntries.forEach(bindEntry);
      return;
    }
    const header = document.querySelector('.home-header');
    if (!header) return;
    const entry = document.createElement('button');
    entry.className = 'employee-preview-entry';
    entry.type = 'button';
    entry.innerHTML = 'Посмотреть приложение как сотрудник <span>→</span>';
    header.insertAdjacentElement('afterend', entry);
    bindEntry(entry);
  }

  async function initialize() {
    installStyles();
    try {
      const me = await api('/api/me');
      window.OmgEmployeePreview = me.preview || null;
      if (me.preview) renderBanner(me.preview);
      else if (me.can_preview_employee && (
        window.location.pathname === '/'
        || document.querySelector('[data-employee-preview-entry]')
      )) installEntry();
    } catch (_) {
      // Main page modules show the authorization error themselves.
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialize, { once: true });
  } else {
    initialize();
  }
})();
