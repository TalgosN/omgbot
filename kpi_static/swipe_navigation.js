(() => {
  const tg = window.Telegram?.WebApp;
  const modules = [
    { path: '/kpi', available: () => true },
    { path: '/problems', available: () => true },
    { path: '/shift-config', available: (me) => Boolean(me?.can_manage) },
  ];
  const ignoredTargets = [
    'input', 'textarea', 'select', 'button', 'a', 'canvas', 'svg',
    '[contenteditable="true"]', '[data-swipe-ignore]',
  ].join(',');
  const minimumDistance = 72;
  const horizontalRatio = 1.35;
  const maximumDuration = 1000;
  let me = null;
  let accessReady = false;
  let start = null;

  function currentPath() {
    const path = window.location.pathname.replace(/\/+$/, '');
    return path || '/';
  }

  function openedDialog() {
    return [...document.querySelectorAll('dialog[open]')].at(-1) || null;
  }

  function navigate(path) {
    if (currentPath() === path) return;
    window.location.assign(path);
  }

  function goBack() {
    const dialog = openedDialog();
    if (dialog) {
      dialog.close();
      return;
    }
    const event = new CustomEvent('omg:navigation-back', { cancelable: true });
    if (!window.dispatchEvent(event)) return;
    navigate('/');
  }

  function goToNextModule() {
    if (openedDialog() || !accessReady) return;
    const available = modules.filter((module) => module.available(me));
    const index = available.findIndex((module) => module.path === currentPath());
    if (index < 0 || available.length < 2) return;
    navigate(available[(index + 1) % available.length].path);
  }

  function touchPoint(event) {
    return event.changedTouches?.[0] || event.touches?.[0] || null;
  }

  document.addEventListener('touchstart', (event) => {
    if (event.touches.length !== 1 || event.target.closest(ignoredTargets)) {
      start = null;
      return;
    }
    const point = touchPoint(event);
    start = point ? {
      x: point.clientX,
      y: point.clientY,
      time: performance.now(),
    } : null;
  }, { passive: true });

  document.addEventListener('touchmove', (event) => {
    if (!start || event.touches.length !== 1) return;
    const point = touchPoint(event);
    if (!point) return;
    const dx = point.clientX - start.x;
    const dy = point.clientY - start.y;
    if (Math.abs(dx) > 14 && Math.abs(dx) > Math.abs(dy) * horizontalRatio) {
      event.preventDefault();
    }
  }, { passive: false });

  document.addEventListener('touchend', (event) => {
    if (!start) return;
    const point = touchPoint(event);
    const gesture = start;
    start = null;
    if (!point) return;
    const dx = point.clientX - gesture.x;
    const dy = point.clientY - gesture.y;
    const duration = performance.now() - gesture.time;
    if (
      duration > maximumDuration
      || Math.abs(dx) < minimumDistance
      || Math.abs(dx) < Math.abs(dy) * horizontalRatio
    ) return;
    if (dx > 0) goBack();
    else goToNextModule();
  }, { passive: true });

  document.addEventListener('touchcancel', () => { start = null; }, { passive: true });

  fetch('/api/me', {
    headers: { 'X-Telegram-Init-Data': tg?.initData || '' },
  })
    .then((response) => response.ok ? response.json() : null)
    .then((payload) => { me = payload; accessReady = true; })
    .catch(() => { accessReady = true; });

  window.OmgSwipeNavigation = { goBack, goToNextModule };
})();
