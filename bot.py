# bot.py
import asyncio
import os
import csv
import tempfile
from datetime import datetime
from collections import deque
from typing import Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from sqlalchemy import Table, Column, Integer, String, MetaData, create_engine, select, func
from sqlalchemy.exc import SQLAlchemyError
import openpyxl

# ====== ENV ======
API_TOKEN = os.environ.get("API_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "anon_ru_chatik")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not API_TOKEN or not ADMIN_ID or not DATABASE_URL:
    raise ValueError("Не заданы обязательные переменные окружения: API_TOKEN, ADMIN_ID или DATABASE_URL")

try:
    ADMIN_ID = int(ADMIN_ID)
except Exception as e:
    raise ValueError("ADMIN_ID должна быть числом (Telegram user id)") from e

# Для pg8000 SQLAlchemy dialect: заменяем схему, если необходимо
_db_url = DATABASE_URL.strip()
if _db_url.startswith("postgresql+"):
    sa_url = _db_url
elif _db_url.startswith("postgresql://"):
    sa_url = _db_url.replace("postgresql://", "postgresql+pg8000://", 1)
elif _db_url.startswith("postgres://"):
    sa_url = _db_url.replace("postgres://", "postgresql+pg8000://", 1)
else:
    # если пользователь указал уже с pg8000 или иной драйвер — используем как есть
    sa_url = _db_url

# ====== Telegram bot ======
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ====== DB (SQLAlchemy sync, pg8000 driver) ======
metadata = MetaData()

feedback_table = Table(
    "feedback", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer),
    Column("partner_id", Integer, nullable=True),
    Column("feedback", String),
    Column("timestamp", String)
)

blocked_table = Table(
    "blocked_users", metadata,
    Column("user_id", Integer, primary_key=True),
    Column("reason", String),
    Column("timestamp", String)
)

# create sync engine
engine = create_engine(sa_url, future=True, echo=False)
# create tables if not exist
metadata.create_all(engine)

# ====== In-memory structures ======
users = {}           # user_id -> {"gender":..., "mode":..., "partner": ...}
waiting = deque()    # queue of user_id

# ====== States ======
class Register(StatesGroup):
    gender = State()
    age_confirm = State()
    mode = State()

# ====== Keyboards (use keyword text= to avoid Pydantic positional issues) ======
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

# ====== DB helper wrappers (run sync SQLAlchemy in threadpool) ======
def _insert_feedback_sync(user_id: int, partner_id: Optional[int], fb: str):
    try:
        with engine.begin() as conn:
            conn.execute(
                feedback_table.insert().values(
                    user_id=user_id,
                    partner_id=partner_id,
                    feedback=fb,
                    timestamp=datetime.utcnow().isoformat()
                )
            )
    except SQLAlchemyError:
        pass

async def insert_feedback(user_id: int, partner_id: Optional[int], fb: str):
    await asyncio.to_thread(_insert_feedback_sync, user_id, partner_id, fb)

def _count_complaints_sync(partner_id: int) -> int:
    with engine.connect() as conn:
        stmt = select(func.count()).select_from(feedback_table).where(
            feedback_table.c.partner_id == partner_id,
            feedback_table.c.feedback == "🚨 Пожаловаться"
        )
        r = conn.execute(stmt).scalar_one_or_none()
        return int(r) if r is not None else 0

async def count_complaints(partner_id: int) -> int:
    return await asyncio.to_thread(_count_complaints_sync, partner_id)

def _is_blocked_sync(user_id: int) -> bool:
    with engine.connect() as conn:
        stmt = select(blocked_table.c.user_id).where(blocked_table.c.user_id == user_id)
        r = conn.execute(stmt).first()
        return r is not None

async def is_blocked(user_id: int) -> bool:
    return await asyncio.to_thread(_is_blocked_sync, user_id)

def _block_user_sync(user_id: int, reason: str):
    with engine.begin() as conn:
        # insert or replace
        conn.execute(
            blocked_table.insert().prefix_with("OR REPLACE").values(
                user_id=user_id,
                reason=reason,
                timestamp=datetime.utcnow().isoformat()
            )
        )

async def block_user(user_id: int, reason: str):
    await asyncio.to_thread(_block_user_sync, user_id, reason)

def _fetch_feedback_sync(limit: int = 20, filter_fb: Optional[str] = None):
    with engine.connect() as conn:
        stmt = select(feedback_table.c.user_id, feedback_table.c.partner_id, feedback_table.c.feedback, feedback_table.c.timestamp).order_by(feedback_table.c.id.desc()).limit(limit)
        if filter_fb:
            stmt = stmt.where(feedback_table.c.feedback == filter_fb)
        rows = conn.execute(stmt).fetchall()
        return rows

async def fetch_feedback(limit: int = 20, filter_fb: Optional[str] = None):
    return await asyncio.to_thread(_fetch_feedback_sync, limit, filter_fb)

def _fetch_all_feedback_sync():
    with engine.connect() as conn:
        stmt = select(feedback_table)
        return conn.execute(stmt).fetchall()

async def fetch_all_feedback():
    return await asyncio.to_thread(_fetch_all_feedback_sync)

# ====== Subscription check ======
async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        status = getattr(member, "status", "")
        if status in ("left", "kicked"):
            return False
        # sometimes member object has is_member
        is_mem = getattr(member, "is_member", None)
        if is_mem is not None:
            return bool(is_mem)
        return True
    except Exception:
        return False

# ====== Queue helpers ======
def add_to_waiting(uid: int):
    if uid not in users:
        return
    if users[uid].get("partner"):
        return
    if uid in waiting:
        return
    waiting.append(uid)

# ====== Handlers ======
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

# ====== Matching logic ======
async def match_users():
    i = 0
    while i < len(waiting):
        uid1 = waiting[i]
        if uid1 not in users:
            # stale entry
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

            # simple matching: same mode
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

                # remove them from waiting if present
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

# ====== Chat handler ======
@dp.message()
async def chat_handler(message: types.Message):
    uid = message.from_user.id
    if uid not in users:
        return

    partner = users[uid].get("partner")

    # finish / new partner
    if message.text in ["✅ Завершить диалог", "🔄 Новый собеседник"]:
        # notify partner and break partner relation both sides
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

    # feedback / complaint
    if message.text in ["👍", "👎", "🚨 Пожаловаться"]:
        partner_id = partner if partner else None
        await insert_feedback(uid, partner_id, message.text)

        if message.text == "🚨 Пожаловаться" and partner_id:
            complaints = await count_complaints(partner_id)
            if complaints >= 3:
                await block_user(partner_id, "Слишком много жалоб")
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

    # forward message to partner
    if partner and partner in users:
        try:
            await bot.send_message(partner, message.text)
        except Exception:
            try:
                await message.answer("Не удалось отправить сообщение собеседнику.")
            except Exception:
                pass

# ====== Admin commands ======
@dp.message(Command("reports"))
async def admin_reports(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    args = message.text.split()
    filter_type = args[1] if len(args) > 1 else None
    rows = await fetch_feedback(limit=50, filter_fb=filter_type)
    if not rows:
        await message.answer("Нет отзывов по данному фильтру.")
        return

    report_text = "Последние отзывы" + (f" ({filter_type})" if filter_type else "") + ":\n\n"
    for u, p, f, ts in rows:
        report_text += f"👤 {u} → {p if p else '-'} | {f} | {ts}\n"
    await message.answer(report_text)

@dp.message(Command("export"))
async def admin_export_csv(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    rows = await fetch_all_feedback()
    if not rows:
        await message.answer("В базе пока нет отзывов.")
        return

    # write csv to tmp file
    with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8", suffix=".csv") as tmp:
        writer = csv.writer(tmp)
        writer.writerow(["id", "user_id", "partner_id", "feedback", "timestamp"])
        for row in rows:
            writer.writerow([row.id, row.user_id, row.partner_id, row.feedback, row.timestamp])
        tmp_path = tmp.name

    await message.answer_document(FSInputFile(tmp_path))

@dp.message(Command("export_xlsx"))
async def admin_export_xlsx(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    rows = await fetch_all_feedback()
    if not rows:
        await message.answer("В базе пока нет отзывов.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["id", "user_id", "partner_id", "feedback", "timestamp"])
    for row in rows:
        ws.append([row.id, row.user_id, row.partner_id, row.feedback, row.timestamp])

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    await message.answer_document(FSInputFile(tmp.name))

@dp.message(Command("unblock"))
async def admin_unblock(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("У вас нет прав для этой команды.")
        return

    args = message.text.split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: /unblock user_id")
        return
    uid = int(args[1])
    # remove from blocked_users
    def _unblock_sync(u):
        with engine.begin() as conn:
            conn.execute(blocked_table.delete().where(blocked_table.c.user_id == u))
    await asyncio.to_thread(_unblock_sync, uid)
    await message.answer(f"✅ Пользователь {uid} разблокирован.")

# ====== Cleanup ======
async def cleanup():
    try:
        waiting.clear()
        for uid in list(users.keys()):
            users[uid]["partner"] = None
        # dispose engine
        try:
            engine.dispose()
        except Exception:
            pass
    except Exception:
        pass

# ====== Run ======
async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await cleanup()

if __name__ == "__main__":
    asyncio.run(main())
