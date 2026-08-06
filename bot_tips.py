import re
from datetime import date
from functools import lru_cache
from pathlib import Path


TIPS_PATH = Path(__file__).resolve().with_name('BOT_TIPS.md')
TIP_PATTERN = re.compile(
    r'^[ \t]*💡 А ты знал\?\s*$\s*'
    r'(.+?)'
    r'(?=^[ \t]*\d+\.\s+\*\*|^## |\Z)',
    re.MULTILINE | re.DOTALL,
)


@lru_cache(maxsize=None)
def load_bot_tips(path=TIPS_PATH):
    source = Path(path).read_text(encoding='utf-8')
    tips = []
    for match in TIP_PATTERN.finditer(source):
        tip = re.sub(r'\s*\n\s*', ' ', match.group(1).strip())
        if tip:
            tips.append(tip)
    if not tips:
        raise ValueError('В BOT_TIPS.md не найдено ни одной подсказки')
    return tuple(tips)


def get_daily_bot_tip(day=None):
    day = day or date.today()
    try:
        tips = load_bot_tips()
    except (OSError, ValueError) as error:
        print(f'Ошибка загрузки подсказок бота: {error}')
        return None
    return tips[day.toordinal() % len(tips)]


def append_daily_bot_tip(text, day=None):
    tip = get_daily_bot_tip(day)
    if not tip:
        return text
    return f'{text.rstrip()}\n\n💡 А ты знал?\n\n{tip}'
