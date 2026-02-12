import os
import json
from dotenv import load_dotenv

load_dotenv()

### Ключи и Токены
TELEGRAM_API_KEY = os.getenv("TELEGRAM_API_KEY")
FT_API_KEY = os.getenv("FT_API_KEY")
AQSI_API_KEY = os.getenv("AQSI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_KEY")

SHIFTON_CREDITNAILS = {
    "username": os.getenv("SHIFTON_USER"),
    "password": os.getenv("SHIFTON_PASS"),
    "client_id": os.getenv("SHIFTON_CLIENT_ID"),
    "client_secret": os.getenv("SHIFTON_CLIENT_SECRET"),
    "grant_type": "password",
    "scope": ""
}

### Лимит на сообщения
MESSAGE_LIMIT_TIME = int(os.getenv("MESSAGE_LIMIT_TIME", 20))

### Чаты для рассылки
CHATS = {
    "reports": os.getenv("CHAT_REPORTS"),
    "main_group": os.getenv("CHAT_MAIN_GROUP"),
    "repair_extra": os.getenv("CHAT_REPAIR_EXTRA"),
    "me": os.getenv("CHAT_ME")
}

### Фразы
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHRASES_PATH = os.path.join(BASE_DIR, "data", "phrases.json")
CLUBS_PATH = os.path.join(BASE_DIR, "data", "clubs.json")

with open(PHRASES_PATH, "r", encoding="utf-8") as f:
    TEXTS = json.load(f)

with open(CLUBS_PATH, "r", encoding="utf-8") as f:
    CLUBS = json.load(f)

col=3 # Пока не знаю что это

### Кнопки

## Доска проблем
funclist_task=("➕ Добавить", "⭕ Текущие", "🛠 Ремонт", "🤖 Улучшения бота", "✔ Выполненные")
messtype=("Вопрос/жалоба/предложение","Ремонт", "Улучшение бота")
taskto=("Обработать","Выбрать другое") 
statuses=("В работе", "Выполнено", "Отклонено")

## Главное меню
funclist={0:("👨🏻‍💻 Смена", "🚩 Доска проблем","🗓 Расписание", "👤 Аккаунт","🆘 Помощь"),
          1:("👨🏻‍💻 Смена", "🚩 Доска проблем","🗓 Расписание", "👤 Аккаунт","⚙️ Обновить настройки","🆘 Помощь"),
          2:("👨🏻‍💻 Смена", "🚩 Доска проблем","🗓 Расписание", "👤 Аккаунт","💲 Финансы","⚙️ Обновить настройки","🆘 Помощь")} # с привелегиями

## Финансы
funclist_fin=("📑 Отчет по приходам","👀 Сверка финансов", "💸 Внести приходы по наличке", "💰 Инкассация","👨🏻‍💻 ЗП за период", "⬅️ Вернуться")

## Открытие закрытие
funclist_today=("✅ Открыть смену","🚫 Закрыть смену", "🚩 Репорт", "⬅️ Вернуться")

## Аккаунт
funclist_acc=("💬 Сменить ник","👤 Я сменил юзернейм","📊 Статистика", "⬅️ Вернуться")

## Клубы
clublist = tuple(
    name for name, info in CLUBS.items() 
    if info.get('is_physical') is True
)

clublist_task = tuple(CLUBS.keys())

### Всячина
emojis={"roll":("⚡️","🦄","👻"),
        "confirm":("🔥","🎉","🍾","🍓","🐳"),
        "mood":("🔥","🍓","😭","🗿","🆒","😈","🤮")}

tags_main = '@grigorovda, @sermysh, @matveevanastya01'
extra_tags={"Ремонт":"@RobinKruzo1",
            "Улучшение бота":"@talgos_n"}

