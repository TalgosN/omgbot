import time
import threading
from telebot.util import antiflood

msg_lock = threading.Lock()

def safe_send(bot, chat_id, text, photo=None, **kwargs):
    with msg_lock:
        try:
            if photo and photo != "None":
                # Если есть фото, шлем картинку с подписью
                msg = antiflood(bot.send_photo, chat_id, photo=photo, caption=text, **kwargs)
            else:
                # Иначе обычный текст
                msg = antiflood(bot.send_message, chat_id, text, **kwargs)
            time.sleep(5)
            return msg
        except Exception as e:
            print(f"Ошибка при безопасной отправке: {e}")
            return None