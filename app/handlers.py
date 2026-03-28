from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy import select
from datetime import datetime, timedelta

from database import AsyncSessionLocal
from models import Task

router = Router()

class AlarmForm(StatesGroup):
    kalkis = State()
    varis = State()
    tarih = State()
    saat = State()
    vagon = State()
    yolcu = State()

def get_task_keyboard(task_id: int, is_active: bool = True):
    builder = InlineKeyboardBuilder()
    durum_text = "⏸ Duraklat" if is_active else "▶️ Devam Et"
    builder.button(text=durum_text, callback_data=f"toggle_{task_id}")
    builder.button(text="🗑️ Sil", callback_data=f"delete_{task_id}")
    builder.adjust(2)
    return builder.as_markup()

@router.message(Command("yeni_alarm"))
async def cmd_yeni_alarm(message: types.Message, state: FSMContext):
    await message.answer("🚂 Kalkış garını girin (Örn: ANKARA GAR):")
    await state.set_state(AlarmForm.kalkis)

@router.message(AlarmForm.kalkis)
async def process_kalkis(message: types.Message, state: FSMContext):
    await state.update_data(kalkis=message.text.upper())
    await message.answer("🎯 Varış garını girin (Örn: ESKİŞEHİR):")
    await state.set_state(AlarmForm.varis)

@router.message(AlarmForm.varis)
async def process_varis(message: types.Message, state: FSMContext):
    await state.update_data(varis=message.text.upper())
    
    # Gelecek 6 günü buton olarak hazırla
    builder = InlineKeyboardBuilder()
    for i in range(6):
        date_str = (datetime.now() + timedelta(days=i)).strftime("%d.%m.%Y")
        builder.button(text=date_str, callback_data=f"date_{date_str}")
    builder.adjust(2)
    
    await message.answer("📅 Tarih seçin:", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.tarih)

@router.callback_query(AlarmForm.tarih, F.data.startswith("date_"))
async def process_tarih(callback: types.CallbackQuery, state: FSMContext):
    tarih = callback.data.split("_")[1]
    await state.update_data(tarih=tarih)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Sabah (06:00-12:00)", callback_data="saat_06:00_12:00")
    builder.button(text="Öğle (12:00-18:00)", callback_data="saat_12:00_18:00")
    builder.button(text="Akşam (18:00-23:59)", callback_data="saat_18:00_23:59")
    builder.button(text="Tüm Gün", callback_data="saat_00:00_23:59")
    builder.adjust(1)
    
    await callback.message.edit_text(f"📅 Seçilen Tarih: {tarih}\n\n⏰ Hangi saat aralığını takip edelim?", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.saat)

@router.callback_query(AlarmForm.saat, F.data.startswith("saat_"))
async def process_saat(callback: types.CallbackQuery, state: FSMContext):
    _, baslangic, bitis = callback.data.split("_")
    await state.update_data(baslangic=baslangic, bitis=bitis)
    
    builder = InlineKeyboardBuilder()
    builder.button(text="Ekonomi", callback_data="vagon_Ekonomi")
    builder.button(text="Business", callback_data="vagon_Business")
    builder.button(text="Loca / Yemekli", callback_data="vagon_Loca")
    builder.button(text="Fark Etmez", callback_data="vagon_Farketmez")
    builder.adjust(2)
    
    await callback.message.edit_text(f"⏰ Saat: {baslangic} - {bitis}\n\n💺 Vagon tipi seçin:", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.vagon)

@router.callback_query(AlarmForm.vagon, F.data.startswith("vagon_"))
async def process_vagon(callback: types.CallbackQuery, state: FSMContext):
    vagon = callback.data.split("_")[1]
    await state.update_data(vagon=vagon)
    
    builder = InlineKeyboardBuilder()
    for i in range(1, 5):
        builder.button(text=f"{i} Kişi", callback_data=f"yolcu_{i}")
    builder.adjust(4)
    
    await callback.message.edit_text(f"💺 Vagon: {vagon}\n\n👥 Kaç kişilik bilet arıyoruz?", reply_markup=builder.as_markup())
    await state.set_state(AlarmForm.yolcu)

@router.callback_query(AlarmForm.yolcu, F.data.startswith("yolcu_"))
async def process_yolcu(callback: types.CallbackQuery, state: FSMContext):
    yolcu = int(callback.data.split("_")[1])
    data = await state.get_data()
    
    async with AsyncSessionLocal() as session:
        new_task = Task(
            kalkis_gar=data['kalkis'],
            varis_gar=data['varis'],
            tarih=data['tarih'],
            baslangic_saati=data['baslangic'],
            bitis_saati=data['bitis'],
            vagon_tipi=data['vagon'],
            yolcu_sayisi=yolcu,
            is_active=True
        )
        session.add(new_task)
        await session.commit()
        
    await callback.message.edit_text(
        f"✅ Alarm Kuruldu!\n\n"
        f"🚂 {data['kalkis']} - {data['varis']}\n"
        f"📅 {data['tarih']} | ⏰ {data['baslangic']}-{data['bitis']}\n"
        f"💺 {data['vagon']} | 👥 {yolcu} Kişi"
    )
    await state.clear()

@router.message(Command("alarmlar"))
async def list_alarms(message: types.Message):
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
            f"📅 {task.tarih} | ⏰ {task.baslangic_saati}-{task.bitis_saati}\n"
            f"💺 {task.vagon_tipi} | 👥 {task.yolcu_sayisi} Kişi\n"
            f"Durum: {durum}"
        )
        await message.answer(text, reply_markup=get_task_keyboard(task.id, task.is_active), parse_mode="HTML")

@router.callback_query(F.data.startswith("toggle_"))
async def process_toggle(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    async with AsyncSessionLocal() as session:
        task = await session.get(Task, task_id)
        if task:
            task.is_active = not task.is_active
            await session.commit()
            await callback.message.edit_reply_markup(reply_markup=get_task_keyboard(task.id, task.is_active))
    await callback.answer("Durum güncellendi!")

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