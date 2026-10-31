"""Desktop layout and interaction checks using isolated demo APIs, never production.

Run: python scripts/check_desktop_ui.py (requires Playwright and Microsoft Edge).
Screenshots are saved to a temporary directory printed after successful checks.
"""
from copy import deepcopy
from pathlib import Path
import json
import re
import subprocess
import tempfile
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SIZES = [(390, 844), (768, 1024), (1024, 768), (1366, 768), (1920, 1080), (2560, 1440)]
CLUBS = ['Дмитровка', 'Каширка', 'Ленинский', 'Марьино', 'Алексеевская']
TASKS = [{
    'id': index + 1, 'title': title, 'club': CLUBS[index % len(CLUBS)],
    'type': 'Ремонт' if index % 2 == 0 else 'Общее обращение',
    'date': '2026-10-29', 'status': 'На проверке' if index == 1 else 'В работе',
    'has_photo': index == 0, 'has_video': False,
    'description': 'После включения устройство теряет соединение. Проверили питание и кабель. Требуется диагностика и замена неисправной детали.',
    'feedback': 'Кабель заменили, проверяем стабильность подключения.' if index == 1 else '',
    'comments': [{'actor': {'name': 'Миша'}, 'message': 'Проверил на смене — проблема повторяется.', 'created_at': '2026-10-30T10:30:00'}],
    'activity': [{'event_type': 'created', 'event_at': '2026-10-29T12:30:00', 'actor': {'name': 'Мося'}}],
} for index, title in enumerate([
    'Шлем — пропадает изображение в зоне 2', 'Заменить кабель контроллера',
    'Протекает радиатор в общем зале', 'Не работает подсветка у ресепшена',
    'Проверить кондиционер в лаунже', 'Обновить информацию на стойке',
    'Контроллер не подключается к PlayStation', 'Повреждена зарядная станция',
])]


def home_payload(role):
    shifts = [{'club': CLUBS[index % 5], 'date': f'2026-11-0{index + 1}', 'duration': 8} for index in range(4)]
    groups = [{
        'title': title, 'completed': index, 'total': 5, 'overdue': index == 0,
        'clubs': [{'id': 40 + index * 5 + i, 'club': club, 'status': 'completed' if i < index else 'pending',
                   'due_at': '2026-10-30T18:00:00', 'can_view_report': i < index,
                   'completed_by_name': 'Миша', 'overdue': False} for i, club in enumerate(CLUBS)],
    } for index, title in enumerate(['Уборка ресепшена', 'Проверка оборудования', 'Подготовка лаунжа'])]
    management = {
        'average_pct': .86, 'participants': 18, 'red_zone': 3, 'active_penalties': 2,
        'problems': {'work': 24, 'review': 3},
    } if role >= 2 else None
    return {
        'role': role, 'personal_kpi': {'total_pct': .92, 'shifts': 14},
        'upcoming_shifts': shifts if role != 3 else [], 'management': management,
        'clubs': [{
            'club': club, 'status': 'Закрыт' if i == 4 else 'Открыт',
            'problems': {'work': 3 + i, 'review': i % 2},
            'on_shift': ['Миша', 'Мося'],
            'on_shift_contacts': [{'name': 'Миша', 'login': '@mishatest'}, {'name': 'Мося', 'login': '@mosyatest'}],
        } for i, club in enumerate(CLUBS)] if management else [],
        'task_dashboard': {'total': 15, 'completed': 3, 'overdue': 1, 'groups': groups},
        'shift_tasks': {'count': 2, 'overdue': 0, 'items': [
            {'id': 1, 'title': 'Уборка ресепшена', 'club': 'Каширка', 'due_at': '2026-10-30T18:00:00', 'status': 'pending'},
            {'id': 2, 'title': 'Проверка оборудования', 'club': 'Каширка', 'due_at': '2026-10-30T19:00:00', 'status': 'in_progress'},
        ]},
    }


def main():
    output = Path(tempfile.mkdtemp(prefix='omg-desktop-'))
    errors, writes = [], []
    actor = {'login': '@pasha', 'name': 'Паша', 'role_name': 'Владелец', 'can_preview_employee': False}
    role = [3]
    callcenter = [False]
    tasks = deepcopy(TASKS)
    baseline = [False]
    baseline_files = {}
    font_assets = {}
    deferred_id, pending_details = [None], []

    def route(route):
        url = urlsplit(route.request.url)
        if url.hostname != 'omg.test':
            if url.hostname in ('fonts.googleapis.com', 'fonts.gstatic.com', 'fonts.cdnfonts.com'):
                asset_url = route.request.url
                if asset_url not in font_assets:
                    try:
                        font_assets[asset_url] = route.fetch(timeout=8000)
                    except Exception:
                        font_assets[asset_url] = None
                if font_assets[asset_url] is not None:
                    route.fulfill(response=font_assets[asset_url])
                else:
                    route.fulfill(body='', content_type='text/css' if url.path.endswith('/furore') or url.hostname == 'fonts.googleapis.com' else 'application/octet-stream')
            else:
                route.abort()
            return
        path = url.path
        if path.startswith('/api/'):
            if route.request.method != 'GET':
                writes.append((path, route.request.post_data))
            if path == '/api/me':
                data = actor
            elif path == '/api/home':
                data = home_payload(role[0])
                if callcenter[0]:
                    data['upcoming_shifts'] = [{'club': 'КЦ', 'date': '2026-11-01', 'duration': 8}]
                    data['shift_tasks'] = {'count': 0, 'items': []}
            elif path == '/api/bookings/today':
                booking = {'start': '2026-10-30T12:00:00+03:00', 'end': '2026-10-30T14:00:00+03:00',
                           'date': '2026-10-30', 'format': 'День рождения', 'participants': 8,
                           'club': 'Каширка', 'number': '1204', 'url': 'https://example.test/booking'}
                data = {'mode': 'callcenter' if callcenter[0] else 'management' if role[0] >= 2 else 'clubs', 'last_synced_at': '2026-10-30T12:00:00+03:00',
                        'stale': False, 'groups': [{'club': club, 'count': 3, 'participants': 24, 'bookings': [booking]} for club in CLUBS], 'bookings': [booking]}
            elif path == '/api/problems-meta':
                data = {'clubs': CLUBS, 'repair_clubs': CLUBS, 'types': ['Ремонт', 'Общее обращение'],
                        'can_process': role[0] >= 2, 'can_edit_repair_catalog': role[0] >= 2,
                        'can_view_equipment': role[0] >= 2, 'can_view_analytics': role[0] >= 2, 'can_export_analytics': role[0] >= 2}
            elif path == '/api/problems':
                status = {'work': 'В работе', 'review': 'На проверке', 'done': 'Выполнено'}.get(parse_qs(url.query).get('status', ['work'])[0])
                data = {'tasks': [task for task in tasks if task['status'] == status], 'counts': {'work': 7, 'review': 1}}
            elif path == '/api/problems/analytics':
                data = {'summary': {'created': 24, 'completed': 18, 'completion_rate': .75, 'average_seconds': 86400, 'precision': 'hour', 'open': 6},
                        'statuses': [{'key': 'work', 'label': 'В работе', 'count': 5, 'share': .2}, {'key': 'review', 'label': 'На проверке', 'count': 1, 'share': .05}, {'key': 'done', 'label': 'Выполнено', 'count': 18, 'share': .75}],
                        'types': [], 'clubs': [], 'trend': [], 'oldest_open': None}
            elif re.fullmatch(r'/api/problems/\d+', path):
                data = next(task for task in tasks if task['id'] == int(path.rsplit('/', 1)[1]))
                if data['id'] == deferred_id[0]:
                    pending_details.append((route, deepcopy(data)))
                    return
            elif re.fullmatch(r'/api/problems/\d+/comments', path):
                task = next(task for task in tasks if task['id'] == int(path.split('/')[3]))
                task['comments'].append({'message': json.loads(route.request.post_data)['message'], 'actor': {'name': 'Паша'}, 'created_at': '2026-10-30T12:00:00'})
                data = {'ok': True}
            elif path.endswith('/solution'):
                next(task for task in tasks if task['id'] == int(path.split('/')[3]))['status'] = 'На проверке'
                data = {'ok': True}
            elif path.endswith('/photo'):
                route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="640" height="240"><rect width="640" height="240" fill="#34204d"/><text x="240" y="130" fill="#cfa1f4" font-size="24">Фото заявки</text></svg>', content_type='image/svg+xml')
                return
            elif path == '/api/repairs/catalog':
                data = {'items': [{'id': 1, 'name': 'Шлем', 'active': True, 'details': [{'id': 2, 'name': 'Кабель', 'active': True}]}],
                        'locations': [{'id': 3, 'name': 'Зона 1', 'active': True}]}
            elif path == '/api/repairs/migration-review':
                data = {'summary': {'mapped': 0, 'duplicates': 0, 'tests': 0}, 'unmapped': []}
            elif path == '/api/equipment':
                data = {'units': []}
            else:
                data = {'tasks': []}
            route.fulfill(json=data)
            return
        name = {'/': 'home.html', '/problems': 'problems.html'}.get(path, path.removeprefix('/static/'))
        file = ROOT / 'kpi_static' / name
        if not file.is_file():
            route.fulfill(status=404)
            return
        body = file.read_bytes()
        if baseline[0]:
            if name not in baseline_files:
                result = subprocess.run(['git', 'show', f'HEAD:kpi_static/{name}'], cwd=ROOT, capture_output=True)
                baseline_files[name] = result.stdout if result.returncode == 0 else body
            body = baseline_files[name]
        route.fulfill(body=body, content_type={'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}.get(file.suffix, 'text/plain'))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 1366, 'height': 768}, reduced_motion='reduce')
        context.route('**/*', route)
        context.add_init_script("window.Telegram={WebApp:{initData:'demo',initDataUnsafe:{user:{id:1}},ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},onEvent(){},enableClosingConfirmation(){},disableClosingConfirmation(){}}};")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def check_width():
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'Page overflow at {page.url} {page.viewport_size}'

        def screenshot(name):
            page.evaluate('document.fonts.ready')
            check_width()
            page.screenshot(path=str(output / f'{name}.png'), full_page=True)
            print(name, flush=True)

        for width, height in SIZES:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('http://omg.test/')
            page.wait_for_function('homeData && !bookingsLoading')
            assert page.locator('#homeError').is_hidden(), page.locator('#homeError').text_content()
            screenshot(f'home-owner-{width}')
            if width >= 1024:
                header_heights = page.locator('.club-head').evaluate_all('(nodes) => nodes.map(node => node.getBoundingClientRect().height)')
                assert max(header_heights) - min(header_heights) < 2, 'Club headers must align regardless of name length'
                assert page.locator('.club-head').evaluate_all('''(nodes) => nodes.every(node => {
                    const title = node.querySelector('h3').getBoundingClientRect();
                    const statuses = node.querySelector('.club-live-statuses').getBoundingClientRect();
                    return title.right <= statuses.left && Math.abs((title.top + title.bottom - statuses.top - statuses.bottom) / 2) < 2
                        && node.scrollWidth <= node.clientWidth + 1;
                })'''), 'Club name and statuses must fit on one line without overlap'
            role[0] = 0
            actor.update(name='Миша', role_name='Сотрудник')
            page.reload()
            page.wait_for_function('homeData && !bookingsLoading')
            assert page.locator('#homeError').is_hidden(), page.locator('#homeError').text_content()
            screenshot(f'home-employee-{width}')
            role[0] = 3
            actor.update(name='Паша', role_name='Владелец')
            page.goto('http://omg.test/problems')
            page.wait_for_function('state.me && !document.querySelector("#newProblem").disabled')
            page.locator('.problem-card').first.click()
            page.locator('#detailDialog').wait_for(state='visible')
            page.locator('#detailPhoto').wait_for(state='detached')
            screenshot(f'taskboard-{width}')
            assert page.locator('#detailDialog').evaluate('(element) => element.matches(":modal")') == (width < 1024)
            if width >= 1024:
                assert page.locator('#problemList').is_visible()
                assert page.locator('#problemDetailPane #detailDialog').count() == 1
                filters = page.locator('.problem-filters').bounding_box()
                listing = page.locator('#problemList').bounding_box()
                pane = page.locator('#problemDetailPane').bounding_box()
                tabs = page.locator('#problemTabs').bounding_box()
                assert abs(filters['x'] - listing['x']) < 2
                assert filters['x'] + filters['width'] <= listing['x'] + listing['width'] + 2
                assert pane['y'] < tabs['y'], 'Detail should start beside the top controls'
                assert listing['y'] - (filters['y'] + filters['height']) <= 20, 'Filters must sit directly above the list'
                page.locator('#problemList .problem-card').nth(1).click()
                page.wait_for_function('state.selected?.id === 3')
                assert page.locator('.problem-card.selected').get_attribute('data-id') == '3'
            page.keyboard.press('Escape')
            page.locator('#detailDialog').wait_for(state='hidden')

        # Preserve in-progress comment and presentation when resizing an open detail.
        page.set_viewport_size({'width': 1366, 'height': 768})
        page.locator('.problem-card').first.click()
        page.locator('#taskCommentForm textarea').fill('Комментарий перед изменением размера')
        for width, height in [(390, 844), (1024, 768), (768, 1024), (1920, 1080)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.wait_for_function('(modal) => document.querySelector("#detailDialog").matches(":modal") === modal', arg=width < 1024)
            assert page.locator('#taskCommentForm textarea').input_value() == 'Комментарий перед изменением размера'
            assert page.locator('#detailDialog').evaluate('(element) => element.matches(":modal")') == (width < 1024)
            check_width()
        page.locator('#taskCommentForm button').click()
        page.get_by_text('Комментарий перед изменением размера', exact=True).wait_for()
        page.locator('[data-action="solution"]').click()
        page.locator('#actionMessage').fill('Кабель заменён, устройство проверено.')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.locator('#actionMessage').input_value() == 'Кабель заменён, устройство проверено.'
        page.locator('#messageDialog .close-dialog').click()
        page.wait_for_function('document.querySelector("#detailDialog").matches(":modal")')
        page.set_viewport_size({'width': 1920, 'height': 1080})
        page.wait_for_function('!document.querySelector("#detailDialog").matches(":modal")')
        page.locator('[data-action="solution"]').click()
        page.locator('#actionMessage').fill('Кабель заменён, устройство проверено.')
        page.locator('#messageSubmit').click()
        page.locator('#messageDialog').wait_for(state='hidden')
        assert any(path.endswith('/solution') for path, _ in writes)

        # A delayed response must not replace the newer selection.
        deferred_id[0] = 3
        page.locator('.problem-card[data-id="3"]').click()
        page.locator('.problem-card[data-id="4"]').click()
        page.wait_for_function('state.selected?.id === 4')
        assert pending_details
        for held, data in pending_details:
            held.fulfill(json=data)
        deferred_id[0] = None
        page.wait_for_function('state.selected?.id === 4 && document.querySelector("#detailDialog").open')
        assert page.locator('.problem-card.selected').get_attribute('data-id') == '4'
        page.keyboard.press('Escape')

        # Exercise nested pages and desktop forms without touching real data.
        page.locator('#newProblem').click()
        page.locator('#createType').select_option('Ремонт')
        page.locator('#createClub').select_option('Каширка')
        page.locator('#repairItem').select_option('1')
        page.locator('#repairLocations input').check()
        screenshot('taskboard-create-1920')
        page.locator('#createDialog .close-dialog').click()
        page.locator('[data-board-view="analytics"]').click()
        page.locator('#analyticsSummary article').first.wait_for()
        screenshot('taskboard-analytics-1920')
        page.locator('#repairCatalog').click()
        page.locator('#catalogItems').wait_for()
        screenshot('taskboard-catalog-1920')
        page.locator('#catalogDialog .close-dialog').click()
        page.locator('#equipmentMode').click()
        page.locator('#equipmentView').wait_for(state='visible')
        screenshot('taskboard-equipment-1920')

        # Role and empty states on the home dashboard.
        role[0] = 2
        actor.update(role_name='Менеджер')
        page.goto('http://omg.test/')
        page.wait_for_function('homeData && !bookingsLoading')
        screenshot('home-manager-1920')
        assert page.locator('#personalSection').is_visible() and page.locator('#managementSection').is_visible()
        role[0] = 0
        callcenter[0] = True
        actor.update(name='Настя', role_name='Сотрудник КЦ')
        page.reload()
        page.wait_for_function('homeData && !bookingsLoading')
        screenshot('home-callcenter-1920')
        assert page.locator('#managementSection').is_hidden()
        assert page.locator('#bookingsTitle').text_content() == 'Без предоплаты'
        callcenter[0] = False
        page.goto('http://omg.test/problems')
        page.wait_for_function('state.me && !document.querySelector("#newProblem").disabled')
        assert page.locator('#repairCatalog').is_hidden()
        assert page.locator('#boardViewTabs').is_hidden()
        page.locator('.problem-card').first.click()
        page.locator('#detailDialog').wait_for(state='visible')
        assert page.locator('#taskCommentForm').count() == 0
        assert page.locator('[data-action="solution"]').count() == 0
        page.keyboard.press('Escape')
        page.locator('#clubFilter').select_option('Каширка')
        page.locator('#typeFilter').select_option('Общее обращение')
        assert page.locator('.problem-card').count() == 0
        screenshot('taskboard-empty-1920')

        # At mobile widths, compare key geometry against the untouched HEAD.
        role[0] = 3
        actor.update(role_name='Владелец')
        page.set_viewport_size({'width': 390, 'height': 844})
        geometry = []
        for original in [False, True]:
            baseline[0] = original
            page.goto('http://omg.test/')
            page.wait_for_function('homeData && !bookingsLoading')
            page.evaluate('document.fonts.ready')
            geometry.append(page.evaluate('''() => [...document.querySelectorAll('.home-header,.welcome,.module-card,#clubsSection,#managementSection')].map(element => {
                const box = element.getBoundingClientRect(); return [box.x,box.y,box.width,box.height].map(Math.round);
            })'''))
        assert geometry[0] == geometry[1], f'Mobile home geometry changed: {geometry}'
        geometry = []
        for original in [False, True]:
            baseline[0] = original
            page.goto('http://omg.test/problems')
            page.wait_for_function('state.me && !document.querySelector("#newProblem").disabled')
            page.evaluate('document.fonts.ready')
            geometry.append(page.evaluate('''() => [...document.querySelectorAll('.problem-header,.header-actions,.problem-tabs,.problem-filters,.problem-card')].map(element => {
                const box = element.getBoundingClientRect(); return [box.x,box.y,box.width,box.height].map(Math.round);
            })'''))
        assert geometry[0] == geometry[1], f'Mobile Taskboard geometry changed: {geometry}'
        assert not errors, errors
        browser.close()
    print(f'Checks passed at all six sizes; mobile baseline geometry matches. Screenshots: {output}')


if __name__ == '__main__':
    main()
