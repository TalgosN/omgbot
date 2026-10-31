"""Responsive module checks with demo APIs; no production writes or deployment.

Run with: python scripts/check_desktop_modules.py (Playwright + Microsoft Edge).
"""
from copy import deepcopy
from pathlib import Path
import json
import re
import subprocess
import tempfile
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from check_desktop_ui import CLUBS, ROOT, SIZES, home_payload


EMPLOYEES = [{
    'login': f'@employee{i}', 'nickname': name, 'rank': i + 1, 'zone': '🟢' if i < 3 else '🟡',
    'total_pct': .98 - i * .04, 'weighted_pct': 1.05 - i * .03, 'shifts': 14 - i,
    'weighted_shifts': 16 - i, 'clubs': [CLUBS[i % 5]], 'stream': False,
    **{key: 5 for key in ['reviews', 'forms', 'extensions', 'certificates', 'subscriptions', 'initiatives']},
    **{key + '_pct': .8 for key in ['reviews', 'forms', 'extensions', 'certificates', 'subscriptions', 'initiatives']},
    'explanation': {'total_pct': .98, 'metric_contribution_pct': .98, 'metrics': [
        {'key': key, 'label': label, 'target': 14, 'fact': 11 + metric_index,
         'ratio': .8 + metric_index * .04, 'contribution_pct': .12 + metric_index * .01,
         'needed': max(0, 3 - metric_index)}
        for metric_index, (key, label) in enumerate([
            ('reviews', 'Отзывы'), ('forms', 'Анкеты'), ('extensions', 'Продления'),
            ('certificates', 'Сертификаты'), ('subscriptions', 'Абонементы'),
        ])
    ], 'pace': {}},
} for i, name in enumerate(['Миша', 'Мося', 'Настя', 'Ваня', 'Лиза', 'Паша'])]
SHIFT_TASKS = [{
    'id': i * 5 + club_index + 1, 'template_id': i + 1, 'club': club,
    'title': title, 'date': '2026-10-30', 'status': 'in_progress',
    'available_at': '2026-10-30T10:00:00', 'due_at': '2026-10-30T19:00:00',
    'can_execute': True, 'required_attachments': 1, 'requirements': ['Фото результата'],
    'instructions': 'Выполните задачу и приложите фотографию результата.', 'media': [],
} for i, title in enumerate(['Проверка оборудования', 'Уборка ресепшена', 'Подготовка лаунжа']) for club_index, club in enumerate(CLUBS)]
CATALOG = [{
    'id': i + 1, 'product_id': i + 1, 'name': name, 'quantity': 24 - i,
    'min_limit': 5, 'is_active': True, 'is_low': i == 2, 'has_photo': False,
    'category_id': 1 if i < 4 else 2, 'category_slug': 'drinks' if i < 4 else 'cleaning',
    'category_emoji': '🥤' if i < 4 else '🧼', 'category_name': 'Напитки' if i < 4 else 'Уборка',
} for i, name in enumerate(['Adrenaline Original', 'Adrenaline Zero Sugar', 'Вода без газа', 'Сок яблочный', 'Жидкое мыло', 'Салфетки', 'Средство для стекол', 'Перчатки'])]


def records_payload(manage):
    return {
        'user': {'name': 'Миша', 'login': '@employee0', 'active': True}, 'viewer': {'login': '@employee0'},
        'can_manage': manage, 'summary': {'earned': 12, 'total': 31, 'bronze': 6, 'silver': 4, 'gold': 2, 'diamond': 0},
        'records': [{'title': title, 'value_label': value, 'holders': [{'name': 'Миша', 'login': '@employee0'}], 'context': 'За всё время'} for title, value in [('Больше всего смен', '420 смен'), ('Лучший KPI', '156%'), ('Отзывы за месяц', '48 отзывов'), ('Инициативы', '32'), ('Анкеты', '125'), ('Продления', '86')]],
        'categories': [{'title': 'Рабочие достижения', 'achievements': [
            {'title': title, 'description': 'Результат за всё время работы в команде.', 'tier': {'key': 'silver', 'label': 'Серебро', 'icon': '🥈'},
             'value_label': '50', 'next_label': '100', 'progress': .5, 'level': 2,
             'thresholds': [{'level': j, 'icon': icon, 'value_label': str(j * 25)} for j, icon in enumerate(['🥉', '🥈', '🥇', '💎'], 1)]}
            for title in ['Опытный администратор', 'Любимчик гостей', 'Инициативный', 'Мастер продлений', 'Анкетолог', 'На высоте']]}],
        'team': [{'login': row['login'], 'name': row['nickname'], 'active': True, 'total': 31, 'earned': 12, 'score': 25,
                  'bronze': 6, 'silver': 4, 'gold': 2, 'diamond': 0} for row in EMPLOYEES],
    }


def main():
    output = Path(tempfile.mkdtemp(prefix='omg-modules-'))
    role, original = [3], [False]
    errors, writes, assets, old_files = [], [], {}, {}
    config = {'clubs': [{'name': club, 'actions': {action: [{'questions': [
        {'id': 'cash', 'text': 'Сколько денег в кассе?', 'type': 'num', 'checklist': 'Проверить кассу'},
        {'id': 'photo', 'text': 'Сфотографируйте оборудование', 'type': 'photo', 'checklist': 'Проверить зарядку шлемов'},
    ]}] for action in ['open', 'close']}} for club in CLUBS]}

    def route(route):
        url = urlsplit(route.request.url)
        if url.hostname != 'omg.test':
            if url.hostname in ['fonts.googleapis.com', 'fonts.gstatic.com', 'fonts.cdnfonts.com']:
                key = route.request.url
                if key not in assets:
                    try:
                        assets[key] = route.fetch(timeout=8000)
                    except Exception:
                        assets[key] = None
                if assets[key] is not None:
                    route.fulfill(response=assets[key])
                else:
                    route.fulfill(body='', content_type='text/css')
            else:
                route.abort()
            return
        path, params = url.path, parse_qs(url.query)
        manage = role[0] >= 2
        if path.startswith('/api/'):
            if route.request.method != 'GET':
                writes.append(path)
            if path == '/api/me':
                data = {'name': 'Паша' if manage else 'Миша', 'login': '@employee0', 'role': role[0],
                        'role_name': 'Владелец' if role[0] == 3 else 'Менеджер' if manage else 'Сотрудник',
                        'can_manage': manage, 'can_edit_settings': role[0] == 3}
            elif path == '/api/kpi':
                data = {'date': '2026-10-30', 'month': '2026-10', 'employees': EMPLOYEES, 'penalties': [],
                        'month_status': {'is_closed': False}, 'my_kpi': EMPLOYEES[0]}
            elif path == '/api/kpi/analytics':
                team = {'employees': 6, 'kpi': .92, 'weighted_kpi': 1.05, 'shifts': 60, 'weighted_shifts': 66, 'zones': {'🟢': 3, '🟡': 3, '🔴': 0}}
                data = {'mode': 'daily', 'employees': EMPLOYEES, 'points': [{'label': f'2026-10-{i:02}', 'team': team, 'employees': []} for i in range(1, 16)]}
            elif path == '/api/kpi/details':
                data = {'entries': []}
            elif path == '/api/kpi/settings':
                data = {'metrics': [{'metric': name, 'value': .1 if name == 'Инициативы' else 1, 'kind': 'initiative_bonus' if name == 'Инициативы' else 'per_shift'} for name in ['Отзывы', 'Анкеты', 'Продления', 'Сертификаты', 'Абонементы', 'Инициативы']],
                        'clubs': [{'club': club, 'weekday_weight': 1, 'weekend_weight': 1.2} for club in CLUBS], 'custom_goals': []}
            elif path == '/api/records':
                data = records_payload(manage)
            elif path == '/api/shift':
                dashboard = home_payload(3)['task_dashboard']
                dashboard['groups'] = [{'title': title, 'completed': 1, 'total': 5, 'clubs': [task for task in SHIFT_TASKS if task['title'] == title]} for title in ['Проверка оборудования', 'Уборка ресепшена', 'Подготовка лаунжа']]
                data = {'user_name': 'Паша' if manage else 'Миша', 'role_name': 'Руководство' if manage else 'Сотрудник', 'can_manage': manage,
                        'external_url': 'https://example.test/shift', 'shift_report_available': not manage, 'can_select_report_club': False,
                        'task_dashboard': dashboard,
                        'employee_dashboard': None if role[0] == 3 else {'today': '2026-10-30', 'upcoming_shifts': [{'club': CLUBS[i], 'date': f'2026-11-0{i+1}', 'start': '10:00', 'end': '00:00', 'duration': 14} for i in range(3)]},
                        'owner_dashboard': {'date': '2026-10-30', 'opened': 4, 'total_clubs': 5, 'late': 1, 'reviews': 3, 'tasks': dashboard,
                                            'clubs': [{'club': club, 'scheduled': True, 'state': 'completed', 'opened_at': '2026-10-30T09:30:00', 'opened_by': 'Миша', 'late_minutes': 0} for club in CLUBS]} if manage else None}
            elif path == '/api/shift/overview':
                data = {'date': '2026-10-30', 'history': [], 'today': [] if role[0] == 3 else [
                    {'club': 'Каширка', 'date': '2026-10-30', 'start': '10:00', 'end': '00:00', 'duration': 14,
                     'club_status': 'Открыт', 'on_shift': ['Миша', 'Мося'], 'report_available': True,
                     'reports': {}, 'bookings_available': False}]}
            elif path == '/api/shift/schedule':
                data = {'week_start': '2026-10-26', 'week_end': '2026-11-01', 'source': 'omg_shift', 'days': [
                    {'date': f'2026-10-{i+26}', 'locations': [{'club': CLUBS[j], 'shifts': [
                        {'employee': 'Миша', 'telegram': '@employee0', 'start': '10:00', 'end': '00:00', 'duration': 14, 'is_mine': True, 'is_free': False},
                        {'employee': 'Мося', 'start': '12:00', 'end': '22:00', 'duration': 10, 'is_mine': False, 'is_free': False},
                    ]} for j in range(2)]} for i in range(5)]}
            elif path == '/api/shift/tasks':
                data = {'tasks': SHIFT_TASKS, 'summary': {'active': 15, 'overdue': 0, 'completed': 3}, 'can_manage': manage, 'clubs': CLUBS}
            elif path.endswith('/report'):
                data = deepcopy(SHIFT_TASKS[0])
            elif path == '/api/shift/consumables':
                data = {'clubs': CLUBS, 'selected_club': params.get('club', ['Каширка'])[0], 'can_manage': manage, 'items': CATALOG,
                        'summary': {'active': 8, 'low': 1, 'archived': 2}, 'categories': [
                            {'id': 1, 'slug': 'drinks', 'emoji': '🥤', 'name': 'Напитки'}, {'id': 2, 'slug': 'cleaning', 'emoji': '🧼', 'name': 'Уборка'}]}
            elif path == '/api/shift-config':
                if route.request.method == 'PUT':
                    config.update(json.loads(route.request.post_data))
                data = config
            elif path == '/api/shift-config/history':
                data = {'versions': [{'id': 1, 'created_at': '2026-10-30T10:00:00', 'created_by': 'Паша', 'action': 'initial_import', 'version': '1', 'active': True}]}
            elif path == '/api/shift-test/pending':
                data = {'reports': []}
            elif path == '/api/shift-test/scenario':
                data = {'requires_club_selection': True, 'clubs': CLUBS, 'action': 'open'}
            else:
                data = {'tasks': [], 'items': []}
            route.fulfill(json=data)
            return
        pages = {'/kpi': 'index.html', '/shift': 'shift.html', '/records': 'records.html', '/shift/consumables': 'shift_consumables.html',
                 '/shift/tasks': 'shift_tasks.html', '/shift-config': 'shift_config.html', '/shift-report': 'shift_test.html'}
        name = pages.get(path, path.removeprefix('/static/'))
        file = ROOT / 'kpi_static' / name
        if not file.is_file():
            route.fulfill(status=404)
            return
        body = file.read_bytes()
        if original[0]:
            if name not in old_files:
                result = subprocess.run(['git', 'show', f'HEAD:kpi_static/{name}'], cwd=ROOT, capture_output=True)
                old_files[name] = result.stdout if result.returncode == 0 else body
            body = old_files[name]
        route.fulfill(body=body, content_type={'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript'}.get(file.suffix, 'text/plain'))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(reduced_motion='reduce')
        context.route('**/*', route)
        context.add_init_script("""window.Telegram={WebApp:{platform:new URLSearchParams(location.search).get('client') || 'android',initData:'demo',initDataUnsafe:{user:{id:1}},ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},onEvent(){},isVersionAtLeast(){return true},requestFullscreen(){window.fullscreenRequests=(window.fullscreenRequests||0)+1},BackButton:{show(){},hide(){},onClick(){}},enableClosingConfirmation(){},disableClosingConfirmation(){}}};""")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def snapshot(name):
            page.evaluate('document.fonts.ready')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'Overflow: {name}'
            page.screenshot(path=str(output / f'{name}.png'), full_page=True)
            print(name, flush=True)

        # Initial narrow desktop window must offer fullscreen before any resize.
        page.set_viewport_size({'width': 450, 'height': 760})
        page.goto('http://omg.test/kpi?client=tdesktop')
        page.locator('#desktopLaunchFullscreen').click()
        assert page.evaluate('window.fullscreenRequests') == 1
        assert page.locator('.desktop-sidebar').is_hidden()
        snapshot('desktop-initial-450')
        page.goto('http://omg.test/kpi?client=weba')
        page.locator('#desktopLaunchFullscreen').click()
        assert page.evaluate('window.fullscreenRequests') == 1
        page.set_viewport_size({'width': 1366, 'height': 768})
        page.locator('#desktopFullscreen').click()
        assert page.evaluate('window.fullscreenRequests') == 2
        for width, height in SIZES:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('http://omg.test/kpi')
            page.wait_for_function('state.employees.length === 6')
            page.locator('[data-view="rating"]').click()
            page.locator('.employee-card').first.click()
            page.locator('#employeeDialog').wait_for(state='visible')
            assert page.locator('#employeeDialog').evaluate('(node) => node.matches(":modal")') == (width < 1024)
            if width >= 1024:
                page.evaluate('window.scrollTo(0, 0)')
                tabs = page.locator('.view-tabs').bounding_box()
                period = page.locator('.period-panel').bounding_box()
                summary = page.locator('#summary').bounding_box()
                pane = page.locator('#kpiEmployeePane').bounding_box()
                controls = page.locator('.rating-controls').bounding_box()
                listing = page.locator('#employeeList').bounding_box()
                assert abs(tabs['y'] - period['y']) < 2
                assert period['x'] + period['width'] < tabs['x']
                assert abs(summary['x'] - period['x']) < 2
                assert abs(summary['width'] - period['width']) < 2
                assert abs(tabs['y'] + tabs['height'] - summary['y'] - summary['height']) < 2
                assert abs(pane['y'] - controls['y']) < 2, (pane, controls)
                assert controls['x'] + controls['width'] <= listing['x'] + listing['width'] + 2
                buttons = page.locator('.view-tab:visible').evaluate_all('(nodes) => nodes.map(node => ({x:node.offsetLeft,y:node.offsetTop}))')
                assert buttons[0]['y'] == buttons[1]['y'] and buttons[2]['y'] > buttons[0]['y']
                search = page.locator('.rating-search').bounding_box()
                assert listing['y'] - search['y'] - search['height'] <= 36, 'No empty filter row when collapsed'
            snapshot(f'kpi-rating-{width}')
            page.keyboard.press('Escape')
            page.locator('#employeeDialog').wait_for(state='hidden')
            for path, ready, name in [
                ('/shift', '#ownerOpenedCount', 'shift'), ('/records', '#recordGrid .record-card', 'records'),
                ('/shift/tasks', '#taskList .task-group', 'tasks'), ('/shift/consumables', '.consumable-card', 'consumables'),
                ('/shift-config', '#questionList .edit-row', 'config'), ('/shift-report?action=open', '#ownerClubStage button', 'report'),
            ]:
                page.goto('http://omg.test' + path)
                page.locator(ready).first.wait_for(state='visible')
                if name == 'shift':
                    page.wait_for_function('scheduleState.data !== null')
                    page.locator('#shiftSchedule').evaluate('(node) => { node.open = true; }')
                    page.evaluate("""() => {
                        const shift = {employee:'Тест', start:'10:00', end:'18:00', duration:8};
                        const empty = {date:'2026-10-26', locations:[{club:'Другая Смена', shifts:[]}]};
                        if (renderScheduleByClubs([empty]).includes('schedule-club')) throw new Error('Empty club rendered');
                        const populated = {date:'2026-10-27', locations:[{club:'Другая Смена', shifts:[shift]}]};
                        const html = renderScheduleByClubs([empty, populated]);
                        if (!html.includes('Другая Смена') || (html.match(/class="schedule-location"/g) || []).length !== 1) {
                            throw new Error('Actual shifts must remain, empty dates must not render');
                        }
                    }""")
                snapshot(f'{name}-{width}')

        # Exercise desktop tabs and responsive employee details with pending input.
        page.set_viewport_size({'width': 1920, 'height': 1080})
        page.goto('http://omg.test/kpi')
        page.wait_for_function('state.employees.length === 6')
        page.locator('[data-view="rating"]').click()
        page.locator('#managerFiltersToggle').click()
        filters = page.locator('#managerFilters').bounding_box()
        listing = page.locator('#employeeList').bounding_box()
        assert filters and filters['y'] + filters['height'] <= listing['y']
        page.locator('#managerFiltersToggle').click()
        assert page.locator('#managerFilters').is_hidden()
        page.locator('#searchInput').fill('Мося')
        assert page.locator('.employee-card').count() == 1
        page.locator('.employee-card').press('Enter')
        assert page.locator('#dialogName').text_content() == 'Мося'
        page.set_viewport_size({'width': 390, 'height': 844})
        page.wait_for_function('document.querySelector("#employeeDialog").matches(":modal")')
        page.set_viewport_size({'width': 1920, 'height': 1080})
        page.wait_for_function('!document.querySelector("#employeeDialog").matches(":modal")')
        page.locator('#addPenalty').click()
        page.locator('#penaltyReason').fill('Текст остаётся при изменении окна')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('#penaltyReason').input_value() == 'Текст остаётся при изменении окна'
        page.locator('#penaltyDialog .close-dialog').click()
        page.wait_for_function('document.querySelector("#employeeDialog").matches(":modal")')
        page.set_viewport_size({'width': 1920, 'height': 1080})
        page.wait_for_function('!document.querySelector("#employeeDialog").matches(":modal")')
        page.keyboard.press('Escape')
        page.locator('[data-view="analytics"]').click()
        page.locator('#analyticsChartArea svg').wait_for()
        controls = page.locator('.analytics-controls').bounding_box()
        chart = page.locator('.chart-card').bounding_box()
        assert controls['x'] + controls['width'] < chart['x']
        assert page.locator('#analyticsChartArea').bounding_box()['height'] <= 560
        summary = page.locator('#analyticsSummary').bounding_box()
        assert summary['y'] - max(controls['y'] + controls['height'], chart['y'] + chart['height']) <= 40
        snapshot('kpi-analytics-1920')
        page.locator('[data-view="settings"]').click()
        page.locator('#settingsMetrics input').first.wait_for()
        assert page.locator('.view-tabs').bounding_box()['x'] > page.locator('.period-panel').bounding_box()['x']
        assert page.locator('#dateDisplay').text_content() == 'Цели и нормы'
        snapshot('kpi-settings-1920')
        page.locator('[data-view="my"]').click()
        assert page.locator('#myKpiTab').text_content() == 'Команда'
        assert page.locator('#freshness').evaluate('(node) => node.parentElement.classList.contains("period-panel")')
        assert page.locator('.freshness-card strong').evaluate('(node) => parseFloat(getComputedStyle(node).fontSize)') >= 14
        assert page.locator('#freshness').bounding_box()['width'] < page.locator('#myView').bounding_box()['width'] * .8
        snapshot('kpi-owner-1920')
        role[0] = 0
        page.goto('http://omg.test/kpi')
        page.wait_for_function('state.myKpi !== null')
        page.locator('[data-view="my"]').click()
        assert page.locator('#settingsTab').is_hidden()
        assert page.locator('#myKpiTab').text_content() == 'Мой KPI'
        assert page.locator('#kpiExplanation').evaluate('(node) => node.open')
        assert page.locator('#freshness').evaluate('(node) => node.parentElement.classList.contains("period-panel")')
        assert page.locator('[data-view="analytics"]').evaluate('(node) => getComputedStyle(node).gridColumnEnd') == '-1'
        assert page.locator('.freshness-card strong').evaluate('(node) => parseFloat(getComputedStyle(node).fontSize)') >= 14
        assert page.locator('.view-tab:visible').first.evaluate('(node) => parseFloat(getComputedStyle(node).fontSize)') >= 18
        assert page.locator('#freshness').bounding_box()['width'] < page.locator('#myView').bounding_box()['width'] * .8
        snapshot('kpi-personal-1920')
        page.goto('http://omg.test/shift')
        page.locator('#shiftToday .today-shift-card').wait_for()
        snapshot('shift-employee-1920')
        assert page.locator('#openShiftConfig').is_hidden()
        page.goto('http://omg.test/records')
        page.locator('#recordGrid .record-card').first.wait_for()
        page.locator('[data-view="achievements"]').click()
        snapshot('records-achievements-1920')
        assert page.locator('#teamTab').is_hidden()
        role[0] = 3
        page.goto('http://omg.test/shift-config')
        page.locator('#questionList textarea').first.fill('Изменённый вопрос для предпросмотра')
        page.locator('#previewButton').click()
        page.get_by_text('Изменённый вопрос для предпросмотра', exact=False).wait_for()
        snapshot('config-preview-1920')
        page.locator('#closePreview').click()
        page.locator('#saveButton').click()
        page.wait_for_function('!dirty')
        assert '/api/shift-config' in writes

        # Mobile geometry must match HEAD for all newly adapted modules.
        page.set_viewport_size({'width': 390, 'height': 844})
        for path, ready, selector in [
            ('/kpi', '#myKpi .owner-dashboard', '.topbar,.view-tabs,.period-panel,#myKpi'),
            ('/shift', '#ownerOperations:not([hidden])', '.shift-header,#ownerOperations,#shiftSchedule,.shift-action'),
            ('/records', '#recordGrid .record-card', '.records-header,.records-hero,.records-tabs,.record-card'),
            ('/shift/consumables', '.consumable-card', '.consumables-header,.consumables-overview,.consumable-card'),
            ('/shift/tasks', '.task-group', '.task-header,.task-summary,.task-group'),
            ('/shift-config', '.edit-row', 'header,.toolbar,.variant-bar,.edit-row'),
        ]:
            shapes = []
            for baseline in [False, True]:
                original[0] = baseline
                page.goto('http://omg.test' + path)
                page.locator(ready).first.wait_for(state='visible')
                page.evaluate('document.fonts.ready')
                shapes.append(page.evaluate('selector => [...document.querySelectorAll(selector)].map(node => { const r=node.getBoundingClientRect(); return [r.width,r.height].map(Math.round); })', selector))
            assert shapes[0] == shapes[1], f'Mobile geometry changed: {path}: {shapes}'
            print('Mobile baseline matches:', path, flush=True)
        assert not errors, errors
        browser.close()
    print(f'All module checks passed. Screenshots: {output}')


if __name__ == '__main__':
    main()
