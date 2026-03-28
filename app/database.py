import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

load_dotenv() # .env dosyasını okuması için

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "tcdd_local_sifre123")
DB_HOST = os.getenv("DB_HOST", "db")
DB_NAME = os.getenv("DB_NAME", "tcdd_bot_db")

# asyncmy kütüphanesi ile asenkron MySQL bağlantısı
DATABASE_URL = f"mysql+asyncmy://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:3306/{DB_NAME}"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def init_db():
    # Uygulama başlarken tablolar yoksa otomatik oluşturur
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)