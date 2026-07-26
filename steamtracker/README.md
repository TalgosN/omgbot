# Steam Tracker v2: лицензии и подготовка промо

Контур встроен в основной проект Виарыча и не изменяет legacy
`dump/steamtracker/main.py` или `steam_stats.db`. Большая legacy-база
используется только для переноса Steam AppID и аккаунтов.

## Что реализовано

- согласованный каталог игр с матчингом по Steam AppID;
- исключение `Pixel Dungeon VR: Prologue`;
- текущее наличие лицензии по каждому Steam-аккаунту;
- события только при добавлении или подтверждённом исчезновении лицензии;
- одна запись игрового времени в день вместо полных повторных снимков;
- защита от временной ошибки Steam API;
- три отдельных dry-run текста: сотрудникам, Telegram и VK;
- согласование и идемпотентный outbox без сетевой публикации.
- Steam Store-описания, жанры, категории и изображения для 89 игр;
- переключаемый `FakeGenerator`/OpenRouter с проверкой JSON;
- Telegram-согласование внутри Виарыча, выключенное по умолчанию.

## Установка и тесты

```powershell
pip install -r requirements.txt
python -m unittest tests.test_steamtracker_catalog `
  tests.test_steamtracker_license_sync `
  tests.test_steamtracker_promo_workflow `
  tests.test_steamtracker_sync_service `
  tests.test_steamtracker_stage2 -v
```

Тесты не требуют Steam, Telegram, VK или LLM-ключей.

## Первичная инициализация

Команда читает аккаунты и AppID из `steam_stats.db`, а согласованный список —
из листа `Игры` Google-таблицы:

```powershell
python tracker_cli.py init
python tracker_cli.py status
python tracker_cli.py export-availability
python tracker_cli.py enrich-store
```

Будет создана новая компактная база `db/steamtracker_v2.db`. Legacy-база
останется без изменений. Команда `export-availability` создаёт CSV-матрицу
всех 89 игр по 42 игровым зонам: AppID, клуб, зона, наличие лицензии и игровое
время.

Для полностью автономного запуска каталог можно передать CSV-файлом:

```powershell
python tracker_cli.py init --catalog-csv .\games.csv
```

CSV поддерживает колонки:

```text
steam_app_id,name,player_count,description
```

Если `steam_app_id` пока отсутствует, он безопасно берётся из legacy-базы.
Неизвестные игры останавливают импорт: система не придумывает AppID.

## Проверка промо-процесса без публикации

Целевая структура листа `Промо-план` сохранена в
`steamtracker/promo_plan_template.csv`. Рабочую Google-таблицу этот этап
автоматически не изменяет.

Пример использует Beat Saber и тестовую скидку:

```powershell
python tracker_cli.py create-promo `
  --app-id 620980 `
  --discount "ТЕСТ: 100 рублей" `
  --from 2026-08-01 `
  --to 2026-08-07

python tracker_cli.py generate 1
python tracker_cli.py approve 1 --by test-manager
python tracker_cli.py status
```

После согласования создаются три записи outbox со статусом
`ready_dry_run`. Никакие сообщения сотрудникам, Telegram или VK не
отправляются. Повторное согласование не создаёт дубли.

## Живое обновление лицензий

Эта команда является единственной операцией первого этапа, которой нужен
Steam API key:

```powershell
$env:STEAM_API_KEY="новый_ключ"
python tracker_cli.py sync-steam
```

Если конкретный аккаунт не ответил, его лицензии не меняются. Лицензия
помечается отсутствующей только после трёх успешных последовательных опросов,
в которых игра не найдена.

Для фонового запуска вместе с Виарычем:

```env
STEAM_API_KEY=...
STEAMTRACKER_SYNC_ENABLED=true
STEAMTRACKER_STORE_ENRICHMENT_ENABLED=true
STEAMTRACKER_CATALOG_SYNC_ENABLED=true
```

Лицензии проверяются раз в четыре часа, Steam Store — ежедневно в 05:30 по
Москве. Задания запускаются в отдельных потоках и не блокируют основной
планировщик бота. Повторный параллельный запуск не допускается.

При включённом `STEAMTRACKER_CATALOG_SYNC_ENABLED` Виарыч раз в пять минут
читает лист `Игры`. Менеджерские изменения применяются только при полной
валидации всех активных строк.

## OpenRouter

Без ключа продолжает работать безопасный шаблонный генератор:

```env
STEAMTRACKER_GENERATOR=fake
PUBLISH_MODE=dry_run
```

Когда будет получен новый ключ:

```env
STEAMTRACKER_GENERATOR=openrouter
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=
```

`OPENROUTER_MODEL` можно оставить пустым, чтобы использовать модель,
назначенную в аккаунте OpenRouter, либо указать проверенный slug. Ответ
принимается только как JSON с полями `employee`, `telegram` и `vk`. Во всех
трёх текстах проверяется точное значение скидки.

## Telegram-согласование

Обработчики уже подключены к Виарычу, но не регистрируются при выключенном
feature flag:

```env
STEAMTRACKER_TELEGRAM_APPROVAL_ENABLED=false
STEAMTRACKER_APPROVER_IDS=
```

Для теста в личном чате:

```env
STEAMTRACKER_TELEGRAM_APPROVAL_ENABLED=true
STEAMTRACKER_APPROVER_IDS=123456789
```

После перезапуска разрешённый пользователь вызывает:

```text
/steam_promo 1
```

Доступны согласование, полная перегенерация, отдельная перегенерация текста
сотрудникам или социальных анонсов и откладывание. Даже после согласования
создаётся только dry-run outbox.

## Управляющие Google-листы

Предварительный просмотр необходимых изменений:

```powershell
python steamtracker_cli.py setup-sheets
```

Команда ничего не записывает без явного флага. Создание недостающих листов и
добавление недостающих заголовков:

```powershell
python steamtracker_cli.py setup-sheets --apply
```

Существующие строки и колонки не очищаются. Для старого `Промо-плана`
заполняется пустой заголовок колонки C, старые тексты остаются на месте.

Проверка менеджерских изменений в листе `Игры`:

```powershell
python steamtracker_cli.py sync-catalog-sheets
```

Применение после успешной проверки:

```powershell
python steamtracker_cli.py sync-catalog-sheets --apply
```

Если хотя бы в одной активной строке неверный AppID, статус или количество
игроков, каталог целиком остаётся без изменений.

Менеджер добавляет игру строкой со статусом `Активна`, количеством игроков и
Steam AppID либо полной Steam-ссылкой. Новая игра проверяется через Steam
Store. Для временного отключения используется `Приостановлена`, для исключения
— `Исключена`. Физического удаления игры и её истории не происходит.

## Текущие ограничения

- отправка сотрудникам выключена;
- публикация в Telegram и VK выключена;
- запись обратно в Google Sheets пока не выполняется.
