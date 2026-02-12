import pygsheets
import json
import os

# Путь к ключу (как в твоем sheets.py)
KEY_FILE = 'key/omgbot-430116-e9a4d9c69b7f.json'

def sync_config():
    logs = []
    logs.append("🔄 Начинаю синхронизацию (pygsheets)...")

    try:
        # 1. Авторизация
        try:
            gc = pygsheets.authorize(service_file=KEY_FILE)
            sh = gc.open('Виарыч') # Открываем таблицу по имени
        except Exception as e:
            return f"❌ Ошибка подключения к Гуглу: {e}"

        # 2. Загрузка текущего JSON с диска
        try:
            with open('data/clubs.json', 'r', encoding='utf-8') as f:
                clubs_data = json.load(f)
        except FileNotFoundError:
            return "❌ Ошибка: Файл data/clubs.json не найден."

        # --- ОБНОВЛЕНИЕ ТЕГОВ (Вкладка 'Tags') ---
        try:
            wks_tags = sh.worksheet_by_title('Tags')
            # Получаем все записи как список словарей
            tags_records = wks_tags.get_all_records()
            
            count_tags = 0
            for row in tags_records:
                club = row.get('Club')
                tag = row.get('Tag')
                
                # Если такой клуб есть в JSON — обновляем тег
                if club and club in clubs_data:
                    clubs_data[club]['tag'] = tag
                    count_tags += 1
            
            logs.append(f"✅ Теги обновлены: {count_tags} шт.")
        except pygsheets.WorksheetNotFound:
            logs.append("⚠️ Вкладка 'Tags' не найдена.")
        except Exception as e:
            logs.append(f"⚠️ Ошибка в Tags: {e}")

        # --- ОБНОВЛЕНИЕ ВОПРОСОВ (Вкладка 'Questions') ---
        try:
            wks_q = sh.worksheet_by_title('Questions')
            q_records = wks_q.get_all_records()
            
            # Временная структура для сборки: temp_q[club][action][variant] = [список вопросов]
            temp_q = {}
            count_q = 0

            for row in q_records:
                club = row.get('Club')
                action = row.get('Action')
                q_text = row.get('Question')
                q_type = row.get('Type')
                
                # Пропускаем пустые строки
                if not club or not action or not q_text:
                    continue
                count_q += 1
                # Обработка варианта (может прийти как строка "0" или число 0)
                try:
                    variant = int(row.get('Variant', 0))
                except ValueError:
                    variant = 0

                # Строим структуру
                if club not in temp_q: temp_q[club] = {}
                if action not in temp_q[club]: temp_q[club][action] = {}
                if variant not in temp_q[club][action]: temp_q[club][action][variant] = []

                # Добавляем вопрос
                temp_q[club][action][variant].append({
                    "text": q_text,
                    "type": q_type
                })

            # Записываем собранные данные обратно в clubs_data
            for club, actions in temp_q.items():
                if club in clubs_data:
                    # Инициализируем секцию questions если её нет
                    if 'questions' not in clubs_data[club]:
                        clubs_data[club]['questions'] = {}
                    
                    for action, variants_dict in actions.items():
                        # Превращаем словарь вариантов {0: [...], 2: [...]} в список списков [[...], [], [...]]
                        if not variants_dict: continue
                        
                        max_v = max(variants_dict.keys())
                        # Создаем список нужной длины, заполненный пустыми списками
                        questions_list = [[] for _ in range(max_v + 1)]
                        
                        for v_idx, q_list in variants_dict.items():
                            questions_list[v_idx] = q_list
                        
                        clubs_data[club]['questions'][action] = questions_list
            
            logs.append(f"✅ Вопросы успешно обновлены ({count_q} строк).")

        except pygsheets.WorksheetNotFound:
            logs.append("⚠️ Вкладка 'Questions' не найдена.")
        except Exception as e:
            logs.append(f"⚠️ Ошибка в Questions: {e}")

        # 3. Сохранение файла
        with open('data/clubs.json', 'w', encoding='utf-8') as f:
            json.dump(clubs_data, f, ensure_ascii=False, indent=2)
        
        logs.append("💾 Конфиг сохранен на сервере!")
        return "\n".join(logs)

    except Exception as e:
        return f"🔥 Критическая ошибка: {e}"

# Для теста запуска напрямую
if __name__ == "__main__":
    print(sync_config())