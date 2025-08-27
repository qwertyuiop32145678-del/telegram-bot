import asyncio
import os
from datetime import datetime
from collections import deque
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from databases import Database
from sqlalchemy import Table, Column, Integer, String, MetaData, create_engine

# ====== Переменные окружения ======
API_TOKEN = os.environ.get("API_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))
CHANNEL_USERNAME = "anon_ru_chatik"
DATABASE_URL = os.environ.get("DATABASE_URL")

if not API_TOKEN or not ADMIN_ID or not DATABASE_URL:
    raise ValueError("Не заданы обязательные переменные окружения API_TOKEN, ADMIN_ID или DATABASE_URL")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ====== PostgreSQL ======
metadata = MetaData()

feedback_table = Table(
    "feedback", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer),
    Column("partner_id", Integer),
    Column("feedback", String),
    Column("timestamp", String)
)

blocked_table = Table(
    "blocked_users", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("reason", String),
    Column("timestamp", String)
)

engine = create_engine(DATABASE_URL)
metadata.create_all(engine)
db = Database(DATABASE_URL)

# ====== Очередь пользователей ======
users = {}  # user_id -> данные пользователя
waiting = deque()

# ====== FSM состояния ======
class Register(StatesGroup):
    check_subscribe = State()
    gender = State()
    age_confirm = State()
    mode = State()

# ====== Клавиатуры ======
gender_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Мужской")],[KeyboardButton(text="Женский")]],
    resize_keyboard=True
)
age_confirm_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="18+"), KeyboardButton(text="Нет")]],
    resize_keyboard=True
)
mode_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Ролевик"), KeyboardButton(text="Вирт")],
        [KeyboardButton(text="Общение")],
        [KeyboardButton(text="Выбор другого режима")]
    ],
    resize_keyboard=True
)
feedback_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👍"), KeyboardButton(text="👎")],
        [KeyboardButton(text="🚨 Пожаловаться")]
    ],
    resize_keyboard=True
)
chat_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Завершить диалог")],
        [KeyboardButton(text="🔄 Новый собеседник")]
    ],
    resize_keyboard=True
)

# ====== Проверка подписки ======
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return member.is_member
    except:
        return False

# ====== Проверка блокировки ======
async def is_blocked(user_id: int) -> bool:
    row = await db.fetch_one(blocked_table.select().where(blocked_table.c.user_id == user_id))
    return row is not None

# ====== Добавление в очередь ======
def add_to_waiting(user_id):
    if user_id not in waiting and not users[user_id].get("partner"):
        waiting.append(user_id)

# ====== Команда /start ======
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await db.connect()
    if await is_blocked(message.from_user.id):
        await message.answer("🚫 Вы заблокированы и не можете пользоваться ботом.")
        return

    if not await is_subscribed(message.from_user.id):
        await message.answer(f"🔔 Пожалуйста, подпишитесь на канал @{CHANNEL_USERNAME} чтобы пользоваться ботом.")
        return

    await message.answer("Привет! Укажи свой пол:", reply_markup=gender_kb)
    await state.set_state(Register.gender)

# ====== FSM обработчики ======
@dp.message(Register.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    await message.answer("Вы подтверждаете, что вам есть 18 лет?", reply_markup=age_confirm_kb)
    await state.set_state(Register.age_confirm)

@dp.message(Register.age_confirm)
async def process_age_confirm(message: types.Message, state: FSMContext):
    if message.text != "18+":
        await message.answer("Вы должны быть старше 18 лет для использования бота.")
        return
    await message.answer("Выберите режим общения:", reply_markup=mode_kb)
    await state.set_state(Register.mode)

@dp.message(Register.mode)
async def process_mode(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = message.from_user.id
    users[uid] = {
        "gender": data["gender"],
        "mode": message.text,
        "partner": None
    }
    add_to_waiting(uid)
    await message.answer(f"Вы выбрали режим: {message.text}. Ждите собеседника…", reply_markup=types.ReplyKeyboardRemove())
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
        for j in range(i + 1, len(waiting)):
            uid2 = waiting[j]
            user2 = users[uid2]
            if user2.get("partner"):
                continue

            if user1["mode"] == user2["mode"]:
                users[uid1]["partner"] = uid2
                users[uid2]["partner"] = uid1

                try:
                    await bot.send_message(uid1, f"Найден собеседник! {user2['gender']}", reply_markup=chat_kb)
                    await bot.send_message(uid2, f"Найден собеседник! {user1['gender']}", reply_markup=chat_kb)
                except:
                    pass

                waiting.remove(uid1)
                waiting.remove(uid2)
                paired = True
                break

        if not paired:
            i += 1

# ====== Переписка и отзывы ======
@dp.message()
async def chat_handler(message: types.Message):
    uid = message.from_user.id
    if uid not in users:
        return

    partner = users[uid].get("partner")

    if message.text in ["✅ Завершить диалог", "🔄 Новый собеседник"]:
        if partner and partner in users:
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

    if message.text in ["👍", "👎", "🚨 Пожаловаться"]:
        partner_id = partner if partner else None
        await db.execute(feedback_table.insert().values(
            user_id=uid,
            partner_id=partner_id,
            feedback=message.text,
            timestamp=datetime.utcnow().isoformat()
        ))

        if message.text == "🚨 Пожаловаться" and partner_id:
            row = await db.fetch_one(f"SELECT COUNT(*) as c FROM feedback WHERE partner_id={partner_id} AND feedback='🚨 Пожаловаться'")
            complaints = row['c'] if row else 0
            if complaints >= 3:
                await db.execute(blocked_table.insert().values(
                    user_id=partner_id,
                    reason="Слишком много жалоб",
                    timestamp=datetime.utcnow().isoformat()
                ))
                if partner_id in users:
                    await bot.send_message(partner_id, "🚫 Вы были автоматически заблокированы из-за большого количества жалоб.")
                await bot.send_message(ADMIN_ID, f"⚠ Пользователь {partner_id} автоматически заблокирован (жалобы: {complaints}).")

        await message.answer("Спасибо за отзыв!", reply_markup=types.ReplyKeyboardRemove())
        return

    if partner and partner in users:
        try:
            await bot.send_message(partner, message.text)
        except:
            pass

# ====== Запуск ======
async def main():
    await db.connect()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
