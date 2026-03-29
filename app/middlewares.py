import time
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 1.5):
        self.limit = limit
        self.users = {}  # user_id -> son istek zamanı (timestamp)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id:
            now = time.time()
            last_time = self.users.get(user_id, 0.0)
            
            # Eğer son istekten bu yana geçen süre limitten küçükse isteği yoksay
            if now - last_time < self.limit:
                if isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Çok hızlı işlem yapıyorsunuz, lütfen bekleyin!", show_alert=False)
                return # İsteği düşür (handler'a gitmesini engelle)
            
            # Zamanı güncelle
            self.users[user_id] = now

        return await handler(event, data)