import asyncio
import sqlite3
from datetime import datetime
from collections import deque
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from openpyxl import Workbook
import os

# ====== Переменные окружения ======
API_TOKEN = os.environ.get("API_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))

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
users = {}           # user_id -> данные пользователя
waiting = deque()    # очередь поиска партнера

# ====== FSM состояния ======
class Register(StatesGroup):
    gender = State()
    age = State()
    looking_gender = State()
    looking_age = State()

# ====== Клавиатуры ======
feedback_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("👍"), KeyboardButton("👎")],
              [KeyboardButton("🚨 Пожаловаться")]],
    resize_keyboard=True
)
chat_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("✅ Завершить диалог")],
              [KeyboardButton("🔄 Новый собеседник")]],
    resize_keyboard=True
)

# ====== Проверка блокировки ======
def is_blocked(user_id: int) -> bool:
    cursor.execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

# ====== Добавление в очередь ======
def add_to_waiting(user_id):
    if user_id not in waiting and not users[user_id].get("partner"):
        waiting.append(user_id)

# ====== Команды ======
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    if is_blocked(message.from_user.id):
        await message.answer("🚫 Вы заблокированы и не можете пользоваться ботом.")
        return

    await message.answer("Привет! Укажи свой пол:",
                         reply_markup=ReplyKeyboardMarkup(
                             keyboard=[[KeyboardButton("Мужской")], [KeyboardButton("Женский")]],
                             resize_keyboard=True))
    await state.set_state(Register.gender)

# ====== Админские команды ======
@dp.message(Command("reports"))
async def reports_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    args = message.text.split()
    filter_type = args[1] if len(args) > 1 else None

    query = "SELECT user_id, partner_id, feedback, timestamp FROM feedback"
    params = []

    if filter_type in ["👍","👎","🚨"]:
        query += " WHERE feedback = ?"
        params.append(filter_type)

    query += " ORDER BY id DESC LIMIT 20"
    cursor.execute(query, params)
    rows = cursor.fetchall()

    if not rows:
        await message.answer("Нет отзывов по данному фильтру.")
        return

    text = "Последние 20 отзывов" + (f" ({filter_type})" if filter_type else "") + ":\n\n"
    for u,p,f,t in rows:
        text += f"👤 {u} → {p if p else '-'} | {f} | {t}\n"
    await message.answer(text)

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

# ====== FSM обработчики ======
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
                             keyboard=[[KeyboardButton("Мужской")],[KeyboardButton("Женский")]],
                             resize_keyboard=True))
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
    uid = message.from_user.id
    users[uid] = {
        "gender": data["gender"],
        "age": data["age"],
        "looking_for": {"gender": data["looking_gender"], "min_age": int(message.text)},
        "partner": None
    }

    await message.answer("Отлично! Теперь ты в поиске. Жди собеседника…")
    add_to_waiting(uid)
    await match_users()
    await state.clear()

# ====== Логика поиска партнера ======
async def match_users():
    i = 0
    while i < len(waiting):
        uid1 = waiting[i]
        user1 = users[uid1]
        if user1.get("partner"):
            i += 1
            continue

        paired = False
        for j in range(i+1, len(waiting)):
            uid2 = waiting[j]
            user2 = users[uid2]
            if user2.get("partner"):
                continue

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
                paired = True
                break

        if not paired:
            i += 1

# ====== Переписка, завершение диалога и отзывы ======
@dp.message()
async def chat_handler(message: types.Message):
    uid = message.from_user.id
    if uid not in users:
        return

    partner = users[uid].get("partner")

    if message.text in ["✅ Завершить диалог","🔄 Новый собеседник"]:
        if partner:
            users[partner]["partner"] = None
            await bot.send_message(partner, "Собеседник завершил диалог. Оставьте отзыв:", reply_markup=feedback_kb)
            add_to_waiting(partner)

        users[uid]["partner"] = None
        await bot.send_message(uid, "Диалог завершен. Оставьте отзыв:", reply_markup=feedback_kb)
        add_to_waiting(uid)

        if message.text == "🔄 Новый собеседник":
            await message.answer("Поиск нового собеседника…", reply_markup=types.ReplyKeyboardRemove())
            await match_users()
        return

    if message.text in ["👍","👎","🚨 Пожаловаться"]:
        partner_id = partner if partner else None
        cursor.execute(
            "INSERT INTO feedback (user_id, partner_id, feedback, timestamp) VALUES (?, ?, ?, ?)",
            (uid, partner_id, message.text, datetime.utcnow().isoformat())
        )
        conn.commit()

        if message.text == "🚨 Пожаловаться" and partner_id:
            cursor.execute(
                "SELECT COUNT(*) FROM feedback WHERE partner_id=? AND feedback='🚨 Пожаловаться'",
                (partner_id,)
            )
            complaints = cursor.fetchone()[0]
            if complaints >= 3:
                cursor.execute(
                    "INSERT OR REPLACE INTO blocked_users (user_id, reason, timestamp) VALUES (?, ?, ?)",
                    (partner_id, "Слишком много жалоб", datetime.utcnow().isoformat())
                )
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
