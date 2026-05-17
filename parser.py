import json
import re
import sqlite3
import requests
from bs4 import BeautifulSoup
import telebot
from telebot import types
from datetime import datetime

# --- НАСТРОЙКИ ---
BOT_TOKEN = "8807593947:AAEWcZPb0CyuvWxJeZFc-CLH1WXDW-NM2Ag"
YOUR_CHAT_ID = "2115475429"

bot = telebot.TeleBot(BOT_TOKEN)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
}

# --- КУРСЫ ВАЛЮТ (Обновляются на лету) ---
def get_exchange_rates():
    """Получает актуальные курсы валют к доллару"""
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        response = requests.get(url, timeout=5).json()
        return response.get("rates", {})
    except Exception:
        # Резервные курсы, если API недоступен
        return {"RUB": 90.0, "KZT": 440.0, "USD": 1.0, "TRY": 32.0, "EUR": 0.9}

def convert_prices(price_float, original_currency_symbol, rates):
    """Переводит базовую цену в Доллары, Тенге и Рубли"""
    # Определяем базовую валюту по символу
    base_currency = "USD"
    if "₸" in original_currency_symbol: base_currency = "KZT"
    elif "руб" in original_currency_symbol.lower(): base_currency = "RUB"
    elif "TL" in original_currency_symbol: base_currency = "TRY"
    elif "EUR" in original_currency_symbol: base_currency = "EUR"

    # Сначала переводим в доллары (как промежуточную валюту)
    rate_to_usd = rates.get(base_currency, 1.0)
    price_in_usd = price_float / rate_to_usd if rate_to_usd else price_float

    # Теперь из долларов в нужные нам
    usd_val = price_in_usd
    kzt_val = price_in_usd * rates.get("KZT", 440.0)
    rub_val = price_in_usd * rates.get("RUB", 90.0)

    return f"🇺🇸 ${usd_val:.2f} | 🇰🇿 {kzt_val:.0f} ₸ | 🇷🇺 {rub_val:.0f} ₽"

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("prices.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS games (
            url TEXT PRIMARY KEY,
            title TEXT,
            last_price_str TEXT,
            last_price_float REAL
        )
    ''')
    conn.commit()
    conn.close()

# --- ПАРСЕР ---
def parse_ps_store(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return None, None, None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. Название
        title = "Неизвестная игра"
        title_tag = soup.find("h1", {"data-qa": re.compile(r".*title.*")})
        if title_tag:
            title = title_tag.get_text(strip=True)
        elif soup.title and soup.title.string:
            title = soup.title.string.split('|')[0].strip()

        # 2. Цена
        price_str = None
        price_tags = soup.find_all(attrs={"data-qa": re.compile(r".*price.*|.*cta.*")})
        for tag in price_tags:
            text = tag.get_text(strip=True)
            if text:
                clean_price = re.search(r'(\$[\d.,]+|[\d\s.,]+(?:₸|TL|руб|\bEUR\b))', text)
                if clean_price:
                    price_str = clean_price.group(0)
                    break
        
        # 3. Дата окончания скидки
        end_date = "Дата не указана"
        # Ищем типичные фразы Sony (работает для RU и EN интерфейса)
        date_pattern = re.compile(r'(?:Заканчивается|Offer ends|до)\s*(\d{1,2}[\./]\d{1,2}[\./]\d{2,4})', re.IGNORECASE)
        date_match = date_pattern.search(response.text)
        if date_match:
            end_date = date_match.group(1)

        return title, price_str, end_date
    except Exception:
        return None, None, None

def clean_and_convert_price(price_str):
    cleaned = price_str.replace(" ", "")
    match = re.search(r'[\d.,]+', cleaned)
    if match:
        num_str = match.group(0)
        if ',' in num_str and '.' not in num_str:
            num_str = num_str.replace(',', '.')
        try:
            return float(num_str)
        except ValueError:
            return 0.0
    return 0.0

# --- КОМАНДЫ БОТА ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Я твой трекер скидок PS5.\n\n"
                          "Доступные команды:\n"
                          "/add [ссылки] - Добавить игры\n"
                          "/remove [ссылки] - Удалить игры\n"
                          "/list - Показать список\n"
                          "/check - Проверить скидки")

@bot.message_handler(commands=['add'])
def add_game(message):
    if str(message.chat.id) != YOUR_CHAT_ID: return
    urls = re.findall(r'(https://store\.playstation\.com[^\s>\]\)]+)', message.text)
    if not urls:
        bot.reply_to(message, "❌ Отправь ссылки на игры.")
        return

    bot.reply_to(message, f"🔄 Загружаю {len(urls)} игр(ы)...")
    conn = sqlite3.connect("prices.db")
    cursor = conn.cursor()
    
    added, exist = 0, 0
    for url in urls:
        title, price_str, _ = parse_ps_store(url)
        if not price_str: continue
        price_float = clean_and_convert_price(price_str)
        try:
            cursor.execute("INSERT INTO games VALUES (?, ?, ?, ?)", (url, title, price_str, price_float))
            added += 1
        except sqlite3.IntegrityError:
            exist += 1
    conn.commit()
    conn.close()
    bot.send_message(message.chat.id, f"✅ Добавлено: {added}\n⏩ Уже в базе: {exist}")

@bot.message_handler(commands=['list'])
def list_games(message):
    if str(message.chat.id) != YOUR_CHAT_ID: 
        return
        
    conn = sqlite3.connect("prices.db")
    cursor = conn.cursor()
    cursor.execute("SELECT title, last_price_str, url FROM games")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.reply_to(message, "📭 Твой список отслеживания пока пуст. Отправь мне `.txt` файл со ссылками!")
        return
        
    response = "📋 *Список отслеживаемых игр:*\n\n"
    for title, price, url in rows: 
        response += f"🎮 *{title}*\n💵 Текущая цена: *{price}*\n🔗 [Ссылка на магазин]({url})\n\n"
    
    # Создаем интерактивную клавиатуру под сообщением
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    # Создаем кнопки
    btn_check = types.InlineKeyboardButton(text="🔄 Проверить скидки прямо сейчас", callback_data="run_check")
    btn_help_del = types.InlineKeyboardButton(text="🗑 Как удалять игры из базы?", callback_data="help_delete")
    
    # Добавляем кнопки в клавиатуру
    markup.add(btn_check, btn_help_del)
    
    bot.send_message(message.chat.id, response, parse_mode="Markdown", reply_markup=markup, disable_web_page_preview=True)
@bot.message_handler(commands=['check'])
def check_prices_command(message):
    if str(message.chat.id) != YOUR_CHAT_ID: return
    bot.reply_to(message, "🔄 Получаю курсы валют и сканирую магазин...")
    
    rates = get_exchange_rates()
    conn = sqlite3.connect("prices.db")
    cursor = conn.cursor()
    cursor.execute("SELECT url, title, last_price_str, last_price_float FROM games")
    games = cursor.fetchall()
    
    discounts = 0
    for url, title, saved_str, saved_float in games:
        _, current_str, end_date = parse_ps_store(url)
        if not current_str: continue
            
        current_float = clean_and_convert_price(current_str)
        
        if current_float < saved_float:
            # Генерируем мультивалютные цены
            old_multi = convert_prices(saved_float, saved_str, rates)
            new_multi = convert_prices(current_float, current_str, rates)
            
            # Собираем красивую карточку
            msg = (
                f"🔥 *СУПЕР СКИДКА!* 🔥\n\n"
                f"🕹 *{title}*\n\n"
                f"❌ *Было:*\n~~{old_multi}~~\n\n"
                f"✅ *Стало:*\n*{new_multi}*\n\n"
                f"⏳ *Скидка до:* {end_date}\n\n"
                f"🛒 [Купить в PS Store]({url})"
            )
            bot.send_message(YOUR_CHAT_ID, msg, parse_mode="Markdown", disable_web_page_preview=True)
            
            cursor.execute("UPDATE games SET last_price_str = ?, last_price_float = ? WHERE url = ?", 
                           (current_str, current_float, url))
            discounts += 1
            
        elif current_float > saved_float:
            cursor.execute("UPDATE games SET last_price_str = ?, last_price_float = ? WHERE url = ?", 
                           (current_str, current_float, url))
            
    conn.commit()
    conn.close()
    bot.send_message(YOUR_CHAT_ID, f"✅ Проверка окончена. Новых скидок: {discounts}.")

@bot.message_handler(content_types=['document'])
def handle_txt_file(message):
    if str(message.chat.id) != YOUR_CHAT_ID:
        return

    # Проверяем, что прислали именно .txt файл
    if not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "❌ Пожалуйста, отправь список ссылок в текстовом файле формата `.txt`", parse_mode="Markdown")
        return

    bot.reply_to(message, "📥 Файл получен! Скачиваю и начинаю массовую загрузку в базу данных. Подожди немного...")

    try:
        # Получаем информацию о файле и скачиваем его через API Telegram
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Декодируем байты файла в текст
        file_text = downloaded_file.decode('utf-8')
        
        # Ищем все ссылки на PS Store внутри файла
        urls = re.findall(r'(https://store\.playstation\.com[^\s>\]\)]+)', file_text)
        
        if not urls:
            bot.send_message(message.chat.id, "❌ Внутри файла не найдено ни одной корректной ссылки на PS Store.")
            return

        bot.send_message(message.chat.id, f"📦 Найдено игр в файле: {len(urls)}. Начинаю парсинг...")

        conn = sqlite3.connect("prices.db")
        cursor = conn.cursor()
        
        added, exist = 0, 0
        
        for url in urls:
            title, price_str, _ = parse_ps_store(url)
            if not price_str: 
                continue
            price_float = clean_and_convert_price(price_str)
            try:
                cursor.execute("INSERT INTO games VALUES (?, ?, ?, ?)", (url, title, price_str, price_float))
                added += 1
            except sqlite3.IntegrityError:
                exist += 1
                
        conn.commit()
        conn.close()
        
        bot.send_message(
            message.chat.id, 
            f"📊 *Итоги импорта из файла:*\n\n"
            f"➕ Успешно добавлено: *{added}*\n"
            f"⏩ Уже были в базе: *{exist}*", 
            parse_mode="Markdown"
        )

    except Exception as e:
        bot.send_message(message.chat.id, f"💥 Произошла ошибка при обработке файла: {e}")

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    try:
        if call.message:
            if call.data == "run_check":
                # Редактируем сообщение, показывая, что процесс пошел, чтобы было понятно, что бот не завис
                bot.answer_callback_query(call.id, text="Запускаю сканирование базы...")
                # Вызываем нашу уже готовую функцию проверки цен
                check_prices_command(call.message)
                
            elif call.data == "help_delete":
                bot.answer_callback_query(call.id)
                help_text = (
                    "ℹ️ *Как удалить игру из отслеживания:*\n\n"
                    "Просто отправь мне команду `/remove` и ссылку на игру через пробел.\n\n"
                    "Например:\n"
                    "`/remove https://store.playstation.com/...`"
                )
                bot.send_message(call.message.chat.id, help_text, parse_mode="Markdown")
    except Exception as e:
        print(f"Ошибка при нажатии кнопки: {e}")

import threading
import time

from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "Бот работает!"

def run_web_server():
    # Запускаем фейковый сайт на порту 10000 (стандарт для Render)
    app.run(host='0.0.0.0', port=10000)

if __name__ == "__main__":
    init_db()
    
    # Запускаем веб-сервер в фоне, чтобы Render не ругался
    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()
    
    print("Супер-бот запущен на фри-хостинге!")
    bot.infinity_polling()