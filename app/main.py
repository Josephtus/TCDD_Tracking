import asyncio
import os
import random
import logging
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from database import init_db, AsyncSessionLocal
from handlers import router, get_or_create_user
from admin_handlers import admin_router
from scraper import check_alarms_grouped
from models import Task, User

load_dotenv()

BOT_TOKEN         = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = str(os.getenv("ADMIN_TELEGRAM_ID"))
LOG_FILE          = os.getenv("LOG_FILE", "bot.log")

# ──────────────────────────────────────────────────────────
# Logging — hem konsol hem dönen dosya (5 MB × 3 yedek)
# ──────────────────────────────────────────────────────────
_fmt = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
_console = logging.StreamHandler()
_console.setFormatter(_fmt)

_file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_console, _file_handler])
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()
dp.include_router(admin_router)   # Admin router önce — öncelik sırası önemli
dp.include_router(router)


# ──────────────────────────────────────────────────────────
# /start — Kullanıcı kayıt & onay akışı
# ──────────────────────────────────────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    tg_user = message.from_user

    # Admin ise direkt karşıla
    if str(tg_user.id) == ADMIN_TELEGRAM_ID:
        await get_or_create_user(tg_user)
        
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Yeni Alarm Kur", callback_data="start_yeni_alarm")
        builder.button(text="📋 Alarmlarım", callback_data="alarmlar_menu")
        builder.button(text="🛡️ Admin Paneli", callback_data="admin_login_info") # Yeni Buton
        builder.adjust(2, 1) # İlk iki buton yan yana, Admin butonu altta tek
        
        await message.answer(
            "👋 <b>Hoş geldin Patron!</b> 🚂\n\n"
            "Aşağıdaki butonları kullanarak işlemlerini hızlıca yapabilirsin.",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        return

    # Normal kullanıcı — DB'ye kaydet veya mevcut durumu getir
    user = await get_or_create_user(tg_user)

    if user.status == "approved":
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Yeni Alarm Kur", callback_data="start_yeni_alarm")
        builder.button(text="📋 Alarmlarım", callback_data="alarmlar_menu")
        builder.adjust(2) # Butonları yan yana dizer

        await message.answer(
            "👋 <b>TCDD Takip Botuna Hoş Geldiniz!</b> 🚂\n\n"
            "Aşağıdaki menüden yapmak istediğiniz işlemi seçebilirsiniz:",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        return

    if user.status == "pending":
        # Kullanıcıya bilgi ver
        await message.answer(
            "⏳ <b>Erişim talebiniz alındı.</b>\n\n"
            "Admin onayı bekleniyor. Onaylandığınızda bildirim alacaksınız.",
            parse_mode="HTML",
        )

        # Admin'e bildirim gönder
        uname = f"@{tg_user.username}" if tg_user.username else "—"
        name  = tg_user.full_name or "İsimsiz"
        notif = (
            f"🆕 <b>Yeni Kullanıcı Kaydı!</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"👤 <b>{name}</b> ({uname})\n"
            f"🆔 <code>{tg_user.id}</code>"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Onayla",   callback_data=f"usr_approve_{tg_user.id}")
        builder.button(text="❌ Reddet",   callback_data=f"usr_reject_{tg_user.id}")
        builder.button(text="🚫 Engelle", callback_data=f"usr_block_{tg_user.id}")
        builder.adjust(2, 1)

        try:
            await bot.send_message(
                chat_id=int(ADMIN_TELEGRAM_ID),
                text=notif,
                parse_mode="HTML",
                reply_markup=builder.as_markup(),
            )
        except Exception as e:
            logger.warning(f"Admin bildirimi gönderilemedi: {e}")

    elif user.status in ("rejected", "blocked"):
        msg_map = {
            "rejected": "❌ Erişim talebiniz reddedildi.",
            "blocked":  "🚫 Hesabınız engellendi. Yöneticiye başvurun.",
        }
        await message.answer(msg_map[user.status])


# ──────────────────────────────────────────────────────────
# Alarm Kontrol Döngüsü (Her 4 dakika)
# ──────────────────────────────────────────────────────────
async def check_all_active_alarms():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).where(Task.is_active == True))
        active_tasks = result.scalars().all()

    if not active_tasks:
        logger.info("ℹ️ Aktif alarm yok, kontrol atlandı.")
        return

    logger.info(f"🔍 {len(active_tasks)} aktif alarm kontrol ediliyor...")

    try:
        results = await check_alarms_grouped(active_tasks)
    except Exception as exc:
        logger.error(f"Toplu sorgu hatası: {exc}")
        return

    for task in active_tasks:
        biletler = results.get(task.id, [])

        if biletler:
            mesaj = (
                f"🚨 <b>BİLET BULUNDU!</b> 🚨\n━━━━━━━━━━━━━━━━\n"
                f"📍 <b>Rota:</b> {task.kalkis_gar} ➔ {task.varis_gar}\n"
                f"📅 <b>Tarih:</b> {task.tarih}\n"
                f"👥 <b>İstenen:</b> {task.yolcu_sayisi} Kişi ({task.vagon_tipi})\n"
                f"━━━━━━━━━━━━━━━━\n\n"
            )
            for b in biletler:
                fiyat_str = (
                    f"{b['fiyat']} ₺"
                    if str(b["fiyat"]).replace(".", "").isdigit()
                    else str(b["fiyat"])
                )
                mesaj += f"🚆 <b>{b['tren_tipi']}</b>\n"
                mesaj += f"🕒 {b['saat']} ➔ {b['varis_saat']}\n"
                mesaj += f"💵 {fiyat_str}\n"
                mesaj += f"💺 {b['bos_koltuk']}\n\n"

            # Bildirimi alarm sahibine gönder (veya admin'e)
            target_chat = ADMIN_TELEGRAM_ID
            if task.user_id:
                async with AsyncSessionLocal() as session:
                    user = await session.get(User, task.user_id)
                    if user and user.status == "approved":
                        target_chat = user.telegram_id

            builder = InlineKeyboardBuilder()
            builder.button(text="⏸ Takibi Durdur", callback_data=f"toggle_{task.id}")
            builder.button(text="✏️ Düzenle",       callback_data=f"edit_{task.id}")
            builder.button(text="🗑️ Sil",           callback_data=f"delete_{task.id}")
            builder.adjust(2, 1)

            try:
                await bot.send_message(
                    chat_id=int(target_chat),
                    text=mesaj,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup(),
                )
            except Exception as exc:
                logger.error(f"Telegram mesaj gönderme hatası (task {task.id}): {exc}")

        # Anti-Bot: Alarmlar arası rastgele bekleme
        jitter = random.uniform(2.5, 6.5)
        logger.debug(f"⏳ Sonraki alarm için {jitter:.1f}s bekleniyor...")
        await asyncio.sleep(jitter)


# ──────────────────────────────────────────────────────────
# Uygulama Başlangıcı
# ──────────────────────────────────────────────────────────
async def main():
    await init_db()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_all_active_alarms,
        "interval",
        minutes=4,
        id="alarm_checker",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("⏰ Alarm zamanlayıcısı başlatıldı — Her 4 dakikada bir kontrol.")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())