from telebot import *
import sqlite3
import datetime
import threading
from constants import CHATS

def start_auth(message,bot):
   
    bot.send_message(message.chat.id, 'Привет! Давай познакомимся.\n\nДля начала напиши свое имя. Твоё имя здесь должно совпадать с тем, что у тебя написано на сервисе ShiftOn. Если ты его не знаешь, уточни, пожалуйста, у управляющего')
    bot.register_next_step_handler(message, ask_full_name,bot)

    
    
        
def ask_full_name(message,bot):
    if len(message.text.strip())<50:
        first_name = message.text.strip()
        bot.send_message(message.chat.id, f'Приятно познакомится, {first_name}\n\nНапиши, пожалуйста, фамилию')
        bot.register_next_step_handler(message, ask_nickname,bot,first_name)
    else:
        bot.send_message(message.chat.id, 'Что-то слишком длинное, не смогу запомнить, может, есть сокращенный вариант?')
        bot.register_next_step_handler(message, ask_full_name,bot)

    
def ask_nickname (message,bot,first_name):
    if len(message.text.strip())<50:
        second_name = message.text.strip()
        bot.send_message(message.chat.id, f'Отлично, записал!\n\nА теперь подумай, {first_name}, и скажи, как тебя записать в таблицах? Это имя можно будет поменять, но ты не думай, что можно написать ерунду! Имя должно быть уникальным, поэтому, если у тебя есть тёска, придумай что-нибудь особенное!')
        bot.register_next_step_handler(message, ask_bday,bot,first_name,second_name)
    else:
        bot.send_message(message.chat.id, 'Что-то слишком длинное, не смогу запомнить, может, есть сокращенный вариант?')
        bot.register_next_step_handler(message, ask_nickname,bot,first_name)
    
def ask_bday (message,bot,first_name,second_name):
    if len(message.text.strip())<50:
        nick_name = message.text.strip().capitalize()
        bot.send_message(message.chat.id, f'Ух, круто, {nick_name}! Буду теперь называть тебя так!\n\nКогда у тебя день Рождения? У меня, например 13.08.2024. Напиши дату также, пожалуйста! Хочу заранее подготовить для тебя подарок')
        bot.register_next_step_handler(message, ask_number,bot,first_name,second_name,nick_name)
    else:
        bot.send_message(message.chat.id, 'Что-то слишком длинное, не смогу запомнить, может, есть сокращенный вариант?')
        bot.register_next_step_handler(message, ask_bday,bot,first_name,second_name)
 
def ask_number (message,bot,first_name,second_name,nick_name):
    try:
        list1 = message.text.split('.')
        
        bday=datetime.date(int(list1[2]),int(list1[1]),int(list1[0]))
        bot.send_message(message.chat.id, f'Ого, это же совсем скоро! Приготовлю для тебя что-нибудь особенное!\n\nСлушай, а дашь свой номер телефона чтобы я мог тебя поздравить? Мой вот например +79991112233')
        bot.register_next_step_handler(message, ask_status,bot,first_name,second_name,nick_name,bday)
    except Exception:
        bot.send_message(message.chat.id, f'Что-то не понял, когда?')
        bot.register_next_step_handler(message, ask_number,bot,first_name,second_name,nick_name)

    
def ask_status (message,bot,first_name,second_name,nick_name,bday):
    if len(message.text.strip())==12 and message.text.strip().startswith("+7") and message.text.strip()[1:].isdigit():
        number = message.text.strip()
        
        
        bot.send_message(message.chat.id, f'Записал, спасибо!\n\nА мыло свое дашь? Открытку отправлю! Но у меня работает только почта от Гугл:(')
       
        bot.register_next_step_handler(message, ask_mail,bot,first_name,second_name,nick_name,bday,number)
    else:
        bot.send_message(message.chat.id, f'Что-то не пробивается. Это точно твой? Попробуй через +7, без других символов')
        bot.register_next_step_handler(message, ask_status,bot,first_name,second_name,nick_name,bday)






def ask_mail (message,bot,first_name,second_name,nick_name,bday,number):
    if len(message.text.strip())<50 and message.text.strip().endswith("@gmail.com"):
        email = message.text.strip()
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Я - админ OMG VR!', 'Я - менеджер OMG VR!')
        bot.send_message(message.chat.id, f'Записал, спасибо!\n\nА чем ты вообще занимаешься?',reply_markup=markup)
       
        bot.register_next_step_handler(message, check_status,bot,first_name,second_name,nick_name,bday,number,email)
    else:
        bot.send_message(message.chat.id, f'У меня только Гугл работает!\n\nАдрес должен кончаться на @gmail.com')
        bot.register_next_step_handler(message, ask_mail,bot,first_name,second_name,nick_name,bday,number)





def check_status (message,bot,first_name,second_name,nick_name,bday,number,email):
    chatid=message.chat.id
    if message.text== 'Я - менеджер OMG VR!':
        status = 1
        bot.send_message(message.chat.id, f'Что-то я тебе не верю! Скажи пароль')
        bot.register_next_step_handler(message, check_pass,bot,first_name,second_name,nick_name,bday,number,email,status)
    elif message.text== 'Я - админ OMG VR!':
        status = 0
        bot.send_message(message.chat.id, f'Вау, как круто! Хорошо, дай мне секунду, я все проверю...')
        check_user (message,bot, first_name,second_name,nick_name,bday,number,email,status)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Я - админ OMG VR!', 'Я - менеджер OMG VR!') 
        bot.send_message(message.chat.id, f'Не понял тебя. Чем именно?',reply_markup=markup)
        bot.register_next_step_handler(message, check_status,bot,first_name,second_name,nick_name,bday,number,email)



def check_pass (message,bot, first_name,second_name,nick_name,bday,number,email,status):
    if message.text == "ClintEastwood":
         bot.send_message(message.chat.id, f'Хорошо, я тебе верю! Но дай проверю еще кое-что...')
         check_user (message,bot, first_name,second_name,nick_name,bday,number,email,status)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Я - админ OMG VR!', 'Я - менеджер OMG VR!')
        bot.send_message(message.chat.id, f'Ты уверен что ты тот за кого себя выдаешь?',reply_markup=markup)
        bot.register_next_step_handler(message, check_status,bot,first_name,second_name,nick_name,bday,number,email)



def check_user(message,bot, first_name,second_name,nick_name,bday,number,email,status):
    conn=sqlite3.connect('db/omgbot.sql')
    cur = conn.cursor()
    cur.execute("SELECT * FROM users_new WHERE nick_name='%s'" % (nick_name))
    users = cur.fetchall()
    cur.close()
    conn.close()
    if len(users)==0:
        bot.send_message(message.chat.id, f'Вроде все в порядке! Давай посмотрим запись')
        bot.send_message(message.chat.id, f'Имя: {first_name}\nФамилия: {second_name}\nНик: {nick_name}\nДень рождения: {bday}\nНомер телефона: {number}\nЭл. адрес: {email}')
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Всё в порядке!', 'Нет, я хочу кое-что поменять!')
        bot.send_message(message.chat.id, f'Проверь, всё верно?',reply_markup=markup)
        bot.register_next_step_handler(message, confirm,bot,first_name,second_name,nick_name,bday,number,email,status)




    else:
        bot.send_message(message.chat.id, f'Прости, но твое имя для смен успели занять! Давай попробуем еще раз? Введи другое имя')
        bot.register_next_step_handler(message, edit_nick,bot,first_name,second_name,nick_name,bday,number,email,status)

def confirm (message,bot, first_name,second_name,nick_name,bday,number,email,status):
    if message.text=='Всё в порядке!':
        bot.send_message(message.chat.id, f'Отлично! Сейчас запишу...')
        send_user(message,bot, first_name,second_name,nick_name,bday,number,email,status)
        from sheets import update_users
        update_users()
    elif message.text=='Нет, я хочу кое-что поменять!':
        bot.send_message(message.chat.id, f'Форматирую...')
        start_auth(message,bot)
    else:
        markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
        markup.add('Всё в порядке!', 'Нет, я хочу кое-что поменять!')
        bot.send_message(message.chat.id, f'Не понял тебя. Всё в порядке?',reply_markup=markup)
        bot.register_next_step_handler(message, confirm,bot,first_name,second_name,nick_name,bday,number,email,status)

        



def send_user(message,bot, first_name,second_name,nick_name,bday,number,email,status):
        conn=sqlite3.connect('db/omgbot.sql')
        cur = conn.cursor()
        cur.execute("UPDATE users_new SET  first_name='%s', second_name='%s', nick_name='%s', bday='%s', phone='%s', email='%s',status='%s',chatid='%s' WHERE login= '%s'" % (first_name,second_name,nick_name,bday,number,email,status,message.chat.id,"@"+message.from_user.username))
        conn.commit()
        conn.close()
        bot.send_message(message.chat.id, f'Все в порядке! Будем знакомы!\n\nНе забудь подписаться на наш инфоканал! https://t.me/+Q2YQbLpwLIswYWY6',reply_markup=types.ReplyKeyboardRemove())
        bot.send_message (CHATS['me'],f'Имя: {first_name}\nФамилия: {second_name}\nНик: {nick_name}\nДень рождения: {bday}\nНомер телефона: {number}\nЭл. адрес: {email}')
        from main import hello as comeback
        comeback (message.chat.id, bot)
        #comeback(message.chat.id)


        
def edit_nick(message,bot, first_name,second_name,nick_name,bday,number,email,status):
    if len(message.text.strip())<50:
        nick_name = message.text.strip().capitalize() 
        bot.send_message(message.chat.id, f'Сейчас проверю...')
        check_user (message,bot, first_name,second_name,nick_name,bday,number,email,status)
    else:
        bot.send_message(message.chat.id, 'Что-то слишком длинное, не смогу запомнить, может, есть сокращенный вариант?')
        bot.register_next_step_handler(message, edit_nick,bot,first_name,second_name,nick_name,bday,number,email,status)
    






