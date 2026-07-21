import os
import json
from dotenv import load_dotenv

load_dotenv()

### Ключи и Токены
TELEGRAM_API_KEY = os.getenv("TELEGRAM_API_KEY")
FT_API_KEY = os.getenv("FT_API_KEY")
AQSI_API_KEY = os.getenv("AQSI_API_KEY")
WEATHER_API_KEY = os.getenv("WEATHER_KEY")

# --- НОВЫЕ НАСТРОЙКИ SHIFTON API ---
# URL сервера (по умолчанию локальный, если бот и API на одном сервере)
SHIFTON_API_URL = os.environ.get("SHIFTON_API_URL", "http://127.0.0.1").rstrip("/")

# Единый токен для доступа к новому API
SHIFTON_API_TOKEN = os.environ.get("SHIFTON_API_TOKEN")

### Лимит на сообщения
MESSAGE_LIMIT_TIME = int(os.getenv("MESSAGE_LIMIT_TIME", 20))

### Чаты для рассылки
CHATS = {
    "reports": os.getenv("CHAT_REPORTS"),
    "main_group": os.getenv("CHAT_MAIN_GROUP"),
    "repair_extra": os.getenv("CHAT_REPAIR_EXTRA"),
    "me": os.getenv("CHAT_ME")
}

def validate_config():
    required = {
        "TELEGRAM_API_KEY": TELEGRAM_API_KEY,
        "SHIFTON_API_URL": SHIFTON_API_URL,
        "SHIFTON_API_TOKEN": SHIFTON_API_TOKEN,
        "CHAT_REPORTS": CHATS["reports"],
        "CHAT_MAIN_GROUP": CHATS["main_group"],
        "CHAT_REPAIR_EXTRA": CHATS["repair_extra"],
        "CHAT_ME": CHATS["me"]
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"Не заданы обязательные переменные окружения: {', '.join(missing)}")

### Фразы
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHRASES_PATH = os.path.join(BASE_DIR, "data", "phrases.json")
CLUBS_PATH = os.path.join(BASE_DIR, "data", "clubs.json")

with open(PHRASES_PATH, "r", encoding="utf-8") as f:
    TEXTS = json.load(f)



def get_clubs():
    try:
        # Проверяем, существует ли файл, чтобы не уронить бота
        if not os.path.exists(CLUBS_PATH):
            print(f"Ошибка: Файл {CLUBS_PATH} не найден!")
            return {}
            
        with open(CLUBS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Критическая ошибка чтения JSON: {e}")
        return {}
    
col=3 # Пока не знаю что это

### Кнопки

## Доска проблем
funclist_task=("➕ Добавить", "⭕ Текущие", "🛠 Ремонт", "🤖 Улучшения бота", "✔ Выполненные")
messtype=("Вопрос/жалоба/предложение","Ремонт", "Улучшение бота")
taskto=("Обработать","Выбрать другое") 
statuses=("В работе", "Выполнено", "Отклонено")
admin_funclist = ['📢 Рассылки', '📦 Расходники (Админ)', '⚙️ Обновить настройки', '📊 Тест недельного отчета', '📦 Тест отчета по расходникам', '⬅️ Вернуться']

## Главное меню
funclist={0:("👨🏻‍💻 Смена", "🚩 Доска проблем","🗓 Расписание", "👤 Аккаунт", '📦 Расходники', "🆘 Помощь"),
          1:("👨🏻‍💻 Смена", "🚩 Доска проблем","🗓 Расписание", "👤 Аккаунт","🧑🏻‍💻 Админ панель", '📦 Расходники', "🆘 Помощь"),
          2:("👨🏻‍💻 Смена", "🚩 Доска проблем","🗓 Расписание", "👤 Аккаунт", "💲 Финансы", "🧑🏻‍💻 Админ панель", '📦 Расходники', "🆘 Помощь")} # с привелегиями

## Финансы
funclist_fin=("📑 Отчет по приходам","👀 Сверка финансов", "💸 Внести приходы по наличке", "💰 Инкассация","👨🏻‍💻 ЗП за период", '📊 Сводный отчет', "⬅️ Вернуться")

## Открытие закрытие
funclist_today=("✅ Открыть смену","🚫 Закрыть смену", "🚩 Репорт", "⬅️ Вернуться")

## Аккаунт
funclist_acc=("💬 Сменить ник","👤 Я сменил юзернейм","📊 Статистика", "⬅️ Вернуться")

## Клубы
clublist = tuple(
    name for name, info in get_clubs().items() 
    if info.get('is_physical') is True
)

clublist_task = tuple(get_clubs().keys())

### Всячина
emojis={"roll":("⚡️","🦄","👻"),
        "confirm":("🔥","🎉","🍾","🍓","🐳"),
        "mood":("🔥","🍓","😭","🗿","🆒","😈","🤮")}

tags_main = '@grigorovda, @sermysh, @matveevanastya01'
extra_tags={"Ремонт":"@RobinKruzo1",
            "Улучшение бота":"@talgos_n"}
