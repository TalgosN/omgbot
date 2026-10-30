(() => {
  const wide = window.matchMedia('(min-width: 1024px)');
  const tg = window.Telegram?.WebApp;
  const desktopClient = ['tdesktop', 'macos', 'web', 'weba', 'webk'].includes(tg?.platform)
    || (!tg && window.matchMedia('(hover: hover) and (pointer: fine)').matches);
  document.body.classList.toggle('desktop-client', desktopClient);
  function syncSafeArea() {
    const top = Math.max(Number(tg?.safeAreaInset?.top) || 0, Number(tg?.contentSafeAreaInset?.top) || 0);
    document.documentElement.style.setProperty('--desktop-safe-top', `${top}px`);
  }
  tg?.onEvent?.('safeAreaChanged', syncSafeArea);
  tg?.onEvent?.('contentSafeAreaChanged', syncSafeArea);
  syncSafeArea();
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
      <a href="${path}" title="${title}" ${(location.pathname === path || (path === '/shift' && /^\/shift(?:\/|-)/.test(location.pathname))) ? 'aria-current="page"' : ''}>
        <i aria-hidden="true">${icon}</i><span><strong>${title}</strong><small>${subtitle}</small></span>
      </a>`).join('')}</nav>
    <div class="desktop-sidebar-bottom">
      <button type="button" id="desktopFullscreen" hidden><i aria-hidden="true">⛶</i><span>Развернуть</span></button>
      <div class="desktop-profile"><i aria-hidden="true">●</i><span><strong id="desktopUserName">Команда OMG VR</strong><small id="desktopUserRole">Внутреннее приложение</small></span></div>
    </div>`;
  document.body.prepend(shell);
  const launchbar = document.createElement('div');
  launchbar.className = 'desktop-launchbar';
  launchbar.innerHTML = '<a href="/">⌂ Главная</a><button id="desktopLaunchFullscreen" type="button" hidden>⛶ Развернуть приложение</button>';
  shell.insertAdjacentElement('afterend', launchbar);
  const fullscreen = shell.querySelector('#desktopFullscreen');
  const compactFullscreen = launchbar.querySelector('button');
  const telegramFullscreen = typeof tg?.requestFullscreen === 'function' && (!tg.isVersionAtLeast || tg.isVersionAtLeast('8.0'));
  const browserFullscreen = !tg && document.fullscreenEnabled && typeof document.documentElement.requestFullscreen === 'function';
  if (telegramFullscreen || browserFullscreen) {
    [fullscreen, compactFullscreen].forEach((button) => {
      button.hidden = false;
      button.addEventListener('click', async () => {
        try {
          if (telegramFullscreen) {
            if (tg.isFullscreen && typeof tg.exitFullscreen === 'function') tg.exitFullscreen();
            else tg.requestFullscreen();
          } else if (document.fullscreenElement) await document.exitFullscreen();
          else await document.documentElement.requestFullscreen();
        } catch (_) { showUnsupported(); }
      });
    });
    const updateFullscreen = () => {
      const active = telegramFullscreen ? tg.isFullscreen : Boolean(document.fullscreenElement);
      fullscreen.querySelector('span').textContent = active ? 'Оконный режим' : 'Развернуть';
      compactFullscreen.textContent = active ? '⛶ Оконный режим' : '⛶ Развернуть приложение';
    };
    tg?.onEvent?.('fullscreenChanged', updateFullscreen);
    document.addEventListener('fullscreenchange', updateFullscreen);
    tg?.onEvent?.('fullscreenFailed', (event) => {
      if (event.error === 'UNSUPPORTED') showUnsupported();
    });
    updateFullscreen();
  }
  function showUnsupported() {
    [fullscreen, compactFullscreen].forEach((button) => {
      button.disabled = true;
      button.title = 'Этот клиент не поддерживает разворачивание приложения. Измените размер окна средствами Telegram.';
    });
    compactFullscreen.textContent = 'Разворачивание недоступно';
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
