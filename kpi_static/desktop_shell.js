(() => {
  const wide = window.matchMedia('(min-width: 1024px)');
  const modules = [
    ['/', '⌂', 'Главная', 'Обзор дня'],
    ['/kpi', '↗', 'OMG KPI', 'Показатели команды'],
    ['/problems', '?', 'Taskboard', 'Проблемы и ремонт'],
    ['/shift', '✓', 'OMG Shift', 'Смены и задачи'],
    ['/records', '★', 'Records', 'Рекорды и достижения'],
  ];
  const shell = document.createElement('aside');
  shell.className = 'desktop-sidebar';
  shell.setAttribute('aria-label', 'Навигация приложения');
  shell.innerHTML = `
    <a class="desktop-brand" href="/" aria-label="OMG VR · Главная"><span>OMG</span><b>VR</b></a>
    <p class="desktop-nav-caption">Рабочее пространство</p>
    <nav>${modules.map(([path, icon, title, subtitle]) => `
      <a href="${path}" title="${title}" ${location.pathname === path ? 'aria-current="page"' : ''}>
        <i aria-hidden="true">${icon}</i><span><strong>${title}</strong><small>${subtitle}</small></span>
      </a>`).join('')}</nav>
    <div class="desktop-sidebar-bottom">
      <button type="button" id="desktopFullscreen" hidden><i aria-hidden="true">⛶</i><span>Развернуть</span></button>
      <div class="desktop-profile"><i aria-hidden="true">●</i><span><strong id="desktopUserName">Команда OMG VR</strong><small id="desktopUserRole">Внутреннее приложение</small></span></div>
    </div>`;
  document.body.prepend(shell);
  const fullscreen = shell.querySelector('#desktopFullscreen');
  const tg = window.Telegram?.WebApp;
  if (typeof tg?.requestFullscreen === 'function' && (!tg.isVersionAtLeast || tg.isVersionAtLeast('8.0'))) {
    fullscreen.hidden = false;
    fullscreen.addEventListener('click', () => {
      try {
        if (tg.isFullscreen && typeof tg.exitFullscreen === 'function') tg.exitFullscreen();
        else tg.requestFullscreen();
      } catch (_) { fullscreen.hidden = true; }
    });
    tg.onEvent?.('fullscreenChanged', () => {
      fullscreen.querySelector('span').textContent = tg.isFullscreen ? 'Оконный режим' : 'Развернуть';
    });
    tg.onEvent?.('fullscreenFailed', (event) => {
      if (event.error === 'UNSUPPORTED') fullscreen.hidden = true;
    });
  }
  function syncNavigation() {
    shell.inert = !wide.matches;
  }
  wide.addEventListener('change', syncNavigation);
  syncNavigation();
  window.OmgDesktop = {
    setUser(me) {
      shell.querySelector('#desktopUserName').textContent = me.name || 'Команда OMG VR';
      shell.querySelector('#desktopUserRole').textContent = me.role_name || '';
      shell.querySelector('.desktop-profile').title = [me.name, me.role_name].filter(Boolean).join(' · ');
    },
  };
})();
