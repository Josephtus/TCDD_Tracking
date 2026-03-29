"""
admin_handlers.py — Admin Panel & Kullanıcı Yönetimi
======================================================
Özellikler:
  - /admin_panel <şifre>  → Dashboard açar
  - 👥 Bekleyen kullanıcılar (Onayla / Reddet)
  - 🚫 Onaylı kullanıcı listesi (Engelle)
  - 🔍 Aktif alarmlar (kim kurmuş + admin silme)
  - 📝 Son 50 log satırı (Telegram mesajı)
"""

import os
import logging
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from database import AsyncSessionLocal
from models import Task, User

logger = logging.getLogger(__name__)
admin_router = Router()

ADMIN_ID     = str(os.getenv("ADMIN_TELEGRAM_ID", ""))
ADMIN_PASS   = str(os.getenv("ADMIN_PASSWORD", "supersecret"))
LOG_FILE     = os.getenv("LOG_FILE", "bot.log")
LOG_LINES    = 50


# ──────────────────────────────────────────────────────────
# Yardımcı: Admin kontrolü
# ──────────────────────────────────────────────────────────
def is_admin(user_id: int | str) -> bool:
    return str(user_id) == ADMIN_ID


# ──────────────────────────────────────────────────────────
# Ana Dashboard Klavyesi
# ──────────────────────────────────────────────────────────
def admin_dashboard_markup() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 Bekleyen Kullanıcılar", callback_data="adm_pending")
    builder.button(text="🚫 Kullanıcı Yönetimi",   callback_data="adm_users")
    builder.button(text="🔍 Aktif Alarmlar",        callback_data="adm_alarms")
    builder.button(text="📝 Logları Görüntüle",     callback_data="adm_logs")
    builder.adjust(1)
    return builder.as_markup()


# ──────────────────────────────────────────────────────────
# /admin_panel <şifre>
# ──────────────────────────────────────────────────────────
@admin_router.message(Command("admin_panel"))
async def cmd_admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Bu komuta erişim yetkiniz yok.")
        return

    parts = message.text.split(maxsplit=1)
    given_pass = parts[1].strip() if len(parts) > 1 else ""

    if given_pass != ADMIN_PASS:
        await message.answer("🔐 Hatalı şifre. Kullanım: `/admin_panel <şifre>`", parse_mode="Markdown")
        return

    await message.answer(
        "🛡️ <b>Admin Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        "Aşağıdaki menüden bir işlem seçin:",
        parse_mode="HTML",
        reply_markup=admin_dashboard_markup(),
    )


# ──────────────────────────────────────────────────────────
# Dashboard callback giriş noktası (sadece admin)
# ──────────────────────────────────────────────────────────
@admin_router.callback_query(F.data.startswith("adm_"))
async def admin_callback_gate(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Yetkisiz erişim!", show_alert=True)
        return

    action = callback.data  # "adm_pending", "adm_users", ...

    if action == "adm_pending":
        await _show_pending_users(callback)
    elif action == "adm_users":
        await _show_all_users(callback)
    elif action == "adm_alarms":
        await _show_active_alarms(callback)
    elif action == "adm_logs":
        await _send_logs(callback)
    elif action == "adm_back":
        await callback.message.edit_text(
            "🛡️ <b>Admin Dashboard</b>\n━━━━━━━━━━━━━━━━\nBir işlem seçin:",
            parse_mode="HTML",
            reply_markup=admin_dashboard_markup(),
        )
    else:
        await callback.answer()


# ──────────────────────────────────────────────────────────
# 👥 Bekleyen Kullanıcılar
# ──────────────────────────────────────────────────────────
async def _show_pending_users(callback: types.CallbackQuery):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.status == "pending").order_by(User.created_at)
        )
        users = result.scalars().all()

    await callback.answer()

    if not users:
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Geri", callback_data="adm_back")
        await callback.message.edit_text(
            "👥 <b>Bekleyen Kullanıcı Yok</b>\nHerkes onaylanmış veya reddedilmiş.",
            parse_mode="HTML", reply_markup=builder.as_markup()
        )
        return

    # Her kullanıcı için ayrı mesaj gönder (edit mesajı bazen sınırlı)
    await callback.message.edit_text(
        f"👥 <b>{len(users)} bekleyen kullanıcı:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardBuilder()
                     .button(text="⬅️ Geri", callback_data="adm_back")
                     .as_markup(),
    )

    for user in users:
        uname = f"@{user.username}" if user.username else "—"
        name  = user.full_name or "İsimsiz"
        text  = (
            f"👤 <b>{name}</b> ({uname})\n"
            f"🆔 <code>{user.telegram_id}</code>\n"
            f"📅 {user.created_at.strftime('%d.%m.%Y %H:%M')}"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Onayla",      callback_data=f"usr_approve_{user.telegram_id}")
        builder.button(text="❌ Reddet",      callback_data=f"usr_reject_{user.telegram_id}")
        builder.button(text="🚫 Engelle",    callback_data=f"usr_block_{user.telegram_id}")
        builder.adjust(2, 1)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ──────────────────────────────────────────────────────────
# 🚫 Onaylı Kullanıcı Listesi
# ──────────────────────────────────────────────────────────
async def _show_all_users(callback: types.CallbackQuery):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.status == "approved").order_by(User.created_at)
        )
        users = result.scalars().all()

    await callback.answer()

    if not users:
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Geri", callback_data="adm_back")
        await callback.message.edit_text(
            "🚫 <b>Onaylı kullanıcı bulunamadı.</b>",
            parse_mode="HTML", reply_markup=builder.as_markup()
        )
        return

    await callback.message.edit_text(
        f"🚫 <b>{len(users)} onaylı kullanıcı:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardBuilder()
                     .button(text="⬅️ Geri", callback_data="adm_back")
                     .as_markup(),
    )

    for user in users:
        uname = f"@{user.username}" if user.username else "—"
        name  = user.full_name or "İsimsiz"
        text  = f"✅ <b>{name}</b> ({uname})\n🆔 <code>{user.telegram_id}</code>"
        builder = InlineKeyboardBuilder()
        builder.button(text="🚫 Engelle", callback_data=f"usr_block_{user.telegram_id}")
        builder.button(text="❌ Reddet",  callback_data=f"usr_reject_{user.telegram_id}")
        builder.adjust(2)
        await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ──────────────────────────────────────────────────────────
# 🔍 Aktif Alarmlar (kim kurmuş)
# ──────────────────────────────────────────────────────────
async def _show_active_alarms(callback: types.CallbackQuery):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Task, User)
            .outerjoin(User, Task.user_id == User.id)
            .where(Task.is_active == True)
        )
        rows = result.all()

    await callback.answer()

    if not rows:
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ Geri", callback_data="adm_back")
        await callback.message.edit_text(
            "🔍 <b>Aktif alarm bulunamadı.</b>",
            parse_mode="HTML", reply_markup=builder.as_markup()
        )
        return

    await callback.message.edit_text(
        f"🔍 <b>{len(rows)} aktif alarm:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardBuilder()
                     .button(text="⬅️ Geri", callback_data="adm_back")
                     .as_markup(),
    )

    for task, user in rows:
        if user:
            uname = f"@{user.username}" if user.username else user.full_name or "—"
            owner = f"{uname} (<code>{user.telegram_id}</code>)"
        else:
            owner = "<i>Admin</i>"

        text = (
            f"🚆 <b>{task.kalkis_gar} ➔ {task.varis_gar}</b>\n"
            f"📅 {task.tarih} | ⏰ {task.baslangic_saati}–{task.bitis_saati}\n"
            f"💺 {task.vagon_tipi} | 👥 {task.yolcu_sayisi} kişi\n"
            f"👤 Kuran: {owner}"
        )
        builder = InlineKeyboardBuilder()
        builder.button(text="🗑️ Sil (Admin)", callback_data=f"adm_del_task_{task.id}")
        await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ──────────────────────────────────────────────────────────
# 📝 Log Görüntüle
# ──────────────────────────────────────────────────────────
async def _send_logs(callback: types.CallbackQuery):
    await callback.answer()
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail = "".join(lines[-LOG_LINES:])
        if not tail.strip():
            tail = "Log dosyası boş."
    except FileNotFoundError:
        tail = f"⚠️ Log dosyası bulunamadı: {LOG_FILE}"
    except Exception as e:
        tail = f"❌ Log okuma hatası: {e}"

    # 4096 karakter sınırını aş — varsa bölüp gönder
    max_len = 4000
    chunks = [tail[i:i+max_len] for i in range(0, len(tail), max_len)]

    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Geri", callback_data="adm_back")

    for i, chunk in enumerate(chunks):
        markup = builder.as_markup() if i == len(chunks) - 1 else None
        await callback.message.answer(
            f"<pre>{chunk}</pre>",
            parse_mode="HTML",
            reply_markup=markup,
        )


# ──────────────────────────────────────────────────────────
# Kullanıcı Durum Değiştirme Callbacks
# ──────────────────────────────────────────────────────────
@admin_router.callback_query(F.data.startswith("usr_"))
async def admin_user_action(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Yetkisiz!", show_alert=True)
        return

    parts       = callback.data.split("_", 2)   # "usr", "action", "telegram_id"
    action      = parts[1]   # approve / reject / block
    telegram_id = parts[2]

    status_map = {"approve": "approved", "reject": "rejected", "block": "blocked"}
    new_status = status_map.get(action)

    if not new_status:
        await callback.answer("Bilinmeyen işlem.", show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            await callback.answer("Kullanıcı bulunamadı!", show_alert=True)
            return
        user.status = new_status
        await session.commit()
        uname = f"@{user.username}" if user.username else user.full_name or telegram_id

    emoji_map  = {"approved": "✅", "rejected": "❌", "blocked": "🚫"}
    label_map  = {"approved": "Onaylandı", "rejected": "Reddedildi", "blocked": "Engellendi"}
    await callback.message.edit_text(
        f"{emoji_map[new_status]} <b>{uname}</b> → {label_map[new_status]}",
        parse_mode="HTML",
    )
    await callback.answer(f"{label_map[new_status]}!", show_alert=False)

    # Kullanıcıya bildir
    try:
        notify_map = {
            "approved": "✅ Erişiminiz onaylandı! Artık botu kullanabilirsiniz. /alarmlar",
            "rejected": "❌ Erişim talebiniz reddedildi.",
            "blocked":  "🚫 Hesabınız engellendi. Yardım için yöneticiye başvurun.",
        }
        from aiogram import Bot
        bot = Bot(token=os.getenv("BOT_TOKEN"))
        await bot.send_message(chat_id=int(telegram_id), text=notify_map[new_status])
        await bot.session.close()
    except Exception as e:
        logger.warning(f"Kullanıcı bildirimi gönderilemedi ({telegram_id}): {e}")


# ──────────────────────────────────────────────────────────
# Admin — Başkasının Alarmını Silme
# ──────────────────────────────────────────────────────────
@admin_router.callback_query(F.data.startswith("adm_del_task_"))
async def admin_delete_task(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔ Yetkisiz!", show_alert=True)
        return

    task_id = int(callback.data.split("_")[-1])
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if task:
            await session.delete(task)
            await session.commit()
            await callback.message.edit_text("🗑️ <b>Alarm silindi.</b>", parse_mode="HTML")
            await callback.answer("Silindi.", show_alert=False)
        else:
            await callback.answer("Alarm bulunamadı!", show_alert=True)
