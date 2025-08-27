import asyncio
import sqlite3
import csv
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from openpyxl import Workbook

API_TOKEN = "YOUR_BOT_TOKEN"
ADMIN_ID = 123456789  # замените на свой Telegram ID

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ====== База данных ======
conn = sqlite3.connect("bot.db")
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    partner_id INTEGER,
    feedback TEXT,
    timestamp TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS blocked_users (
    user_id INTEGER PRIMARY KEY,
    reason TEXT,
    timestamp TEXT
)
""")
conn.commit()

# ====== Хранилище пользователей ======
users = {}
waiting = []

# ====== FSM состояния ======
class Register(StatesGroup):
    gender = State()
    age = State()
    looking_gender = State()
    looking_age = State()

# ====== Клавиатуры ======
feedback_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="👍"), KeyboardButton(text="👎")],
              [KeyboardButton(text="🚨 Пожаловаться")]],
    resize_keyboard=True
)

chat_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="✅ Завершить диалог")],
              [KeyboardButton(text="🔄 Новый собеседник")]],
    resize_keyboard=True
)

# ====== Проверка блокировки ======
def is_blocked(user_id: int) -> bool:
    cursor.execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

# ====== Команды ======
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    if is_blocked(message.from_user.id):
        await message.answer("🚫 Вы заблокированы и не можете пользоваться ботом.")
        return

    await message.answer("Привет! Укажи свой пол:",
                         reply_markup=ReplyKeyboardMarkup(
                             keyboard=[[KeyboardButton(text="Мужской")], [KeyboardButton(text="Женский")]],
                             resize_keyboard=True
                         ))
    await state.set_state(Register.gender)

# Админ: просмотр жалоб и отзывов
@dp.message(Command("reports"))
async def reports_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    args = message.text.split()
    filter_type = args[1] if len(args) > 1 else None

    query = "SELECT user_id, partner_id, feedback, timestamp FROM feedback"
    params = []

    if filter_type in ["👍", "👎", "🚨"]:
        query += " WHERE feedback = ?"
        params.append(filter_type)

    query += " ORDER BY id DESC LIMIT 20"

    cursor.execute(query, params)
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Нет отзывов по данному фильтру.")
        return

    report_text = "Последние 20 отзывов" + (f" ({filter_type})" if filter_type else "") + ":\n\n"
    for user_id, partner_id, fb, ts in rows:
        report_text += f"👤 {user_id} → {partner_id if partner_id else '-'} | {fb} | {ts}\n"

    await message.answer(report_text)

# Админ: экспорт отзывов в CSV
@dp.message(Command("export"))
async def export_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    cursor.execute("SELECT * FROM feedback")
    rows = cursor.fetchall()

    if not rows:
        await message.answer("В базе пока нет отзывов.")
        return

    filename = "feedback_export.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "user_id", "partner_id", "feedback", "timestamp"])
        writer.writerows(rows)

    file = FSInputFile(filename)
    await message.answer_document(file)

# Админ: экспорт отзывов в Excel
@dp.message(Command("export_xlsx"))
async def export_xlsx_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    cursor.execute("SELECT * FROM feedback")
    rows = cursor.fetchall()

    if not rows:
        await message.answer("В базе пока нет отзывов.")
        return

    wb = Workbook()
    ws = wb.active
    ws.append(["id", "user_id", "partner_id", "feedback", "timestamp"])
    for row in rows:
        ws.append(row)

    filename = "feedback_export.xlsx"
    wb.save(filename)

    file = FSInputFile(filename)
    await message.answer_document(file)

# Админ: разблокировка пользователя
@dp.message(Command("unblock"))
async def unblock_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: /unblock user_id")
        return

    uid = int(args[1])
    cursor.execute("DELETE FROM blocked_users WHERE user_id=?", (uid,))
    conn.commit()
    await message.answer(f"✅ Пользователь {uid} разблокирован.")

@dp.message(Register.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    await message.answer("Укажи свой возраст (числом):", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Register.age)

@dp.message(Register.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите возраст числом!")
        return
    await state.update_data(age=int(message.text))
    await message.answer("Кого ищем? Укажи пол:",
                         reply_markup=ReplyKeyboardMarkup(
                             keyboard=[[KeyboardButton(text="Мужской")], [KeyboardButton(text="Женский")]],
                             resize_keyboard=True
                         ))
    await state.set_state(Register.looking_gender)

@dp.message(Register.looking_gender)
async def process_looking_gender(message: types.Message, state: FSMContext):
    await state.update_data(looking_gender=message.text)
    await message.answer("Укажи минимальный возраст собеседника:")
    await state.set_state(Register.looking_age)

@dp.message(Register.looking_age)
async def process_looking_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Введите возраст числом!")
        return

    if is_blocked(message.from_user.id):
        await message.answer("🚫 Вы заблокированы и не можете искать собеседников.")
        return

    data = await state.get_data()
    user_id = message.from_user.id

    users[user_id] = {
        "gender": data["gender"],
        "age": data["age"],
        "looking_for": {
            "gender": data["looking_gender"],
            "min_age": int(message.text)
        },
        "partner": None
    }

    await message.answer("Отлично! Теперь ты в поиске. Жди собеседника…")
    waiting.append(user_id)

    await match_users()
    await state.clear()

# ====== Логика поиска ======
async def match_users():
    if len(waiting) < 2:
        return

    for i, uid1 in enumerate(waiting):
        user1 = users[uid1]
        for uid2 in waiting[i+1:]:
            user2 = users[uid2]

            if (user1["looking_for"]["gender"] == user2["gender"] and
                user2["looking_for"]["gender"] == user1["gender"] and
                user1["age"] >= user2["looking_for"]["min_age"] and
                user2["age"] >= user1["looking_for"]["min_age"]):

                users[uid1]["partner"] = uid2
                users[uid2]["partner"] = uid1

                await bot.send_message(uid1, f"Найден собеседник! {user2['gender']}, {user2['age']} лет.", reply_markup=chat_kb)
                await bot.send_message(uid2, f"Найден собеседник! {user1['gender']}, {user1['age']} лет.", reply_markup=chat_kb)

                waiting.remove(uid1)
                waiting.remove(uid2)
                return

# ====== Переписка и отзывы ======
@dp.message()
async def chat_handler(message: types.Message):
    uid = message.from_user.id
    if uid not in users:
        return

    partner = users[uid].get("partner")

    if message.text in ["✅ Завершить диалог", "🔄 Новый собеседник"]:
        if partner:
            await bot.send_message(partner, "Собеседник завершил диалог. Оставьте отзыв:", reply_markup=feedback_kb)
            users[partner]["partner"] = None
        await message.answer("Диалог завершен. Оставьте отзыв:", reply_markup=feedback_kb)
        users[uid]["partner"] = None

        if message.text == "🔄 Новый собеседник":
            if not is_blocked(uid):
                waiting.append(uid)
                await message.answer("Поиск нового собеседника…", reply_markup=types.ReplyKeyboardRemove())
                await match_users()
        return

    if message.text in ["👍", "👎", "🚨 Пожаловаться"]:
        partner_id = partner if partner else None
        cursor.execute("INSERT INTO feedback (user_id, partner_id, feedback, timestamp) VALUES (?, ?, ?, ?)",
                       (uid, partner_id, message.text, datetime.utcnow().isoformat()))
        conn.commit()

        # Проверка на жалобы
        if message.text == "🚨 Пожаловаться" and partner_id:
            cursor.execute("SELECT COUNT(*) FROM feedback WHERE partner_id=? AND feedback='🚨 Пожаловаться'", (partner_id,))
            complaints = cursor.fetchone()[0]
            if complaints >= 3:
                cursor.execute("INSERT OR REPLACE INTO blocked_users (user_id, reason, timestamp) VALUES (?, ?, ?)",
                               (partner_id, "Слишком много жалоб", datetime.utcnow().isoformat()))
                conn.commit()
                await bot.send_message(partner_id, "🚫 Вы были автоматически заблокированы из-за большого количества жалоб.")
                await bot.send_message(ADMIN_ID, f"⚠ Пользователь {partner_id} автоматически заблокирован (жалобы: {complaints}).")

        await message.answer("Спасибо за отзыв!", reply_markup=types.ReplyKeyboardRemove())
        return

    if partner:
        await bot.send_message(partner, message.text)

# ====== Запуск ======
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
