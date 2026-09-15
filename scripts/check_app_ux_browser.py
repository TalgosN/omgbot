"""Browser smoke checks with isolated storage and mocked APIs; no production writes.

Run: python scripts/check_app_ux_browser.py (requires playwright and Edge).
"""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
TASK = {
    'id': 11, 'template_id': 1, 'club': 'Каширка', 'title': 'Проверка чистоты',
    'date': '2026-09-15', 'available_at': '2026-09-15 10:00:00',
    'due_at': '2026-09-15 22:00:00', 'status': 'in_progress',
    'requirements': ['Ресепшен'], 'required_attachments': 1,
    'can_execute': True, 'media': [],
}


def main():
    errors = []
    requests = []
    actor = {'login': '@tester', 'name': 'Тестер', 'role_name': 'Менеджер', 'can_manage': True}
    task = dict(TASK)
    fail_upload = [True]

    def route(request):
        url = request.request.url
        if not url.startswith('http://omg.test/'):
            request.abort()
            return
        path = url.split('http://omg.test', 1)[1].split('?', 1)[0]
        if path.startswith('/api/'):
            if path == '/api/me':
                data = actor
            elif path == '/api/kpi':
                params = parse_qs(urlsplit(url).query)
                date = params.get('date', ['2026-09-15'])[0]
                data = {'date': date, 'month': date[:7], 'employees': [], 'penalties': [], 'my_kpi': None}
            elif path == '/api/shift/tasks':
                data = {'tasks': [task], 'can_manage': True, 'clubs': ['Каширка'], 'summary': {}}
            elif path.endswith('/report'):
                data = task
            elif path.endswith('/complete'):
                requests.append(path)
                if fail_upload[0]:
                    request.abort('failed')
                    return
                task.update(status='completed', can_execute=False, completed_by_name='Тестер')
                data = {'id': task['id'], 'status': 'completed'}
            elif path == '/api/problems-meta':
                data = {'clubs': ['Каширка'], 'repair_clubs': ['Каширка'], 'types': ['Общее', 'Ремонт'], 'can_process': True}
            elif path == '/api/problems':
                if request.request.method == 'POST':
                    requests.append(path)
                    request.abort('failed')
                    return
                data = {'tasks': [], 'counts': {'work': 0, 'review': 0}}
            elif path == '/api/repairs/catalog':
                data = {'items': [{'id': 1, 'name': 'Шлем', 'active': True, 'details': [{'id': 2, 'name': 'Кабель', 'active': True}]}], 'locations': [{'id': 3, 'name': 'Зона 1', 'active': True}]}
            else:
                data = {'tasks': []}
            request.fulfill(json=data)
            return
        pages = {'/shift/tasks': 'shift_tasks.html', '/problems': 'problems.html', '/kpi': 'index.html'}
        name = pages.get(path, path.removeprefix('/static/'))
        file = ROOT / 'kpi_static' / name
        if not file.is_file():
            request.fulfill(status=404)
            return
        mime = {'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html'}.get(file.suffix, 'text/plain')
        request.fulfill(body=file.read_bytes(), content_type=mime)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 360, 'height': 740})
        context.add_init_script("window.Telegram={WebApp:{initData:'test',initDataUnsafe:{user:{id:1}},ready(){},expand(){},setHeaderColor(){},setBackgroundColor(){},BackButton:{show(){},onClick(){}},HapticFeedback:{notificationOccurred(){}}}};")
        context.route('**/*', route)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://omg.test/shift/tasks?task=11')
        page.wait_for_function("document.querySelector('#taskDialog').open && !restoringTask")
        # Compile every script independently, including scripts with top-level await.
        for script in (ROOT / 'kpi_static').glob('*.js'):
            page.evaluate('(source) => { const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor; new AsyncFunction(source); }', script.read_text(encoding='utf-8'))
        sources = {file.name: file.read_text(encoding='utf-8') for file in (ROOT / 'kpi_static').glob('*.js')}
        recovery_tests = (ROOT / 'tests' / 'app_recovery.test.js').read_text(encoding='utf-8')
        print(page.evaluate('(sources) => { ' + recovery_tests + '; return testAppRecovery(sources); }', sources))
        shift_tests = (ROOT / 'tests' / 'shift_report_ui.test.js').read_text(encoding='utf-8')
        print(page.evaluate('(source) => { ' + shift_tests + '; return testShiftReportUi(source); }', sources['shift_test.js']))
        page.locator('#taskFileInput').set_input_files({'name': 'report.png', 'mimeType': 'image/png', 'buffer': b'photo-test'})
        page.get_by_text('Черновик сохранён на этом устройстве', exact=True).wait_for()
        page.reload()
        page.get_by_text('Черновик восстановлен · фото и причина сохранены', exact=True).wait_for()
        assert page.locator('.attachment-row').count() == 1
        assert page.locator('#taskDialog').evaluate('(element) => element.getBoundingClientRect().width <= innerWidth && element.getBoundingClientRect().height <= innerHeight')
        page.locator('#completeTask').click()
        page.locator('#dialogError:not([hidden])').wait_for()
        assert page.locator('.attachment-row').count() == 1
        assert len(requests) == 1
        fail_upload[0] = False
        page.locator('#completeTask').click()
        page.wait_for_function("!document.querySelector('#taskDialog').open")
        assert page.evaluate("OmgApp.drafts.get('task:11')") is None

        page.goto('http://omg.test/problems')
        page.wait_for_function('state.me && !restoringProblem')
        page.locator('#newProblem').click()
        page.locator('#createType').select_option('Ремонт')
        page.locator('#createClub').select_option('Каширка')
        page.locator('#repairItem').select_option('1')
        page.locator('#repairDetail').select_option('2')
        page.locator('#repairLocations input').check()
        page.locator('[name="description"]').fill('Проверка сохранения черновика')
        page.locator('#problemMediaFile').set_input_files({'name': 'problem.png', 'mimeType': 'image/png', 'buffer': b'photo-test'})
        page.get_by_text('Черновик сохранён на этом устройстве', exact=True).wait_for()
        page.reload()
        page.get_by_role('button', name='Продолжить черновик').click()
        assert page.locator('#repairItem').input_value() == '1'
        assert page.locator('#repairDetail').input_value() == '2'
        assert page.locator('#repairLocations input').is_checked()
        assert page.locator('[name="description"]').input_value() == 'Проверка сохранения черновика'
        assert page.locator('#problemMediaPreview img').count() == 1
        assert page.locator('#createDialog').evaluate('(element) => element.getBoundingClientRect().width <= innerWidth && element.getBoundingClientRect().height <= innerHeight')
        page.evaluate("document.querySelector('#createDialog').close(); document.querySelector('#clubFilter').value='Каширка'; document.querySelector('#typeFilter').value='Ремонт'; state.status='done'; saveProblemView()")
        page.reload()
        page.wait_for_function("state.status === 'done' && !document.querySelector('#newProblem').disabled")
        assert page.locator('#clubFilter').input_value() == 'Каширка'
        assert page.locator('#typeFilter').input_value() == 'Ремонт'
        page.goto('http://omg.test/problems?status=review')
        page.wait_for_function("state.status === 'review' && !document.querySelector('#newProblem').disabled")
        assert page.locator('#clubFilter').input_value() == ''
        assert page.locator('#typeFilter').input_value() == ''
        page.locator('#newProblem').click()
        # Busy forms reject swipe-close and a second click.
        page.evaluate("OmgApp.busy(document.querySelector('#createDialog'), true); OmgSwipeNavigation.goBack()")
        assert page.locator('#createDialog').evaluate('(element) => element.open')
        page.evaluate("document.querySelector('#createForm button[type=submit]').click()")
        assert '/api/problems' not in requests
        page.evaluate("OmgApp.busy(document.querySelector('#createDialog'), false)")
        page.locator('#createForm button[type=submit]').click()
        page.wait_for_function('!submittingProblem')
        assert page.locator('[name="description"]').input_value() == 'Проверка сохранения черновика'
        assert page.locator('#problemMediaPreview img').count() == 1
        context.set_offline(True)
        page.locator('#omgConnectionStatus').wait_for(state='visible')
        context.set_offline(False)
        page.locator('#omgConnectionStatus').wait_for(state='hidden')
        page.evaluate("() => { OmgApp.drafts.put = () => Promise.reject(new Error('quota')); }")
        page.locator('[name="description"]').fill('Данные остаются в форме даже при сбое хранилища')
        page.locator('#problemDraftStatus.error').wait_for()
        assert page.locator('#problemMediaPreview img').count() == 1
        # Drafts are scoped by authenticated viewer/preview, not just the task id.
        actor['login'] = '@another'
        page.reload()
        page.wait_for_function('state.me?.login === "@another" && !restoringProblem')
        assert page.locator('[name="description"]').input_value() == ''
        assert page.evaluate("OmgApp.drafts.get('problem')") is None
        page.goto('http://omg.test/kpi')
        page.wait_for_function("state.me && document.querySelector('#summary').children.length === 3")
        page.evaluate("state.day='2026-08-20'; state.sort='name'; document.querySelector('#searchInput').value='Миша'; setKpiView('rating'); saveKpiView()")
        page.reload()
        page.wait_for_function("document.querySelector('.view-tab.active')?.dataset.view === 'rating'")
        assert page.locator('#datePicker').input_value() == '2026-08-20'
        assert page.locator('#analyticsMonth').input_value() == '2026-08'
        assert page.locator('#sortSelect').input_value() == 'name'
        assert page.locator('#searchInput').input_value() == 'Миша'
        page.goto('http://omg.test/shift/tasks')
        page.wait_for_function('taskListReady')
        page.locator('[data-scope="history"]').click()
        page.wait_for_function("state.scope === 'history' && !document.querySelector('.task-empty')")
        page.reload()
        page.wait_for_function('taskListReady')
        assert page.locator('.task-tabs .active').get_attribute('data-scope') == 'history'
        assert not errors, errors
        browser.close()
    print('Browser checks passed: syntax, mobile dialog bounds, draft recovery, failed upload/retry, cleanup, repair fields, busy navigation, account isolation, offline/storage warnings, KPI and task filters, direct links.')


if __name__ == '__main__':
    main()
