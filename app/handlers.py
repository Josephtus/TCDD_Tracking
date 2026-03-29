import re
import calendar
import json
import logging
import os
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from datetime import datetime, timedelta

from database import AsyncSessionLocal
from models import Task, User

logger = logging.getLogger(__name__)

ADMIN_TELEGRAM_ID = str(os.getenv("ADMIN_TELEGRAM_ID", ""))


async def get_or_create_user(telegram_user: types.User) -> User:
    """Kullanıcıyı DB'den çeker; yoksa pending olarak kaydeder."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == str(telegram_user.id))
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                telegram_id=str(telegram_user.id),
                username=telegram_user.username,
                full_name=telegram_user.full_name,
                status="pending",
                is_admin=(str(telegram_user.id) == ADMIN_TELEGRAM_ID),
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


async def check_access(message_or_callback) -> tuple[bool, User | None]:
    """
    Kullanıcının erişim hakkı var mı kontrol eder.
    Admin her zaman erişebilir.
    Dönüş: (erişim_var_mı, user_nesnesi)
    """
    if isinstance(message_or_callback, types.Message):
        tg_user = message_or_callback.from_user
        reply   = message_or_callback.answer
    else:  # CallbackQuery
        tg_user = message_or_callback.from_user
        reply   = message_or_callback.message.answer

    # Admin her zaman geçer
    if str(tg_user.id) == ADMIN_TELEGRAM_ID:
        # Admin için de user kaydı oluştur (ilk seferde)
        user = await get_or_create_user(tg_user)
        return True, user

    user = await get_or_create_user(tg_user)

    if user.status == "approved":
        return True, user
    elif user.status == "pending":
        await reply(
            "⏳ <b>Erişim talebiniz admin onayı bekliyor.</b>\n"
            "Onaylandığınızda bildirim alacaksınız.",
            parse_mode="HTML",
        )
    elif user.status == "rejected":
        await reply("❌ Erişim talebiniz reddedildi.")
    elif user.status == "blocked":
        await reply("🚫 Hesabınız engellendi. Yöneticiye başvurun.")

    return False, user

router = Router()

STATION_LIST = []
if os.path.exists("app/station_dict.json"):
    with open("app/station_dict.json", "r", encoding="utf-8") as f:
        STATION_LIST = list(json.load(f).keys())

def normalize_tr(text: str) -> str:
    """Türkçe i/İ/ı/I harflerini eşitleyerek küçük harfe çevirir."""
    tr_map = str.maketrans("İIı", "iii")
    return text.translate(tr_map).lower()

class AlarmForm(StatesGroup):
    edit_menu = State()
    kalkis = State()
    kalkis_diger = State()
    varis = State()
    varis_diger = State()
    tarih = State()
    saat_baslangic = State()
    saat_bitis = State()
    vagon = State()
    yolcu = State()

IGNORE_CALLBACK = "ignore"

def get_task_keyboard(task_id: int, is_active: bool = True):
    builder = InlineKeyboardBuilder()
    durum_text = "⏸ Duraklat" if is_active else "▶️ Devam Et"
    builder.button(text=durum_text, callback_data=f"toggle_{task_id}")
    builder.button(text="✏️ Düzenle", callback_data=f"edit_{task_id}")
    builder.button(text="🗑️ Sil", callback_data=f"delete_{task_id}")
    builder.adjust(2, 1)
    return builder.as_markup()

# ─────────────────────────────────────────────────────────────
# DÜZENLEME MENÜSÜ
# ─────────────────────────────────────────────────────────────
async def show_edit_menu(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    builder.button(text="🚂 Rota Değiştir",     callback_data="editf_rota")
    builder.button(text="📅 Tarih Değiştir",    callback_data="editf_tarih")
    builder.button(text="⏰ Saat Değiştir",     callback_data="editf_saat")
    builder.button(text="💺 Vagon Değiştir",    callback_data="editf_vagon")
    builder.button(text="👥 Yolcu Değiştir",    callback_data="editf_yolcu")
    builder.button(text="✅ Kaydet & Aktifleştir", callback_data="editf_save")
    builder.button(text="❌ İptal",             callback_data="cancel_edit")
    builder.adjust(1)

    yolcu_text = str(data.get('yolcu', '?'))
    text = (
        "✏️ <b>Düzenleme Menüsü</b>\n━━━━━━━━━━━━━━━━\n"
        f"📍 <b>{data.get('kalkis')}</b> ➔ <b>{data.get('varis')}</b>\n"
        f"📅 <b>{data.get('tarih')}</b> | ⏰ {data.get('baslangic')} ➔ {data.get('bitis')}\n"
        f"💺 <b>{data.get('vagon')}</b> | 👥 <b>{yolcu_text} Kişi</b>\n\n"
        "Hangi alanı değiştirmek istersiniz?"
    )
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(AlarmForm.edit_menu)

@router.callback_query(AlarmForm.edit_menu, F.data.startswith("editf_"))
async def process_edit_field(callback: types.CallbackQuery, state: FSMContext):
    field = callback.data[6:]   # "editf_" sonrası
    if field == "save":
        await finalize_alarm(callback, state)
        return
    await state.update_data(edit_single=field)
    if field == "rota":
        await show_kalkis(callback, state, is_edit_single=True)
    elif field == "tarih":
        await show_tarih(callback, state, is_edit_single=True)
    elif field == "saat":
        await show_saat_baslangic(callback, state, is_edit_single=True)
    elif field == "vagon":
        await show_vagon(callback, state, is_edit_single=True)
    elif field == "yolcu":
        await show_yolcu(callback, state, is_edit_single=True)

@router.callback_query(F.data == "back_to_edit_menu")
async def back_to_edit_menu(callback: types.CallbackQuery, state: FSMContext):
    await show_edit_menu(callback, state)

@router.callback_query(F.data == "cancel_edit")
async def cancel_edit(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Düzenleme iptal edildi.")

# ─────────────────────────────────────────────────────────────
# KAYIT / FİNALİZE
# ─────────────────────────────────────────────────────────────
async def finalize_alarm(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    yolcu = data.get('yolcu')
    editing_task_id = data.get("editing_task_id")

    # Kullanıcı DB kaydını çek (user_id için)
    tg_user = callback.from_user
    async with AsyncSessionLocal() as session:
        u_result = await session.execute(
            select(User).where(User.telegram_id == str(tg_user.id))
        )
        db_user = u_result.scalar_one_or_none()
        db_user_id = db_user.id if db_user else None

    async with AsyncSessionLocal() as session:
        if editing_task_id:
            task = await session.get(Task, editing_task_id)
            if task:
                task.kalkis_gar      = data['kalkis']
                task.varis_gar       = data['varis']
                task.tarih           = data['tarih']
                task.baslangic_saati = data['baslangic']
                task.bitis_saati     = data['bitis']
                task.vagon_tipi      = data['vagon']
                task.yolcu_sayisi    = yolcu
                task.is_active       = True
                await session.commit()
                text_prefix = "✏️ Alarm Güncellendi ve Aktifleştirildi!"
            else:
                editing_task_id = None

        if not editing_task_id:
            new_task = Task(
                user_id=db_user_id,
                kalkis_gar=data['kalkis'], varis_gar=data['varis'],
                tarih=data['tarih'], baslangic_saati=data['baslangic'],
                bitis_saati=data['bitis'], vagon_tipi=data['vagon'],
                yolcu_sayisi=yolcu, is_active=True
            )
            session.add(new_task)
            await session.commit()
            task_id_for_keyboard = new_task.id
            text_prefix = "✅ Yeni Alarm Kuruldu!"
        else:
            task_id_for_keyboard = editing_task_id

    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Tekrar Düzenle", callback_data=f"edit_{task_id_for_keyboard}")
    builder.button(text="📄 Tüm Alarmlar",  callback_data="alarmlar_menu")

    await callback.message.edit_text(
        f"{text_prefix}\n━━━━━━━━━━━━━━━━\n"
        f"📍 <b>{data['kalkis']}</b> ➔ <b>{data['varis']}</b>\n"
        f"📅 <b>{data['tarih']}</b> | ⏰ {data['baslangic']} ➔ {data['bitis']}\n"
        f"💺 <b>{data['vagon']}</b> | 👥 <b>{yolcu} Kişi</b>",
        parse_mode="HTML", reply_markup=builder.as_markup()
    )
    await state.clear()

# ─────────────────────────────────────────────────────────────
# ADIM 1 – KALKIŞ GARI
# ─────────────────────────────────────────────────────────────
async def show_kalkis(message_or_callback, state: FSMContext, is_edit_single=False):
    builder = InlineKeyboardBuilder()
    for s in ["ANKARA GAR", "ESKİŞEHİR", "ERYAMAN YHT", "İSTANBUL(SÖĞÜTLÜÇEŞME)", "İSTANBUL(PENDİK)"]:
        builder.button(text=s, callback_data=f"kalkis_{s}")
    builder.button(text="🔍 Diğer İstasyon...", callback_data="kalkis_diger")
    if is_edit_single:
        builder.button(text="⬅️ Geri (Menü)", callback_data="back_to_edit_menu")
    builder.adjust(1)

    text = "🚂 Kalkış garını seçin:"
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message_or_callback.answer(text, reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.kalkis)

@router.message(Command("yeni_alarm"))
async def cmd_yeni_alarm(message: types.Message, state: FSMContext):
    ok, user = await check_access(message)
    if not ok:
        return

    # KULLANICI LİMİT KONTROLÜ
    if not user.is_admin:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Task).where(Task.user_id == user.id))
            task_count = len(result.scalars().all())
            if task_count >= 3:
                await message.answer(
                    "❌ <b>Limit Doldu!</b>\nEn fazla 3 adet alarm kurabilirsiniz. Yeni bir alarm kurmak için lütfen /alarmlar menüsünden mevcut alarmlarınızdan birini silin.",
                    parse_mode="HTML"
                )
                return

    await state.clear()
    await show_kalkis(message, state)
    
@router.callback_query(F.data == "start_yeni_alarm")
async def cb_yeni_alarm(callback: types.CallbackQuery, state: FSMContext):
    ok, user = await check_access(callback) 
    if not ok:
        return
        
    # KULLANICI LİMİT KONTROLÜ
    if not user.is_admin:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Task).where(Task.user_id == user.id))
            task_count = len(result.scalars().all())
            if task_count >= 3:
                await callback.answer("❌ Limit Doldu! En fazla 3 adet alarm kurabilirsiniz.", show_alert=True)
                return

    await state.clear()
    await show_kalkis(callback, state)

@router.callback_query(F.data == "back_kalkis")
async def back_kalkis(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await show_kalkis(callback, state, is_edit_single=(data.get("edit_single") == "rota"))

@router.callback_query(F.data == "back_varis")
async def back_varis(callback: types.CallbackQuery, state: FSMContext):
    await show_varis(callback, state)

@router.callback_query(AlarmForm.kalkis, F.data == "kalkis_diger")
async def kalkis_diger(callback: types.CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Geri", callback_data="back_kalkis")
    await callback.message.edit_text(
        "🔍 Kalkış istasyonu adını yazın\n(ör: Konya, Sivas, Kars...):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AlarmForm.kalkis_diger)

@router.message(AlarmForm.kalkis_diger)
async def process_kalkis_diger_search(message: types.Message, state: FSMContext):
    query = message.text
    matches = sorted([s for s in STATION_LIST if normalize_tr(query) in normalize_tr(s)])[:10]
    builder = InlineKeyboardBuilder()
    if not matches:
        builder.button(text="⬅️ Geri", callback_data="back_kalkis")
        builder.adjust(1)
        await message.answer("❌ Eşleşen istasyon bulunamadı. Başka bir isim yazın:", reply_markup=builder.as_markup())
        return
    for m in matches:
        builder.button(text=m, callback_data=f"kalkis_{m}")
    builder.button(text="⬅️ Geri", callback_data="back_kalkis")
    builder.adjust(1)
    await message.answer("Bunu mu demek istediniz?", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.kalkis)

@router.callback_query(AlarmForm.kalkis, F.data.startswith("kalkis_"))
async def process_kalkis(callback: types.CallbackQuery, state: FSMContext):
    kalkis = callback.data.split("_", 1)[1]
    await state.update_data(kalkis=kalkis)
    await show_varis(callback, state)

# ─────────────────────────────────────────────────────────────
# ADIM 2 – VARIŞ GARI
# ─────────────────────────────────────────────────────────────
async def show_varis(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    kalkis = data['kalkis']
    builder = InlineKeyboardBuilder()
    for s in ["ANKARA GAR", "ESKİŞEHİR", "ERYAMAN YHT", "İSTANBUL(SÖĞÜTLÜÇEŞME)", "İSTANBUL(PENDİK)"]:
        if s != kalkis:
            builder.button(text=s, callback_data=f"varis_{s}")
    builder.button(text="🔍 Diğer İstasyon...", callback_data="varis_diger")
    builder.button(text="⬅️ Geri", callback_data="back_kalkis")
    builder.adjust(1)
    await callback.message.edit_text(f"🚂 Kalkış: {kalkis}\n\n🎯 Varış garını seçin:", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.varis)



@router.callback_query(AlarmForm.varis, F.data == "varis_diger")
async def varis_diger(callback: types.CallbackQuery, state: FSMContext):
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Geri", callback_data="back_varis")
    await callback.message.edit_text(
        "🔍 Varış istasyonu adını yazın\n(ör: Karaman, Eskişehir...):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AlarmForm.varis_diger)

@router.message(AlarmForm.varis_diger)
async def process_varis_diger_search(message: types.Message, state: FSMContext):
    query = message.text
    data = await state.get_data()
    kalkis = data.get('kalkis', "")
    matches = sorted([s for s in STATION_LIST if normalize_tr(query) in normalize_tr(s) and s != kalkis])[:10]
    builder = InlineKeyboardBuilder()
    if not matches:
        builder.button(text="⬅️ Geri", callback_data="back_varis")
        builder.adjust(1)
        await message.answer("❌ Eşleşen istasyon bulunamadı. Başka bir isim yazın:", reply_markup=builder.as_markup())
        return
    for m in matches:
        builder.button(text=m, callback_data=f"varis_{m}")
    builder.button(text="⬅️ Geri", callback_data="back_varis")
    builder.adjust(1)
    await message.answer("Varış noktanız hangisi?", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.varis)

@router.callback_query(AlarmForm.varis, F.data.startswith("varis_"))
async def process_varis(callback: types.CallbackQuery, state: FSMContext):
    varis = callback.data.split("_", 1)[1]
    await state.update_data(varis=varis)
    data = await state.get_data()
    if data.get("edit_single") == "rota":
        await show_edit_menu(callback, state)
    else:
        await show_tarih(callback, state)

# ─────────────────────────────────────────────────────────────
# ADIM 3 – TAKVİM
# ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == IGNORE_CALLBACK)
async def process_ignore(callback: types.CallbackQuery):
    await callback.answer()

def generate_calendar(year: int, month: int, back_target: str = "back_varis"):
    inline_keyboard = []
    month_names = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
                   "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
    inline_keyboard.append([InlineKeyboardButton(text=f"{month_names[month]} {year}", callback_data=IGNORE_CALLBACK)])
    days = ["Pt", "Sa", "Ça", "Pe", "Cu", "Ct", "Pz"]
    inline_keyboard.append([InlineKeyboardButton(text=d, callback_data=IGNORE_CALLBACK) for d in days])
    today = datetime.now().date()
    for week in calendar.monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data=IGNORE_CALLBACK))
            else:
                date_obj = datetime(year, month, day)
                if date_obj.date() < today:
                    row.append(InlineKeyboardButton(text=" ", callback_data=IGNORE_CALLBACK))
                else:
                    date_str = date_obj.strftime("%d.%m.%Y")
                    row.append(InlineKeyboardButton(text=str(day), callback_data=f"date_{date_str}"))
        inline_keyboard.append(row)
    prev_m = month - 1 if month > 1 else 12
    prev_y = year if month > 1 else year - 1
    next_m = month + 1 if month < 12 else 1
    next_y = year if month < 12 else year + 1
    inline_keyboard.append([
        InlineKeyboardButton(text="<", callback_data=f"calprev_{prev_y}_{prev_m}"),
        InlineKeyboardButton(text="⬅️ Geri", callback_data=back_target),
        InlineKeyboardButton(text=">", callback_data=f"calnext_{next_y}_{next_m}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

async def show_tarih(message_or_callback, state: FSMContext, year=None, month=None, is_edit_single=False):
    data = await state.get_data()
    if not year or not month:
        now = datetime.now()
        year, month = now.year, now.month
    back_t = "back_to_edit_menu" if (data.get("edit_single") == "tarih" or is_edit_single) else "back_varis"
    text = f"🚂 Rota: {data['kalkis']} ➔ {data['varis']}\n\n📅 Takvimden tarih seçin:"
    markup = generate_calendar(year, month, back_target=back_t)
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=markup)
    else:
        await message_or_callback.answer(text, reply_markup=markup)
    await state.set_state(AlarmForm.tarih)

@router.callback_query(F.data.startswith("calprev_") | F.data.startswith("calnext_"))
async def process_cal_nav(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    y, m = int(parts[1]), int(parts[2])
    data = await state.get_data()
    is_edit = data.get("edit_single") == "tarih"
    await show_tarih(callback, state, year=y, month=m, is_edit_single=is_edit)

@router.callback_query(AlarmForm.tarih, F.data.startswith("date_"))
async def process_tarih(callback: types.CallbackQuery, state: FSMContext):
    tarih = callback.data.split("_")[1]
    await state.update_data(tarih=tarih)
    data = await state.get_data()
    if data.get("edit_single") == "tarih":
        await show_edit_menu(callback, state)
    else:
        await show_saat_baslangic(callback, state)

# ─────────────────────────────────────────────────────────────
# ADIM 4 – SAAT ARALIĞI
# ─────────────────────────────────────────────────────────────
def generate_hours(prefix="start", min_hour=0, back_target="back_tarih"):
    inline_keyboard = []
    if prefix == "start":
        inline_keyboard.append([InlineKeyboardButton(text="🕘 Tüm Gün (00:00-23:59)", callback_data="saat_tumgun")])
    for i in range(0, 24, 4):
        row = []
        for j in range(4):
            h = i + j
            if h < min_hour:
                row.append(InlineKeyboardButton(text=" ", callback_data=IGNORE_CALLBACK))
            else:
                if prefix == "start":
                    text_b = f"{h:02d}:00"
                    cb = f"h_start_{h:02d}:00"
                else:
                    if h == 23:
                        text_b, cb = "23:59", "h_end_23:59"
                    else:
                        text_b = f"{h+1:02d}:00"
                        cb = f"h_end_{h+1:02d}:00"
                row.append(InlineKeyboardButton(text=text_b, callback_data=cb))
        inline_keyboard.append(row)
    inline_keyboard.append([InlineKeyboardButton(text="⬅️ Geri", callback_data=back_target)])
    return InlineKeyboardMarkup(inline_keyboard=inline_keyboard)

async def show_saat_baslangic(callback: types.CallbackQuery, state: FSMContext, is_edit_single=False):
    data = await state.get_data()
    back_t = "back_to_edit_menu" if (data.get("edit_single") == "saat" or is_edit_single) else "back_tarih"
    msg_text = f"📅 Tarih: {data['tarih']}\n\n⏰ Aramanın **BAŞLAYACAĞI** saati seçin:"
    await callback.message.edit_text(msg_text, reply_markup=generate_hours("start", back_target=back_t), parse_mode="Markdown")
    await state.set_state(AlarmForm.saat_baslangic)

@router.callback_query(F.data == "back_saat_start")
async def back_saat_start(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await show_saat_baslangic(callback, state, is_edit_single=(data.get("edit_single") == "saat"))

async def show_saat_bitis(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    baslangic = data['baslangic']
    try:
        min_h = int(baslangic.split(":")[0])
    except:
        min_h = 0
    msg_text = f"⏰ Başlangıç: {baslangic}\n\n🛑 Aramanın **BİTECEĞİ** saati seçin:"
    await callback.message.edit_text(msg_text,
        reply_markup=generate_hours("end", min_hour=min_h, back_target="back_saat_start"),
        parse_mode="Markdown")
    await state.set_state(AlarmForm.saat_bitis)

@router.callback_query(AlarmForm.saat_baslangic, F.data.startswith("h_start_") | (F.data == "saat_tumgun"))
async def process_saat_baslangic(callback: types.CallbackQuery, state: FSMContext):
    if callback.data == "saat_tumgun":
        await state.update_data(baslangic="00:00", bitis="23:59")
        data = await state.get_data()
        if data.get("edit_single") == "saat":
            await show_edit_menu(callback, state)
        else:
            await show_vagon(callback, state)
    else:
        baslangic = callback.data[8:]   # "h_start_" sonrası
        await state.update_data(baslangic=baslangic)
        await show_saat_bitis(callback, state)

@router.callback_query(AlarmForm.saat_bitis, F.data.startswith("h_end_"))
async def process_saat_bitis(callback: types.CallbackQuery, state: FSMContext):
    bitis = callback.data[6:]   # "h_end_" sonrası
    await state.update_data(bitis=bitis)
    data = await state.get_data()
    if data.get("edit_single") == "saat":
        await show_edit_menu(callback, state)
    else:
        await show_vagon(callback, state)

# ─────────────────────────────────────────────────────────────
# ADIM 5 – VAGON TİPİ
# ─────────────────────────────────────────────────────────────
async def show_vagon(callback: types.CallbackQuery, state: FSMContext, is_edit_single=False):
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    builder.button(text="Ekonomi",       callback_data="vagon_Ekonomi")
    builder.button(text="Business",      callback_data="vagon_Business")
    builder.button(text="Loca",          callback_data="vagon_Loca")
    builder.button(text="Tek. Sandalye", callback_data="vagon_Tekerlekli Sandalye")
    builder.button(text="Yataklı",       callback_data="vagon_Yataklı")
    builder.button(text="Fark Etmez",    callback_data="vagon_Fark Etmez")
    back_t = "back_to_edit_menu" if (data.get("edit_single") == "vagon" or is_edit_single) else "back_saat_start"
    builder.button(text="⬅️ Geri", callback_data=back_t)
    builder.adjust(2, 2, 2, 1)
    msg_text = f"⏰ Saat: {data['baslangic']} ➔ {data['bitis']}\n\n💺 Hangi vagon tipini arıyorsunuz?"
    await callback.message.edit_text(msg_text, reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.vagon)

@router.callback_query(F.data == "back_vagon")
async def back_vagon(callback: types.CallbackQuery, state: FSMContext):
    await show_vagon(callback, state)

@router.callback_query(AlarmForm.vagon, F.data.startswith("vagon_"))
async def process_vagon(callback: types.CallbackQuery, state: FSMContext):
    vagon = callback.data.split("_", 1)[1]
    await state.update_data(vagon=vagon)
    data = await state.get_data()
    if data.get("edit_single") == "vagon":
        await show_edit_menu(callback, state)
    else:
        await show_yolcu(callback, state)

# ─────────────────────────────────────────────────────────────
# ADIM 6 – YOLCU SAYISI
# ─────────────────────────────────────────────────────────────
async def show_yolcu(callback: types.CallbackQuery, state: FSMContext, is_edit_single=False):
    data = await state.get_data()
    builder = InlineKeyboardBuilder()
    for i in range(1, 5):
        builder.button(text=f"{i} Kişi", callback_data=f"yolcu_{i}")
    back_t = "back_to_edit_menu" if (data.get("edit_single") == "yolcu" or is_edit_single) else "back_vagon"
    builder.button(text="⬅️ Geri", callback_data=back_t)
    builder.adjust(4, 1)
    await callback.message.edit_text(f"💺 Vagon: {data['vagon']}\n\n👥 Kaç kişilik bilet arıyoruz?", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.yolcu)

@router.callback_query(AlarmForm.yolcu, F.data.startswith("yolcu_"))
async def process_yolcu(callback: types.CallbackQuery, state: FSMContext):
    yolcu = int(callback.data.split("_")[1])
    await state.update_data(yolcu=yolcu)
    data = await state.get_data()
    if data.get("edit_single") == "yolcu":
        await show_edit_menu(callback, state)
    else:
        await finalize_alarm(callback, state)

# ─────────────────────────────────────────────────────────────
# ALARM LİSTESİ
# ─────────────────────────────────────────────────────────────
@router.callback_query(F.data == "alarmlar_menu")
async def alarmlar_menu(callback: types.CallbackQuery):
    await _list_alarms(callback.message)
    await callback.answer()

async def _list_alarms(message: types.Message):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task))
        tasks = result.scalars().all()
    if not tasks:
        await message.answer("Alarmınız yok. /yeni_alarm ile ekleyin.")
        return
    for task in tasks:
        durum = "🟢 Aktif" if task.is_active else "🔴 Duraklatıldı"
        text = (
            f"🚂 <b>{task.kalkis_gar} - {task.varis_gar}</b>\n"
            f"📅 <b>{task.tarih}</b> | ⏰ {task.baslangic_saati} ➔ {task.bitis_saati}\n"
            f"💺 <b>{task.vagon_tipi}</b> | 👥 <b>{task.yolcu_sayisi} Kişi</b>\n"
            f"Durum: {durum}"
        )
        await message.answer(text, reply_markup=get_task_keyboard(task.id, task.is_active), parse_mode="HTML")

@router.message(Command("alarmlar"))
async def cmd_alarmlar(message: types.Message):
    ok, _ = await check_access(message)
    if not ok:
        return
    await _list_alarms(message)

# ─────────────────────────────────────────────────────────────
# TOGGLE / DELETE / EDIT callbacks
# ─────────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("toggle_"))
async def process_toggle(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if task:
            task.is_active = not task.is_active
            await session.commit()
            durum_text = "▶️ Devam Et" if not task.is_active else "⏸ Takibi Durdur"
            toast_text = "⏸ Takip durduruldu." if not task.is_active else "▶️ Takip yeniden başlatıldı."
            # Sadece butonları güncelle, mesaj metnine dokunma
            builder = InlineKeyboardBuilder()
            builder.button(text=durum_text,         callback_data=f"toggle_{task.id}")
            builder.button(text="✏️ Düzenle",       callback_data=f"edit_{task.id}")
            builder.button(text="🗑️ Sil",          callback_data=f"delete_{task.id}")
            builder.adjust(2, 1)
            await callback.message.edit_reply_markup(reply_markup=builder.as_markup())
            await callback.answer(toast_text, show_alert=False)
        else:
            await callback.answer("Alarm bulunamadı!", show_alert=True)

@router.callback_query(F.data.startswith("delete_"))
async def process_delete(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if task:
            await session.delete(task)
            await session.commit()
    await callback.message.delete()
    await callback.answer("Silindi.", show_alert=True)

@router.callback_query(F.data.regexp(r"^edit_\d+$"))
async def process_edit(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if not task:
            await callback.answer("Bu alarm bulunamadı!", show_alert=True)
            return
        await state.clear()
        await state.update_data(
            editing_task_id=task.id,
            kalkis=task.kalkis_gar,
            varis=task.varis_gar,
            tarih=task.tarih,
            baslangic=task.baslangic_saati,
            bitis=task.bitis_saati,
            vagon=task.vagon_tipi,
            yolcu=task.yolcu_sayisi
        )
    await show_edit_menu(callback, state)