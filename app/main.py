import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from dotenv import load_dotenv

# Yeni eklediğimiz kütüphaneler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime

# Kendi yazdığımız modüller
from database import init_db
from handlers import router
from scraper import check_train_tickets

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = str(os.getenv("ADMIN_TELEGRAM_ID"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Handlers dosyamızı bağladık
dp.include_router(router)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    if user_id == ADMIN_TELEGRAM_ID:
        await message.answer("Hoş geldin Patron! TCDD Bilet Botu emrinde. 🚂\nAlarmları görmek için /alarmlar yazabilirsin.")
    else:
        await message.answer("Merhaba! Bu bot özel kullanıma tabidir.")

# --- İŞTE YENİ EKLENEN ARKA PLAN İŞÇİSİ (WORKER) ---
async def check_all_active_alarms():
    """
    Bu fonksiyon her X saniyede/dakikada bir otomatik tetiklenecek.
    Normalde burada veritabanına bağlanıp "Aktif" olan alarmları çekeceğiz.
    Şimdilik sistemin çalıştığını görmek için statik bir test atıyoruz.
    """
    logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Otomatik bilet kontrolü başlatıldı...")
    
    # İleride burası: for task in active_tasks: olacak
    test_kalkis = "ANKARA GAR"
    test_varis = "ESKİŞEHİR"
    test_tarih = "30.03.2026" # Yarına bakalım
    
    # Scraper'ı çalıştır
    biletler = await check_train_tickets(test_kalkis, test_varis, test_tarih)
    
    if biletler:
        mesaj = f"🚨 <b>BİLET BULUNDU!</b> 🚨\n\nRota: {test_kalkis} - {test_varis}\n"
        for b in biletler:
            mesaj += f"⏰ {b['saat']} | 🚆 {b['tren_tipi']} | 💺 {b['bos_koltuk']} Koltuk\n"
        
        # Patron'a (Sana) anında Telegram'dan mesaj at!
        await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=mesaj, parse_mode="HTML")
        logging.info("TCDD'de bilet bulundu ve Telegram'a bildirildi!")
    else:
        logging.info("Şu an boş yer yok, aramaya devam...")

async def main():
    await init_db()
    logging.info("Veritabanı hazırlandı.")
    
    # Zamanlayıcıyı (Scheduler) kur ve başlat
    scheduler = AsyncIOScheduler()
    
    # TEST İÇİN: Şimdilik 1 dakika yerine '15 saniyede bir' çalışsın ki sonucu hemen görelim.
    # Canlıya (VDS'e) alırken burayı minutes=1 yapacağız.
    scheduler.add_job(check_all_active_alarms, 'interval', seconds=30)
    scheduler.start()
    logging.info("Zamanlayıcı (Scheduler) başlatıldı. Arka plan işçisi devrede.")
    
    logging.info("Bot çalışıyor...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())