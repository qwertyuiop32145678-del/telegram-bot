# bot.py
import asyncio
import os
from datetime import datetime
from collections import deque

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

from sqlalchemy import Table, Column, Integer, String, MetaData, create_engine, select, func

# ====== Переменные окружения ======
API_TOKEN = os.environ.get("API_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "anon_ru_chatik")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not API_TOKEN or not ADMIN_ID:
    raise ValueError("Не заданы обязательные переменные окружения API_TOKEN или ADMIN_ID")

try:
    ADMIN_ID = int(ADMIN_ID)
except Exception:
    raise ValueError("ADMIN_ID должна быть числом (Telegram user id)")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ====== PostgreSQL (SQLAlchemy sync engine) ======
metadata = MetaData()

feedback_table = Table(
    "feedback", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
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

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    engine = None

# ====== Очередь пользователей (в памяти) ======
users = {}
waiting = deque()

# ====== FSM состояния ======
class Register(StatesGroup):
    gender = State()
    age_confirm = State()
    mode = State()

# ====== Клавиатуры (KeyboardButton теперь с keyword arg text=) ======
gender_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Мужской")], [KeyboardButton(text="Женский")]],
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

# ====== DB helper functions (sync via asyncio.to_thread) ======
async def init_db():
    if engine is None:
        print("DB not configured (DATABASE_URL not set). Skipping DB init.")
        return
    try:
        await asyncio.to_thread(metadata.create_all, engine)
        print("DB init done.")
    except Exception as e:
        print("DB init failed:", repr(e))

async def is_blocked(user_id: int) -> bool:
    if engine is None:
        return False
    try:
        def _sync():
            with engine.connect() as conn:
                r = conn.execute(select(blocked_table.c.user_id).where(blocked_table.c.user_id == user_id))
                return r.fetchone()
        row = await asyncio.to_thread(_sync)
        return row is not None
    except Exception as e:
        print("is_blocked error:", repr(e))
        return False

async def insert_feedback(user_id: int, partner_id: int | None, feedback: str):
    if engine is None:
        return
    try:
        def _sync():
            with engine.begin() as conn:
                conn.execute(feedback_table.insert().values(
                    user_id=user_id,
                    partner_id=partner_id,
                    feedback=feedback,
                    timestamp=datetime.utcnow().isoformat()
                ))
        await asyncio.to_thread(_sync)
    except Exception as e:
        print("insert_feedback error:", repr(e))

async def count_complaints_for(partner_id: int) -> int:
    if engine is None:
        return 0
    try:
        def _sync():
            with engine.connect() as conn:
                stmt = select(func.count()).select_from(feedback_table).where(
                    (feedback_table.c.partner_id == partner_id) &
                    (feedback_table.c.feedback == "🚨 Пожаловаться")
                )
                return int(conn.execute(stmt).scalar() or 0)
        return await asyncio.to_thread(_sync)
    except Exception as e:
        print("count_complaints_for error:", repr(e))
        return 0

async def block_user(partner_id: int, reason: str):
    if engine is None:
        return
    try:
        def _sync():
            with engine.begin() as conn:
                conn.execute(blocked_table.insert().values(
                    user_id=partner_id,
                    reason=reason,
                    timestamp=datetime.utcnow().isoformat()
                ))
        await asyncio.to_thread(_sync)
    except Exception as e:
        print("block_user error:", repr(e))

# ====== Проверка подписки ======
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return getattr(member, "is_member", None) or getattr(member, "status", "") not in ("left", "kicked")
    except Exception as e:
        print("is_subscribed error:", repr(e))
        return False

# ====== Очередь и matching ======
def add_to_waiting(user_id: int):
    if user_id not in users:
        return
    if users[user_id].get("partner"):
        return
    if user_id in waiting:
        return
    waiting.append(user_id)

@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if await is_blocked(uid):
        await message.answer("🚫 Вы заблокированы и не можете пользоваться ботом.")
        return

    if not await is_subscribed(uid):
        await message.answer(f"🔔 Пожалуйста, подпишитесь на канал @{CHANNEL_USERNAME} чтобы пользоваться ботом.")
        return

    await message.answer("Привет! Укажи свой пол:", reply_markup=gender_kb)
    await state.set_state(Register.gender)

@dp.message(Register.gender)
async def process_gender(message: types.Message, state: FSMContext):
    await state.update_data(gender=message.text)
    await message.answer("Вы подтверждаете, что вам есть 18 лет?", reply_markup=age_confirm_kb)
    await state.set_state(Register.age_confirm)

@dp.message(Register.age_confirm)
async def process_age_confirm(message: types.Message, state: FSMContext):
    if message.text != "18+":
        await message.answer("Вы должны быть старше 18 лет для использования бота.")
        await state.clear()
        return
    await message.answer("Выберите режим общения:", reply_markup=mode_kb)
    await state.set_state(Register.mode)

@dp.message(Register.mode)
async def process_mode(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = message.from_user.id
    users[uid] = {
        "gender": data.get("gender", "Не указан"),
        "mode": message.text,
        "partner": None
    }
    add_to_waiting(uid)
    await message.answer(f"Вы выбрали режим: {message.text}. Ждите собеседника…", reply_markup=types.ReplyKeyboardRemove())
    await match_users()
    await state.clear()

async def match_users():
    i = 0
    while i < len(waiting):
        uid1 = waiting[i]
        if uid1 not in users:
            try: waiting.remove(uid1)
            except ValueError: pass
            continue

        user1 = users[uid1]
        if user1.get("partner"):
            i += 1
            continue

        paired = False
        for j in range(i + 1, len(waiting)):
            uid2 = waiting[j]
            if uid2 not in users:
                continue
            user2 = users[uid2]
            if user2.get("partner"):
                continue

            if user1["mode"] == user2["mode"]:
                users[uid1]["partner"] = uid2
                users[uid2]["partner"] = uid1
                try:
                    await bot.send_message(uid1, f"Найден собеседник! {user2['gender']}", reply_markup=chat_kb)
                    await bot.send_message(uid2, f"Найден собеседник! {user1['gender']}", reply_markup=chat_kb)
                except Exception:
                    users[uid1]["partner"] = None
                    users[uid2]["partner"] = None
                    continue

                try: waiting.remove(uid1)
                except ValueError: pass
                try: waiting.remove(uid2)
                except ValueError: pass
                paired = True
                break

        if not paired:
            i += 1

@dp.message()
async def chat_handler(message: types.Message):
    uid = message.from_user.id
    if uid not in users:
        return

    partner = users[uid].get("partner")

    if message.text in ["✅ Завершить диалог", "🔄 Новый собеседник"]:
        if partner and partner in users:
            users[partner]["partner"] = None
            try:
                await bot.send_message(partner, "Собеседник завершил диалог. Оставьте отзыв:", reply_markup=feedback_kb)
            except Exception:
                pass
            add_to_waiting(partner)

        users[uid]["partner"] = None
        try:
            await bot.send_message(uid, "Диалог завершен. Оставьте отзыв:", reply_markup=feedback_kb)
        except Exception:
            pass
        add_to_waiting(uid)

        if message.text == "🔄 Новый собеседник":
            await message.answer("Поиск нового собеседника…", reply_markup=types.ReplyKeyboardRemove())
            await match_users()
        return

    if message.text in ["👍", "👎", "🚨 Пожаловаться"]:
        partner_id = partner if partner else None
        await insert_feedback(uid, partner_id, message.text)

        if message.text == "🚨 Пожаловаться" and partner_id:
            complaints = await count_complaints_for(partner_id)
            if complaints >= 3:
                await block_user(partner_id, "Слишком много жалоб")
                if partner_id in users:
                    try: await bot.send_message(partner_id, "🚫 Вы были автоматически заблокированы из-за большого количества жалоб.")
                    except Exception: pass
                try: await bot.send_message(ADMIN_ID, f"⚠ Пользователь {partner_id} автоматически заблокирован (жалобы: {complaints}).")
                except Exception: pass

        try:
            await message.answer("Спасибо за отзыв!", reply_markup=types.ReplyKeyboardRemove())
        except Exception:
            pass
        return

    if partner and partner in users:
        try:
            await bot.send_message(partner, message.text)
        except Exception:
            try: await message.answer("Не удалось отправить сообщение собеседнику.")
            except Exception: pass

async def cleanup():
    try:
        waiting.clear()
        for uid in list(users.keys()):
            users[uid]["partner"] = None
    except Exception as e:
        print("cleanup error:", repr(e))

async def main():
    try:
        await init_db()
    except Exception as e:
        print("init_db error:", repr(e))

    try:
        await dp.start_polling(bot)
    finally:
        await cleanup()

if __name__ == "__main__":
    asyncio.run(main())
