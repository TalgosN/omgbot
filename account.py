from telebot import *
from constants import *
import sqlite3
from sheets import *



def account_settings(message,bot):
    
    bot.send_message(message.chat.id, f'Этот раздел посвящен тебе, тебе и только тебе!')
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(*funclist_acc)
    bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
    bot.register_next_step_handler(message, func_acc,bot)
     
def func_acc(message,bot):
    if message.text=='💬 Сменить ник':
        bot.send_message(message.chat.id, f'Здесь ты можешь сменить имя, которым я тебя называю и которым ты записываешься в таблицах\n\nПожалуйста, не меняй его слишком часто, чтобы не путать меня и других!')
        change_nickname_handler(message,bot)

    elif message.text=='📊 Статистика':
        bot.send_message(message.chat.id, f'Здесь ты можешь посмотреть свою статистику за месяц и за всё время!')
        
        stats_handler(message,bot)

    elif message.text=='👤 Я сменил юзернейм':
        
        bot.send_message(message.chat.id, f'Этот подраздел нужен для того чтобы учесть все изменения в таблицах, а также чтобы я не глючил при открытии/закрытии\n\nПожалуйста, не меняй его слишком часто, чтобы не путать меня и других!')
        
        change_username_handler(message,bot)
        
    elif message.text=='⬅️ Вернуться':
        returnback(message,bot)
       
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add(*funclist_acc)
        bot.send_message(message.chat.id, 'Что вы хотите сделать? 👀', reply_markup=markup)
        bot.register_next_step_handler(message, func_acc,bot)
        
        
        
def returnback(message,bot):
        from menu import hello
        hello (message.chat.id,bot)
        
############################ смена ника ##################################################
        
def change_nickname_handler(message,bot):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    markup.add("Вернуться")
    
    
    bot.send_message(message.chat.id, f'Введи своё новое имя', reply_markup=markup)
    bot.register_next_step_handler(message, change_nickname,bot)
    
    
    
def change_nickname(message,bot):
    
    if message.text== "Вернуться":
        account_settings(message,bot)
        
    else:  
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        
        nick_name = message.text.strip()
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("SELECT * FROM users_new WHERE nick_name=?", (nick_name,))
        users = cur.fetchall()
        cur.close()
        conn.close()
    
    
        if len(nick_name)>50:
            bot.send_message(message.chat.id, 'Слишком длинное!')
            bot.send_message(message.chat.id, f'Введи своё новое имя', reply_markup=markup)
            bot.register_next_step_handler(message, change_nickname,bot)
    
        elif len(users)==0:
            
            markup2 = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
            markup2.add("Да","Нет")
        
            bot.send_message(message.chat.id, f'Отлично, {nick_name}! Меняю ник?', reply_markup=markup2)
            bot.register_next_step_handler(message, change_nickname_confirm,bot,nick_name)
            
        else:
            bot.send_message(message.chat.id, f'Такое имя уже занято!')
            bot.send_message(message.chat.id, f'Введи своё новое имя', reply_markup=markup)
            bot.register_next_step_handler(message, change_nickname,bot)
    
def change_nickname_confirm( message, bot,nick_name):
    
    if message.text=="Да":
        
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("UPDATE users_new SET nick_name=? WHERE chatid=?", (nick_name, message.chat.id))
        conn.commit()
        cur.close()
        conn.close()
        update_users()
        bot.send_message(message.chat.id, f'Все готово!')
        returnback(message,bot)
        
    elif message.text == 'Нет':
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup.add("Вернуться")
        bot.send_message(message.chat.id, f'Введи своё новое имя', reply_markup=markup)
        bot.register_next_step_handler(message, change_nickname,bot)
        
    else:
        
        bot.send_message(message.chat.id, 'Не понял тебя!')
        markup2 = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
        markup2.add("Да","Нет")
        
        bot.send_message(message.chat.id, f'Отлично, {nick_name}! Меняю ник?', reply_markup=markup2)
        bot.register_next_step_handler(message, change_nickname_confirm,bot,nick_name)

############################ Статистика ##################################################      


def stats_handler(message,bot):
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("📊 За месяц","⭐️ За всё время","Вернуться")
    
    
    bot.send_message(message.chat.id, f'Выбери, какую статистику ты хочешь посмотреть 👨🏻‍💻', reply_markup=markup)
    bot.register_next_step_handler(message, stats_show,bot)
    
    
def stats_show(message,bot):
    
    if message.text== "Вернуться":
        account_settings(message,bot)
        
    elif message.text=="📊 За месяц":
        
        stats_acc(message,bot)
        stats_handler(message,bot)
    elif message.text=="⭐️ За всё время":
        
        
        statsall_acc(message,bot)
        stats_handler(message,bot)        
        
    else:
        stats_handler(message,bot)
        

def stats_acc(message, bot):
    update_status()
    
    for i in range(len(tables)):
        update_table(tables[i])    
    
    user_name = "@"+message.from_user.username

    begin = datetime.now().replace(day=1)
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    count_list=[]
    for i in action:
        count_list.append(str(def_count(action[i],user_name,begin,today)))       
    
    sale_abik = def_sum_bonus('abik',user_name,begin,today)
    sale_sert = def_sum_bonus('sert',user_name,begin,today)

    if sale_abik==None:
        sale_abik = 0
    
    if sale_sert==None:
        sale_sert = 0

    
    
    text = f"{random.choice(TEXTS['hey'])} {user_name}!\n\n📊 Твоя статистика за месяц:\n\n⏱ Продления: {count_list[0]}\n🎂 Дни рождения: {count_list[1]}\n🌠 Инициативы: {count_list[2]}\n💸 Продано абонементов на сумму: {sale_abik} р.\n💲 Продано сертификатов на сумму: {sale_sert} р."

    bot.reply_to(message, text)



def statsall_acc(message, bot):
    update_status()
    
    for i in range(len(tables)):
        update_table(tables[i])
        
    user_name = "@"+message.from_user.username

    begin = datetime.now().replace(year=2022)
    today = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d')
    count_list=[]
    for i in action:
        count_list.append(str(def_count(action[i],user_name,begin,today)))       
    
    sale_abik = def_sum_bonus('abik',user_name,begin,today)
    sale_sert = def_sum_bonus('sert',user_name,begin,today)

    if sale_abik==None:
        sale_abik = 0
    
    if sale_sert==None:
        sale_sert = 0

    
    
    text = f"{random.choice(TEXTS['hey'])} {user_name}!\n\n📊 Твоя статистика за все время:\n\n⏱ Продления: {count_list[0]}\n🎂 Дни рождения: {count_list[1]}\n🌠 Инициативы: {count_list[2]}\n💸 Продано абонементов на сумму: {sale_abik} р.\n💲 Продано сертификатов на сумму: {sale_sert} р."

    bot.reply_to(message, text)
  
############################ Смена логина ##################################################     


def change_username_handler(message,bot):
    
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add("Да","Нет")
    
    bot.send_message(message.chat.id, 'ВНИМАНИЕ! Не работает ни в коем случае если юзернейма нет или он скрыт. Пожалуйста, поставь юзернейм, либо, ставь его каждый раз когда мной пользуешься')
    
    bot.send_message(message.chat.id, 'ВНИМАНИЕ!\n\nНужно сначала сменить юзернейм в ТГ и лишь после воспользоваться этой функцией. Ты уже сменил юзернейм?', reply_markup=markup)
    
    bot.register_next_step_handler(message, change_username,bot)
    
    
def change_username (message,bot):
    
    if message.text=="Да":
        try:
            update_all_tables(message)
            bot.send_message(message.chat.id, 'Все готово!')
            account_settings(message,bot)
            
        except Exception as e:
            bot.send_message(message.chat.id, e)
            bot.send_message(message.chat.id, 'Что-то пошло не так! Проверь, что все сделано так, как нужно')
            account_settings(message,bot)
        
        
    elif message.text == "Нет":
        account_settings(message,bot)
        
    else:
        
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add("Да","Нет")
        bot.send_message(message.chat.id, 'ВНИМАНИЕ!\n\nНужно сначала сменить юзернейм в ТГ и лишь после воспользоваться этой функцией. Ты уже сменил юзернейм?', reply_markup=markup)
    
        bot.register_next_step_handler(message, change_username,bot)
        
    
        
def update_all_tables(message):
    
    chatid=message.chat.id
    new_login = '@'+message.from_user.username
     
    conn=sqlite3.connect('db/omgbot.sql')
    
    #define old login from chatid
    
    cur = conn.cursor()
    cur.execute("SELECT login FROM users_new WHERE chatid=?", (chatid,))
    logins=cur.fetchall()
    cur.close()
    old_login = logins[0][0]
    
    #users update 
    cur = conn.cursor()
    cur.execute("UPDATE users_new SET login=? WHERE login=?", (new_login, old_login))
    conn.commit()
    cur.close()
    
    #KPI update
    
    for i in tables:
        cur = conn.cursor()
        cur.execute(f'UPDATE "{i}" SET who=? WHERE who=?', (new_login, old_login))
        conn.commit()
        cur.close()
    
    
    #openclose update 
    cur = conn.cursor()
    cur.execute("UPDATE activity SET login=? WHERE login=?", (new_login, old_login))
    conn.commit()
    cur.close()
    
    conn.close()
    
    
    
    #Sheets update
    update_users()
    update_table_open()
    
    for i in range(len(tables)):
        update_table(tables[i])
