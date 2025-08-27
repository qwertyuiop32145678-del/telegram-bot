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
from databases import Database
from sqlalchemy import Table, Column, Integer, String, MetaData, create_engine, select

# ====== Переменные окружения ======
API_TOKEN = os.environ.get("API_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "anon_ru_chatik")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not API_TOKEN or not ADMIN_ID or not DATABASE_URL:
    raise ValueError("Не заданы обязательные переменные окружения API_TOKEN, ADMIN_ID или DATABASE_URL")

try:
    ADMIN_ID = int(ADMIN_ID)
except Exception:
    raise ValueError("ADMIN_ID должна быть числом (Telegram user id)")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ====== PostgreSQL / таблицы ======
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

# Создаём (синхронно) таблицы через SQLAlchemy engine (удобно при старте)
engine = create_engine(DATABASE_URL)
metadata.create_all(engine)

# async database
db = Database(DATABASE_URL)

# ====== Очередь пользователей (в памяти) ======
users = {}        # user_id -> {"gender":..., "mode":..., "partner": ...}
waiting = deque() # очередь user_id

# ====== FSM состояния ======
class Register(StatesGroup):
    gender = State()
    age_confirm = State()
    mode = State()

# ====== Клавиатуры ======
gender_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("Мужской")], [KeyboardButton("Женский")]],
    resize_keyboard=True
)
age_confirm_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("18+"), KeyboardButton("Нет")]],
    resize_keyboard=True
)
mode_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("Ролевик"), KeyboardButton("Вирт")],
        [KeyboardButton("Общение")],
        [KeyboardButton("Выбор другого режима")]
    ],
    resize_keyboard=True
)
feedback_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("👍"), KeyboardButton("👎")],
        [KeyboardButton("🚨 Пожаловаться")]
    ],
    resize_keyboard=True
)
chat_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("✅ Завершить диалог")],
        [KeyboardButton("🔄 Новый собеседник")]
    ],
    resize_keyboard=True
)

# ====== Проверка подписки ======
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        # aiogram types ChatMember has .is_member or status, keep robust:
        return getattr(member, "is_member", None) or getattr(member, "status", "") not in ("left", "kicked")
    except Exception:
        # если ошибка (например, бот не админ/канал приватный), считаем неподписанным
        return False

# ====== Проверка блокировки (асинхронно) ======
async def is_blocked(user_id: int) -> bool:
    try:
        row = await db.fetch_one(select([blocked_table.c.user_id]).where(blocked_table.c.user_id == user_id))
        return row is not None
    except Exception:
        return False

# ====== Добавление в очередь (без дубликатов) ======
def add_to_waiting(user_id: int):
    # guard: пользователь должен быть в users
    if user_id not in users:
        return
    if users[user_id].get("partner"):
        return
    if user_id in waiting:
        return
    waiting.append(user_id)

# ====== /start ======
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    # db.connect выполняется в main при старте процесса, не здесь
    uid = message.from_user.id
    if await is_blocked(uid):
        await message.answer("🚫 Вы заблокированы и не можете пользоваться ботом.")
        return

    if not await is_subscribed(uid):
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
        await state.clear()
        return
    await message.answer("Выберите режим общения:", reply_markup=mode_kb)
    await state.set_state(Register.mode)

@dp.message(Register.mode)
async def process_mode(message: types.Message, state: FSMContext):
    data = await state.get_data()
    uid = message.from_user.id
    # сохраняем профиль в памяти (для простоты)
    users[uid] = {
        "gender": data.get("gender", "Не указан"),
        "mode": message.text,
        "partner": None
    }
    add_to_waiting(uid)
    await message.answer(f"Вы выбрали режим: {message.text}. Ждите собеседника…", reply_markup=types.ReplyKeyboardRemove())
    await match_users()
    await state.clear()

# ====== Логика поиска партнёра ======
async def match_users():
    # простая O(n^2) проверка пары в очереди по mode
    i = 0
    while i < len(waiting):
        uid1 = waiting[i]
        # возможно пользователь вышел — защитимся
        if uid1 not in users:
            try:
                waiting.remove(uid1)
            except ValueError:
                pass
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

            # критерий сопоставления: совпадает режим
            if user1["mode"] == user2["mode"]:
                users[uid1]["partner"] = uid2
                users[uid2]["partner"] = uid1

                # уведомляем обоих
                try:
                    await bot.send_message(uid1, f"Найден собеседник! {user2['gender']}", reply_markup=chat_kb)
                    await bot.send_message(uid2, f"Найден собеседник! {user1['gender']}", reply_markup=chat_kb)
                except Exception:
                    # если не удалось доставить – очищаем пары и продолжаем
                    users[uid1]["partner"] = None
                    users[uid2]["partner"] = None
                    continue

                # удаляем их из очереди (если они там)
                try:
                    waiting.remove(uid1)
                except ValueError:
                    pass
                try:
                    waiting.remove(uid2)
                except ValueError:
                    pass

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

    # кнопки завершения / новый партнер
    if message.text in ["✅ Завершить диалог", "🔄 Новый собеседник"]:
        # уведомляем партнёра, разрываем пару у обоих
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

    # обработка отзывов / жалоб
    if message.text in ["👍", "👎", "🚨 Пожаловаться"]:
        partner_id = partner if partner else None
        try:
            await db.execute(
                feedback_table.insert().values(
                    user_id=uid,
                    partner_id=partner_id,
                    feedback=message.text,
                    timestamp=datetime.utcnow().isoformat()
                )
            )
        except Exception:
            # логируем, но не падаем
            pass

        # автоматическая блокировка при >=3 жалоб
        if message.text == "🚨 Пожаловаться" and partner_id:
            try:
                row = await db.fetch_one(
                    "SELECT COUNT(*) as c FROM feedback WHERE partner_id = :pid AND feedback = '🚨 Пожаловаться'",
                    values={"pid": partner_id}
                )
                complaints = int(row["c"]) if row and row.get("c") is not None else 0
            except Exception:
                complaints = 0

            if complaints >= 3:
                try:
                    await db.execute(
                        blocked_table.insert().values(
                            user_id=partner_id,
                            reason="Слишком много жалоб",
                            timestamp=datetime.utcnow().isoformat()
                        )
                    )
                except Exception:
                    pass

                if partner_id in users:
                    try:
                        await bot.send_message(partner_id, "🚫 Вы были автоматически заблокированы из-за большого количества жалоб.")
                    except Exception:
                        pass

                try:
                    await bot.send_message(ADMIN_ID, f"⚠ Пользователь {partner_id} автоматически заблокирован (жалобы: {complaints}).")
                except Exception:
                    pass

        try:
            await message.answer("Спасибо за отзыв!", reply_markup=types.ReplyKeyboardRemove())
        except Exception:
            pass
        return

    # обычная пересылка текста партнёру
    if partner and partner in users:
        try:
            await bot.send_message(partner, message.text)
        except Exception:
            # если не удалось доставить – уведомим отправителя
            try:
                await message.answer("Не удалось отправить сообщение собеседнику.")
            except Exception:
                pass

# ====== Очистка очереди и партнёров при закрытии ======
async def cleanup():
    try:
        waiting.clear()
        for uid in list(users.keys()):
            users[uid]["partner"] = None
        # попробуем отключиться от БД, если подключены
        try:
            await db.disconnect()
        except Exception:
            pass
    except Exception:
        pass

# ====== Запуск ======
async def main():
    # подключаемся к БД один раз при старте
    await db.connect()
    try:
        await dp.start_polling(bot)
    finally:
        await cleanup()

if __name__ == "__main__":
    asyncio.run(main())
