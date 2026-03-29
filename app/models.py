from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Enum
from database import Base
import datetime
import enum


class UserStatus(str, enum.Enum):
    pending  = "pending"
    approved = "approved"
    rejected = "rejected"
    blocked  = "blocked"


class User(Base):
    __tablename__ = "users"

    id          = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String(50), unique=True, index=True, nullable=False)
    username    = Column(String(100), nullable=True)
    full_name   = Column(String(150), nullable=True)
    status      = Column(
        Enum("pending", "approved", "rejected", "blocked", name="user_status"),
        default="pending",
        nullable=False,
    )
    is_admin    = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id              = Column(Integer, primary_key=True, index=True)
    # Hangi kullanıcının alarmı (User.id FK, nullable — admin kendi alarmları için)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=True)

    kalkis_gar      = Column(String(100))
    varis_gar       = Column(String(100))
    tarih           = Column(String(20))
    baslangic_saati = Column(String(10), default="00:00")
    bitis_saati     = Column(String(10), default="23:59")
    vagon_tipi      = Column(String(50), default="Fark Etmez")
    yolcu_sayisi    = Column(Integer, default=1)

    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)