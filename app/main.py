import asyncio
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from dotenv import load_dotenv

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from sqlalchemy import select

from database import init_db, AsyncSessionLocal
from handlers import router
from scraper import check_train_tickets
from models import Task

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TELEGRAM_ID = str(os.getenv("ADMIN_TELEGRAM_ID"))

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
dp.include_router(router)

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = str(message.from_user.id)
    if user_id == ADMIN_TELEGRAM_ID:
        await message.answer("Hoş geldin Patron! 🚂\nAlarmları görmek için /alarmlar\nYeni alarm için /yeni_alarm")

async def check_all_active_alarms():
    logging.info(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 Bilet kontrolü başlatıldı...")
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Task).where(Task.is_active == True))
        active_tasks = result.scalars().all()
        
    if not active_tasks: return

    for task in active_tasks:
        logging.info(f"Aranıyor: {task.kalkis_gar}-{task.varis_gar} | {task.baslangic_saati}-{task.bitis_saati}")
        
        # YENİ EKLENEN PARAMETRELERİ GÖNDERİYORUZ
        biletler = await check_train_tickets(
            kalkis=task.kalkis_gar, 
            varis=task.varis_gar, 
            tarih=task.tarih,
            baslangic_saati=task.baslangic_saati,
            bitis_saati=task.bitis_saati,
            yolcu_sayisi=task.yolcu_sayisi,
            vagon_tipi=task.vagon_tipi
        )
        
        if biletler:
            mesaj = f"🚨 <b>BİLET BULUNDU!</b> 🚨\n\n🚂 Rota: {task.kalkis_gar} - {task.varis_gar}\n📅 Tarih: {task.tarih}\n👥 İstenen: {task.yolcu_sayisi} Kişi ({task.vagon_tipi})\n\n"
            for b in biletler:
                mesaj += f"⏰ {b['saat']} | 🚆 {b['tren_tipi']} | 💺 {b['bos_koltuk']} Boş Koltuk\n"
            
            await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=mesaj, parse_mode="HTML")
            
        await asyncio.sleep(3)

async def main():
    await init_db()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_all_active_alarms, 'interval', minutes=0.5)
    scheduler.start()
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())