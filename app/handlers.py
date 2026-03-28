from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Router, main.py'deki Dispatcher'ın yardımcısıdır. Kodlarımızı modüler tutar.
router = Router()

def get_task_keyboard(task_id: int, is_active: bool = True):
    builder = InlineKeyboardBuilder()
    
    # Durum butonunun metnini belirleme (Aktifse Duraklat yazsın, değilse Devam Et)
    durum_text = "⏸ Duraklat" if is_active else "▶️ Devam Et"
    
    # callback_data kısmı, butona tıklanınca arkada bota giden gizli koddur
    builder.button(text=durum_text, callback_data=f"toggle_{task_id}")
    builder.button(text="⭐ Öncelik", callback_data=f"priority_{task_id}")
    builder.button(text="🗑️ Sil", callback_data=f"delete_{task_id}")
    
    # Butonları yan yana 2'li, alt satıra 1'li dizmek için:
    builder.adjust(2, 1)
    return builder.as_markup()

# Kullanıcı /alarmlar yazdığında çalışacak komut
@router.message(Command("alarmlar"))
async def list_alarms(message: types.Message):
    # İleride burayı veritabanından (MySQL) çekeceğiz, şimdilik tasarımı görmek için statik yazıyoruz
    alarm_text = (
        "🚂 <b>Ankara - İstanbul (Söğütlüçeşme)</b>\n"
        "📅 Tarih: 15 Nisan\n"
        "⏰ Saat Aralığı: 06:00 - 12:00\n"
        "⏱️ Kontrol: 1 Dakikada Bir\n"
        "Durum: 🟢 Aktif (Aranıyor...)"
    )
    
    # Mesajı gönderirken tasarladığımız butonları da (reply_markup) ekliyoruz
    await message.answer(
        alarm_text, 
        reply_markup=get_task_keyboard(task_id=1, is_active=True),
        parse_mode="HTML"
    )

# Kullanıcı butonlara tıkladığında devreye girecek yakalayıcılar (Callback Queries)
@router.callback_query(F.data.startswith("toggle_"))
async def process_toggle(callback: types.CallbackQuery):
    task_id = callback.data.split("_")[1]
    await callback.message.answer(f"✅ {task_id} ID'li alarmın durumu değiştirildi!")
    # Tıklama sonrası butondaki yükleniyor (saat) ikonunu durdurmak için zorunlu:
    await callback.answer() 
    
@router.callback_query(F.data.startswith("delete_"))
async def process_delete(callback: types.CallbackQuery):
    task_id = callback.data.split("_")[1]
    # Sil butonuna basılınca alarm kartını mesaj geçmişinden tamamen sil!
    await callback.message.delete() 
    # Ekrana pop-up uyarı çıkar
    await callback.answer(f"🗑️ {task_id} ID'li alarm başarıyla silindi.", show_alert=True)