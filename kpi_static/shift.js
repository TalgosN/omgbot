const tg = window.Telegram?.WebApp;
const shiftActions = document.querySelector('#shiftActions');
const externalLink = document.querySelector('#openExternalShift');
const configLink = document.querySelector('#openShiftConfig');
const errorCard = document.querySelector('#shiftError');

tg?.ready();
tg?.expand();
tg?.setHeaderColor('#e9e3f3');
tg?.setBackgroundColor('#e9e3f3');

async function loadShift() {
  const response = await fetch('/api/shift', {
    headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Не удалось открыть OMG Shift');
  if (!payload.external_url) throw new Error('Адрес OMG Shift не настроен');

  externalLink.href = payload.external_url;
  configLink.hidden = !payload.can_manage;
  shiftActions.classList.toggle('manager', payload.can_manage);
  shiftActions.hidden = false;
  document.querySelector('#shiftRole').textContent = payload.can_manage
    ? 'Менеджмент'
    : 'Сотрудник';
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
