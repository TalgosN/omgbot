import threading
import time


MEMBERSHIP_CACHE_SECONDS = 60
ACTIVE_MEMBER_STATUSES = {'creator', 'administrator', 'member', 'restricted'}

_membership_cache = {}
_membership_cache_lock = threading.Lock()


def is_main_group_member(bot, group_id, telegram_id):
    """Checks MAIN GROUP membership with a short, process-local cache."""
    if not bot or not group_id or telegram_id is None:
        return False

    key = (str(group_id), str(telegram_id))
    now = time.monotonic()
    with _membership_cache_lock:
        cached = _membership_cache.get(key)
        if cached and now - cached['checked_at'] < MEMBERSHIP_CACHE_SECONDS:
            return cached['allowed']

    try:
        status = bot.get_chat_member(group_id, telegram_id).status
        allowed = status in ACTIVE_MEMBER_STATUSES
    except Exception as error:
        print(f'Не удалось проверить участие Telegram ID {telegram_id} в MAIN GROUP: {error}')
        allowed = False

    with _membership_cache_lock:
        _membership_cache[key] = {'allowed': allowed, 'checked_at': now}
    return allowed


def clear_membership_cache(group_id=None, telegram_id=None):
    with _membership_cache_lock:
        if group_id is None and telegram_id is None:
            _membership_cache.clear()
            return
        _membership_cache.pop((str(group_id), str(telegram_id)), None)
