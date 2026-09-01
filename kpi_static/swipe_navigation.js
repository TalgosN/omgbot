(() => {
  const modules = [
    '/kpi',
    '/problems',
    '/records',
    '/shift',
  ];
  const ignoredTargets = [
    'input', 'textarea', 'select', 'button', 'a', 'canvas', 'svg',
    '[contenteditable="true"]', '[data-swipe-ignore]',
  ].join(',');
  const minimumDistance = 72;
  const horizontalRatio = 1.35;
  const maximumDuration = 1000;
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
    navigate(document.body.dataset.swipeBack || '/');
  }

  function goToNextModule() {
    if (openedDialog()) return;
    const index = modules.indexOf(currentPath());
    if (index < 0) return;
    navigate(modules[(index + 1) % modules.length]);
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

  window.OmgSwipeNavigation = { goBack, goToNextModule };
})();
