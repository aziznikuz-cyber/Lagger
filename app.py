# language: Python, file: app.py
# target: Render Web Service, Python 3.10+
# deps: aiogram, flask
import asyncio, logging, os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from flask import Flask

# ===== ВПИСАНО =====
BOT_TOKEN = "9249901689:AAGkOQfYR__qglgyFv-LsK4tR1p0ZMdt1wQ"
ADMIN_ID = 8234498887
# ===================

logging.basicConfig(level=logging.INFO)

# --- Flask для Render ---
web = Flask(__name__)

@web.route("/")
def home(): return "Spammer is running"

@web.route("/health")
def health(): return "OK"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    web.run(host="0.0.0.0", port=port)

# --- Логика бота ---
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class Spam(StatesGroup):
    waiting_targets = State()
    waiting_text = State()

spam_data = {
    "targets": [],
    "text": "",
    "photo_id": None,
    "is_running": False,
    "stats": {"sent": 0, "blocked": 0, "errors": 0}
}

def is_admin(uid): return uid == ADMIN_ID

def kb_admin():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать", callback_data="start_spam")],
        [InlineKeyboardButton(text="⏹ Остановить", callback_data="stop_spam")]
    ])

@dp.message(CommandStart())
async def cmd_start(m: Message):
    if not is_admin(m.from_user.id): return
    await m.answer(
        "Рассыльщик готов.\n"
        "1. Отправь список ID (каждый с новой строки или через запятую).\n"
        "2. Отправь текст.\n"
        "3. Нажми 'Начать'.\n\n"
        "Сброс базы: /reset",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Загрузить ID", callback_data="load_targets")]])
    )

@dp.callback_query(F.data == "load_targets")
async def cb_load(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await cb.message.answer("Отправь ID через запятую, с новой строки или .txt файлом.")
    await state.set_state(Spam.waiting_targets)
    await cb.answer()

@dp.message(Spam.waiting_targets)
async def get_targets(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    targets = []
    if m.document:
        if not m.document.file_name.endswith('.txt'):
            return await m.answer("Только .txt файлы.")
        file = await bot.get_file(m.document.file_id)
        content = await bot.download_file(file.file_path)
        text = content.read().decode('utf-8')
        lines = text.replace(',', '\n').split('\n')
        targets = [int(x.strip()) for x in lines if x.strip().isdigit()]
    elif m.text:
        lines = m.text.replace(',', '\n').split('\n')
        targets = [int(x.strip()) for x in lines if x.strip().isdigit()]
        
    if not targets:
        return await m.answer("Не найдено корректных ID. Попробуй снова.")
        
    spam_data["targets"] = list(set(targets))
    await m.answer(f"Загружено {len(spam_data['targets'])} ID.\nОтправь текст сообщения.")
    await state.set_state(Spam.waiting_text)

@dp.message(Spam.waiting_text)
async def get_text(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    spam_data["text"] = m.html_text
    spam_data["photo_id"] = m.photo[-1].file_id if m.photo else None
    await m.answer(f"Текст сохранен. База: {len(spam_data['targets'])} ID.", reply_markup=kb_admin())
    await state.clear()

@dp.callback_query(F.data == "start_spam")
async def cb_start(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    if spam_data["is_running"]: return await cb.answer("Уже запущено!", show_alert=True)
    if not spam_data["targets"] or not spam_data["text"]:
        return await cb.answer("Загрузи базу и текст!", show_alert=True)
        
    spam_data["is_running"] = True
    spam_data["stats"] = {"sent": 0, "blocked": 0, "errors": 0}
    await cb.message.answer("🚀 Рассылка запущена в фоне...")
    await cb.answer()
    asyncio.create_task(run_spam(cb.message))

async def run_spam(status_msg):
    stats = spam_data["stats"]
    targets = spam_data["targets"]
    text = spam_data["text"]
    photo = spam_data.get("photo_id")
    
    for i, uid in enumerate(targets):
        if not spam_data["is_running"]:
            await status_msg.answer("⏹ Остановлено вручную.")
            break
            
        try:
            if photo:
                await bot.send_photo(uid, photo=photo, caption=text, parse_mode="HTML")
            else:
                await bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True)
            stats["sent"] += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            try:
                if photo: await bot.send_photo(uid, photo=photo, caption=text, parse_mode="HTML")
                else: await bot.send_message(uid, text, parse_mode="HTML")
                stats["sent"] += 1
            except: stats["errors"] += 1
        except TelegramForbiddenError:
            stats["blocked"] += 1
        except TelegramBadRequest:
            stats["errors"] += 1
        except Exception as e:
            stats["errors"] += 1
            
        await asyncio.sleep(0.05)
        
        if (i + 1) % 50 == 0:
            try:
                await status_msg.edit_text(f"⏳ {i+1}/{len(targets)}\n✅ {stats['sent']} | 🚫 {stats['blocked']} | ⚠️ {stats['errors']}")
            except: pass

    spam_data["is_running"] = False
    await status_msg.answer(f"🏁 Готово!\n✅ Доставлено: {stats['sent']}\n🚫 Блок: {stats['blocked']}\n⚠️ Ошибки: {stats['errors']}")

@dp.callback_query(F.data == "stop_spam")
async def cb_stop(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    spam_data["is_running"] = False
    await cb.answer("Останавливаю...", show_alert=True)

@dp.message(Command("reset"))
async def cmd_reset(m: Message):
    if not is_admin(m.from_user.id): return
    spam_data["targets"] = []
    spam_data["text"] = ""
    spam_data["photo_id"] = None
    await m.answer("Очищено.")

# --- Запуск ---
async def main():
    threading.Thread(target=run_web, daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())