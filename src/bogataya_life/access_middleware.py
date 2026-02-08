from aiogram import BaseMiddleware

from bogataya_life.google_sheets import CONFIG
from bogataya_life.client_access import is_user_allowed, resolve_registry_sheet_id


GLOBAL_ADMINS = set(map(int, CONFIG.get("admins", [])))


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        text = getattr(event, "text", "") or ""

        # /update_users — только глобальные админы из config.json5
        if text.strip().startswith("/update_users"):
            if user_id not in GLOBAL_ADMINS:
                await event.answer("⛔ Команда доступна только администратору.")
                return
            return await handler(event, data)

        # обычная проверка доступа
        if not is_user_allowed(user_id):
            await event.answer("⛔ У вас нет доступа к этому боту.")
            return

        data["registry_sheet_id"] = resolve_registry_sheet_id(user_id)
        return await handler(event, data)

