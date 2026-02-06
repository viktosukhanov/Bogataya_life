from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

# Импортируй свои функции из permissions/utils
from bogataya_life.permissions import can_admin_project, is_global_admin
from bogataya_life.google_sheets import CONFIG  # глобально загруженный конфиг
from bogataya_life.keyboards_store import is_user_allowed  # напишем ниже


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        else:
            return await handler(event, data)

        # Разрешаем глобальным админам всё (они управляют доступами)
        if is_global_admin(user_id, CONFIG):
            return await handler(event, data)

        # Разрешаем проект-админам пользоваться админ-командами (add/remove/list), остальное — по allowed
        text = ""
        if isinstance(event, Message) and event.text:
            text = event.text.strip()

        is_admin_cmd = text.startswith("/add_user") or text.startswith("/remove_user") or text.startswith("/list_users")

        if is_admin_cmd:
            # Проект-админам позволяем команды управления, даже если их нет в allowed_user_ids
            return await handler(event, data)

        # Для всех остальных действий — проверяем is_user_allowed
        if not is_user_allowed(user_id):
            msg = "⛔ Доступ к боту закрыт. Обратитесь к администратору проекта."
            if isinstance(event, Message):
                await event.answer(msg)
            elif isinstance(event, CallbackQuery):
                await event.message.answer(msg)
            return  # блокируем обработку

        return await handler(event, data)
