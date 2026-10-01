import sqlite3
import datetime
import io
import time
import threading
import telebot
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.daemon = True
    t.start()

keep_alive()

TOKEN = "8981394220:AAFHcPKw3y4n0mO0nnDcOzYPO7zIARhJDMc"
bot = telebot.TeleBot(TOKEN)

user_chat_id = None

# ==================== 1. РАБОТА С БАЗОЙ ДАННЫХ ====================
def init_db():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            water INTEGER DEFAULT 0,
            creatine INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS workout_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_name TEXT,
            set_num INTEGER,
            weight REAL,
            reps INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("PRAGMA table_info(workout_logs)")
    columns = [column[1] for column in cursor.fetchall()]
    if "set_num" not in columns:
        cursor.execute("ALTER TABLE workout_logs ADD COLUMN set_num INTEGER DEFAULT 1")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    # Дефолтная цель по воде, если еще не задана
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('water_goal', '2500')")
    
    conn.commit()
    conn.close()

init_db()

def get_today_str():
    return datetime.date.today().isoformat()

def save_chat_id(chat_id):
    global user_chat_id
    user_chat_id = chat_id
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)", (str(chat_id),))
    conn.commit()
    conn.close()

def load_chat_id():
    global user_chat_id
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'chat_id'")
    row = cursor.fetchone()
    conn.close()
    if row:
        user_chat_id = int(row[0])

load_chat_id()

def get_water_goal():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'water_goal'")
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row else 2500

def set_water_goal_db(goal):
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('water_goal', ?)", (str(goal),))
    conn.commit()
    conn.close()

def add_water_to_db(amount):
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    today = get_today_str()
    cursor.execute("""
        INSERT INTO daily_stats (date, water, creatine) VALUES (?, ?, 0)
        ON CONFLICT(date) DO UPDATE SET water = water + ?
    """, (today, amount, amount))
    conn.commit()
    conn.close()

def set_creatine_db():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    today = get_today_str()
    cursor.execute("""
        INSERT INTO daily_stats (date, water, creatine) VALUES (?, 0, 1)
        ON CONFLICT(date) DO UPDATE SET creatine = 1
    """, (today,))
    conn.commit()
    conn.close()

def get_today_stats_db():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    today = get_today_str()
    cursor.execute("SELECT water, creatine FROM daily_stats WHERE date = ?", (today,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], bool(row[1])
    return 0, False

def log_exercise_db(name, set_num, weight, reps):
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT weight, reps FROM workout_logs 
        WHERE LOWER(exercise_name) = LOWER(?) AND set_num = ? 
        ORDER BY id DESC LIMIT 1
    """, (name, set_num))
    last_set = cursor.fetchone()
    
    cursor.execute("""
        SELECT MAX(weight) FROM workout_logs 
        WHERE LOWER(exercise_name) = LOWER(?)
    """, (name,))
    max_record = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO workout_logs (exercise_name, set_num, weight, reps) 
        VALUES (?, ?, ?, ?)
    """, (name, set_num, weight, reps))
    
    conn.commit()
    conn.close()
    return last_set, max_record

def delete_last_log_db():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, exercise_name, set_num, weight, reps FROM workout_logs ORDER BY id DESC LIMIT 1")
    last_row = cursor.fetchone()
    if last_row:
        cursor.execute("DELETE FROM workout_logs WHERE id = ?", (last_row[0],))
        conn.commit()
        conn.close()
        return last_row
    conn.close()
    return None

def get_all_records_db():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT exercise_name, MAX(weight) as max_w
        FROM workout_logs 
        GROUP BY LOWER(exercise_name)
        ORDER BY max_w DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_last_logs_db(limit=7):
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT exercise_name, set_num, weight, reps, timestamp 
        FROM workout_logs 
        ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

# ==================== 2. ГЕНЕРАЦИЯ ГРАФИКА И АНАЛИТИКИ ====================
def generate_exercise_chart(exercise_name):
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DATE(timestamp), MAX(weight) 
        FROM workout_logs 
        WHERE LOWER(exercise_name) = LOWER(?)
        GROUP BY DATE(timestamp)
        ORDER BY timestamp ASC
    """, (exercise_name,))
    data = cursor.fetchall()
    conn.close()

    if not data or len(data) < 2:
        return None

    dates = [row[0][5:] for row in data]
    weights = [row[1] for row in data]

    plt.figure(figsize=(8, 4))
    plt.plot(dates, weights, marker='o', color='#10B981', linewidth=2, markersize=6)
    plt.title(f"Прогресс весов: {exercise_name.title()}", fontsize=14, fontweight='bold')
    plt.xlabel("Дата")
    plt.ylabel("Макс. вес (кг)")
    plt.grid(True, linestyle='--', alpha=0.5)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=120)
    buf.seek(0)
    plt.close()
    return buf

def get_weekly_summary():
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT SUM(weight * reps), COUNT(DISTINCT DATE(timestamp))
        FROM workout_logs 
        WHERE timestamp >= DATE('now', '-7 days')
    """)
    tonnage_row = cursor.fetchone()
    total_tonnage = tonnage_row[0] if tonnage_row[0] else 0
    workout_days = tonnage_row[1] if tonnage_row[1] else 0

    cursor.execute("""
        SELECT COUNT(*) FROM daily_stats 
        WHERE date >= DATE('now', '-7 days') AND creatine = 1
    """)
    creatine_days = cursor.fetchone()[0]

    conn.close()
    
    return (
        f"📊 **НЕДЕЛЬНЫЙ ОТЧЕТ ТРЕНИРОВОК**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏋️‍♂️ **Тренировочных дней:** `{workout_days}`\n"
        f"🏋️ **Поднятый тоннаж:** `{int(total_tonnage)} кг`\n"
        f"💊 **Креатин принят:** `{creatine_days} из 7 дней`\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

# ==================== 3. СТРУКТУРА ТРЕНИРОВОК ====================
WORKOUT_PLAN = {
    "1 День": (
        "🔴 **ДЕНЬ 1 — Силовой Блок**\n\n"
        "💪 **Жим лежа:** Тяжёлый подход\n"
        "   └ 🛑 _Топ-сет, далее -5 кг в последующих подходах_\n\n"
        "🎯 **Спина:** Средняя нагрузка\n"
        "🔥 **Руки & Плечи:** Лёгкая нагрузка\n"
        "   └ _Бицепс, трицепс, брахиалис, плечи_\n"
        "   └ ⚠️️ *Отказ: 1 подход на бицепс*"
    ),
    "2 День": (
        "🟡 **ДЕНЬ 2 — Объемный Блок**\n\n"
        "💪 **Жим лежа:** Средний вес\n"
        "   └ ➕ _Жим гантелей средней тяжести_\n\n"
        "🎯 **Спина:** Лёгкая нагрузка\n"
        "🔥 **Бицепс:** Тяжёлая нагрузка *(Акцент!)*\n"
        "⚡ **Трицепс & Плечи:** Трицепс лёгкий, плечи средние"
    ),
    "3 День": (
        "🟢 **ДЕНЬ 3 — Выносливость & Спина**\n\n"
        "💪 **Жим лежа:** Лёгкий вес\n"
        "   └ 🗓 _2 недели: 5–8 повторов_\n"
        "   └ 🗓 _3-я неделя: 8–12 повторов_\n\n"
        "🎯 **Спина:** Тяжёлая нагрузка *(Основной акцент)*\n"
        "🔥 **Руки:** Средняя нагрузка *(Бицепс, трицепс, брахиалис, плечи)*"
    )
}

# ==================== 4. НАПОМИНАНИЯ И ТАЙМЕРЫ ====================
def start_rest_timer(chat_id, seconds):
    def timer_thread():
        time.sleep(seconds)
        bot.send_message(
            chat_id,
            f"🔔 **Время отдыха прошло ({int(seconds/60)} мин)!**\nПора делать следующий подход! 💪",
            parse_mode="Markdown"
        )
    threading.Thread(target=timer_thread).start()

def send_water_reminder():
    if not user_chat_id:
        return
    water_goal = get_water_goal()
    water, _ = get_today_stats_db()
    if water < water_goal:
        left = water_goal - water
        bot.send_message(
            user_chat_id,
            f"💧 **Время выпить воды!**\n"
            f"Выпито сегодня: *{water}/{water_goal} мл* (осталось: *{left} мл*).\n"
            f"Жми на кнопку `💧 +250 мл`!",
            parse_mode="Markdown"
        )

def send_creatine_reminder():
    if not user_chat_id:
        return
    _, creatine = get_today_stats_db()
    if not creatine:
        bot.send_message(
            user_chat_id,
            "💊 **Напоминание:** Ты ещё не выпил креатин сегодня!\n"
            "После приема нажми кнопку **💊 Выпил креатин**.",
            parse_mode="Markdown"
        )

def send_weekly_report():
    if user_chat_id:
        bot.send_message(user_chat_id, get_weekly_summary(), parse_mode="Markdown")

scheduler = BackgroundScheduler()
scheduler.add_job(send_water_reminder, 'cron', hour='8-22', minute=0)
scheduler.add_job(send_creatine_reminder, 'cron', hour='9,12,15,18,21', minute=0)
scheduler.add_job(send_weekly_report, 'cron', day_of_week='sun', hour=21, minute=0)
scheduler.start()

# ==================== 5. КЛАВИАТУРЫ И ОБРАБОТЧИКИ ====================
def main_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("💧 +250 мл"), types.KeyboardButton("💧 +500 мл"))
    markup.add(types.KeyboardButton("💊 Выпил креатин"), types.KeyboardButton("📊 Дневной отчет"))
    markup.add(types.KeyboardButton("🏋️ План тренировок"), types.KeyboardButton("📈 График прогресса"))
    markup.add(types.KeyboardButton("🏆 Личные рекорды"), types.KeyboardButton("📉 Недельный отчет"))
    markup.add(types.KeyboardButton("📜 История подходов"), types.KeyboardButton("⚙️ Доп. функции"))
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    save_chat_id(message.chat.id)
    welcome_text = (
        "👋 **Привет! Я твой персональный фитнес-помощник!**\n\n"
        "Я отслеживаю твои подходы, строю графики прогресса и слежу за нормой воды и креатина.\n\n"
        "📝 **Формат записи подхода:**\n"
        "`Упражнение Подход Вес Повторы`\n\n"
        "💡 *Пример:* `Жим 1 80 6`\n\n"
        "👇 Используй меню ниже:"
    )
    bot.send_message(
        message.chat.id, 
        welcome_text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

@bot.message_handler(commands=['set_water'])
def set_water_cmd(message):
    parts = message.text.split()
    if len(parts) == 2 and parts[1].isdigit():
        new_goal = int(parts[1])
        set_water_goal_db(new_goal)
        bot.send_message(message.chat.id, f"✅ Дневная норма воды изменена на *{new_goal} мл*!", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "Используй команду так: `/set_water 3000`", parse_mode="Markdown")

@bot.message_handler(commands=['backup'])
def backup_cmd(message):
    conn = sqlite3.connect("gym_app.db")
    cursor = conn.cursor()
    cursor.execute("SELECT exercise_name, set_num, weight, reps, timestamp FROM workout_logs ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    
    csv_data = "Exercise,Set,Weight,Reps,Timestamp\n"
    for r in rows:
        csv_data += f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]}\n"
        
    buf = io.BytesIO(csv_data.encode('utf-8'))
    buf.name = "workout_history.csv"
    bot.send_document(message.chat.id, document=buf, caption="📁 Ваша полная история подходов (CSV)")

@bot.message_handler(content_types=['text'])
def handle_text(message):
    save_chat_id(message.chat.id)
    text = message.text.strip()
    water_goal = get_water_goal()

    if text in ["💧 +250 мл", "💧 +250мл"]:
        add_water_to_db(250)
        water, _ = get_today_stats_db()
        bot.send_message(
            message.chat.id, 
            f"💧 **Отлично! +250 мл добавлено.**\nВыпито сегодня: *{water} / {water_goal} мл*",
            parse_mode="Markdown"
        )

    elif text in ["💧 +500 мл", "💧 +500мл"]:
        add_water_to_db(500)
        water, _ = get_today_stats_db()
        bot.send_message(
            message.chat.id, 
            f"💧 **Супер! +500 мл добавлено.**\nВыпито сегодня: *{water} / {water_goal} мл*",
            parse_mode="Markdown"
        )

    elif text == "💊 Выпил креатин":
        set_creatine_db()
        bot.send_message(
            message.chat.id, 
            "💊 **Креатин принят!** Напоминания на сегодня отключены.",
            parse_mode="Markdown"
        )

    elif text == "📊 Дневной отчет":
        water, creatine = get_today_stats_db()
        left = max(0, water_goal - water)
        c_status = "✅ Принят" if creatine else "❌ Ещё не принят"
        
        percent = min(100, int((water / water_goal) * 100))
        bars = int(percent / 10)
        progress_bar = "🟦" * bars + "⬜" * (10 - bars)

        report_msg = (
            f"📊 **ДНЕВНОЙ ОТЧЕТ**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💧 **Вода:** {water} / {water_goal} мл\n"
            f"[{progress_bar}] {percent}%\n"
            f"Осталось выпить: *{left} мл*\n\n"
            f"💊 **Креатин:** {c_status}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        bot.send_message(message.chat.id, report_msg, parse_mode="Markdown")

    elif text == "🏋️ План тренировок":
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔴 День 1", callback_data="day_1"),
            types.InlineKeyboardButton("🟡 День 2", callback_data="day_2"),
            types.InlineKeyboardButton("🟢 День 3", callback_data="day_3")
        )
        bot.send_message(message.chat.id, "🏋️ **Выбери день тренировки:**", reply_markup=markup, parse_mode="Markdown")

    elif text == "📈 График прогресса":
        records = get_all_records_db()
        if not records:
            bot.send_message(message.chat.id, "В базе нет сохраненных упражнений.")
        else:
            markup = types.InlineKeyboardMarkup()
            for ex, _ in records:
                markup.add(types.InlineKeyboardButton(f"📊 {ex.title()}", callback_data=f"graph_{ex}"))
            bot.send_message(message.chat.id, "Выбери упражнение для построения графика:", reply_markup=markup)

    elif text == "📉 Недельный отчет":
        bot.send_message(message.chat.id, get_weekly_summary(), parse_mode="Markdown")

    elif text == "🏆 Личные рекорды":
        records = get_all_records_db()
        if not records:
            bot.send_message(message.chat.id, "🏋️‍♂️ В базе пока нет сохранённых упражнений.")
        else:
            msg = "🏆 **ВАШИ ЛИЧНЫЕ РЕКОРДЫ (PR):**\n━━━━━━━━━━━━━━━━━━\n"
            for ex, max_w in records:
                msg += f"🥇 **{ex.title()}**: `{max_w} кг`\n"
            msg += "━━━━━━━━━━━━━━━━━━"
            bot.send_message(message.chat.id, msg, parse_mode="Markdown")

    elif text == "📜 История подходов":
        logs = get_last_logs_db(7)
        if not logs:
            bot.send_message(message.chat.id, "📜 История подходов пока пустая.")
        else:
            msg = "📜 **ПОСЛЕДНИЕ ПОДХОДЫ В БАЗЕ:**\n━━━━━━━━━━━━━━━━━━\n"
            for ex, s_num, w, r, t in logs:
                time_str = t[11:16] if len(t) >= 16 else ""
                msg += f"• **{ex.title()}** | Сет #{s_num}: `{w} кг × {r} повт.` _({time_str})_\n"
            msg += "━━━━━━━━━━━━━━━━━━"
            bot.send_message(message.chat.id, msg, parse_mode="Markdown")

    elif text == "⚙️ Доп. функции":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("❌ Удалить последний подход", callback_data="delete_last"))
        markup.add(types.InlineKeyboardButton("📁 Бэкап базы (CSV)", callback_data="get_backup"))
        bot.send_message(
            message.chat.id, 
            "⚙️ **Дополнительные настройки:**\n\n"
            "• Чтобы изменить норму воды, отправь: `/set_water 3000`",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    elif text.isdigit():
        added = int(text)
        add_water_to_db(added)
        water, _ = get_today_stats_db()
        bot.send_message(
            message.chat.id, 
            f"💧 **Добавлено +{added} мл воды!**\nВсего за сегодня: *{water} / {water_goal} мл*",
            parse_mode="Markdown"
        )

    else:
        parts = text.split()
        if len(parts) >= 4 and parts[-3].isdigit() and parts[-2].replace('.', '', 1).isdigit() and parts[-1].isdigit():
            reps = int(parts[-1])
            weight = float(parts[-2])
            set_num = int(parts[-3])
            ex_name = " ".join(parts[:-3]).lower()
            
            last_set, max_record = log_exercise_db(ex_name, set_num, weight, reps)
            
            progress_msg = ""
            if last_set:
                old_w, old_r = last_set
                if weight > old_w:
                    progress_msg = f"🚀 **Прогресс по весу!** (+{round(weight - old_w, 2)} кг)"
                elif weight < old_w:
                    progress_msg = f"🔻 **Регресс по весу** (-{round(old_w - weight, 2)} кг)"
                else:
                    if reps > old_r:
                        progress_msg = f"🚀 **Прогресс по повторам!** (+{reps - old_r} повт.)"
                    elif reps < old_r:
                        progress_msg = f"🔻 **Регресс по повторам** (-{old_r - reps} повт.)"
                    else:
                        progress_msg = "🎯 **Результат сохранен!**"
            else:
                progress_msg = "🆕 *Первая запись этого подхода!*"
            
            pr_badge = ""
            if max_record is None or weight > max_record:
                pr_badge = "\n🎉 **НОВЫЙ ЛИЧНЫЙ РЕКОРД (PR)!** 🏆"

            # Кнопки для быстрого запуска таймера
            timer_markup = types.InlineKeyboardMarkup()
            timer_markup.add(
                types.InlineKeyboardButton("⏱ 2 мин отдыха", callback_data="timer_120"),
                types.InlineKeyboardButton("⏱ 3 мин отдыха", callback_data="timer_180")
            )

            response = (
                f"✅ **ПОДХОД ЗАПИСАН!**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏋 **Упражнение:** {ex_name.title()}\n"
                f"🔢 **Подход:** #{set_num}\n"
                f"⚖️ **Результат:** `{weight} кг × {reps} повторов`\n\n"
                f"{progress_msg}{pr_badge}\n"
                f"━━━━━━━━━━━━━━━━━━"
            )
            bot.send_message(message.chat.id, response, reply_markup=timer_markup, parse_mode="Markdown")
        else:
            bot.send_message(
                message.chat.id, 
                "⚠️ **Неверный формат!**\n\n"
                "Используй шаблон: `Упражнение Подход Вес Повторы`\n"
                "Пример: `Жим 1 80 6`",
                parse_mode="Markdown"
            )

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data.startswith("day_"):
        day_map = {"day_1": "1 День", "day_2": "2 День", "day_3": "3 День"}
        selected_day = day_map.get(call.data)
        if selected_day:
            bot.send_message(call.message.chat.id, WORKOUT_PLAN[selected_day], parse_mode="Markdown")

    elif call.data.startswith("graph_"):
        ex_name = call.data.replace("graph_", "")
        chart_buf = generate_exercise_chart(ex_name)
        if chart_buf:
            bot.send_photo(call.message.chat.id, photo=chart_buf, caption=f"📈 График прогресса: **{ex_name.title()}**", parse_mode="Markdown")
        else:
            bot.send_message(call.message.chat.id, f"Для построения графика нужно хотя бы 2 записи тренировок в разные дни по упражнению **{ex_name.title()}**.")

    elif call.data.startswith("timer_"):
        seconds = int(call.data.replace("timer_", ""))
        start_rest_timer(call.message.chat.id, seconds)
        bot.answer_callback_query(call.id, f"Таймер на {int(seconds/60)} мин запущен!")
        bot.send_message(call.message.chat.id, f"⏱ **Таймер на {int(seconds/60)} мин запущен.** Отдыхай!", parse_mode="Markdown")

    elif call.data == "delete_last":
        deleted = delete_last_log_db()
        if deleted:
            bot.send_message(
                call.message.chat.id, 
                f"❌ **Удалена запись:** {deleted[1].title()} (Сет #{deleted[2]}): {deleted[3]} кг × {deleted[4]} повт.",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(call.message.chat.id, "В базе нет записей для удаления.")

    elif call.data == "get_backup":
        backup_cmd(call.message)

print("Bot with Full Feature Set running successfully...")
bot.infinity_polling()