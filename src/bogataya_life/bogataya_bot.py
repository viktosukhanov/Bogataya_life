import time, re
import logging
import os
import pyjson5
import json
import datetime as dt
import pytz
from google.oauth2.service_account import Credentials
import gspread
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from bogataya_life.google_sheets import get_values_from_spreadsheet, \
    insert_values_to_spreadsheet, find_spreadsheet_id_for_user, CONFIG
from bogataya_life.keyboards_store import configure as kb_configure, build_and_cache_all_keyboards, get_user_keyboards, refresh_user_keyboards, get_type_keyboard_for_user, get_category_keyboard_for_user_and_type, build_projects_keyboard, remove_user_from_cache
from bogataya_life.google_sheets_async import init_sheets_service, insert_values_async
from bogataya_life.access_middleware import AccessMiddleware
from bogataya_life.permissions import get_admin_projects, can_admin_project, find_project
from bogataya_life.config_helpers import save_config

# ========================== Переменные ================================

# === Загружаем конфиг ===
# CONFIG уже загружен в google_sheets.py, используем его
BOT_TOKEN = CONFIG["bot_token"]
CURR = "₽"

# Определяем недостающие константы
DATE_FMT = "%d.%m.%Y"
DATE_FMT_FULL = "%d.%m.%Y"
DATE_FMT_SHORT = "%d.%m.%y"
MONTH_FMT = "%d.%m.%Y"
TZ = pytz.timezone('Europe/Moscow')  # или ваш часовой пояс

kb_configure(
    CONFIG=CONFIG,
    get_values_from_spreadsheet=get_values_from_spreadsheet,
    find_spreadsheet_id_for_user=find_spreadsheet_id_for_user,
)

# ========================== Логирование ==============================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# ========================== Функции ================================

def loadConfig(fileName):
    if os.path.exists(fileName) and os.path.isfile(fileName):
        with open(fileName, 'r') as fp:
            config = pyjson5.load(fp)
    else:
        config = pyjson5.loads("{}")
    return config

# Клавиатура для шага "Комментарий"
comment_skip_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📝 Без комментария", callback_data="no_comment")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
    ]
)

# ========================== FSM состояния ==========================

class AddUserStates(StatesGroup):
    waiting_project = State()
    waiting_user_id = State()

class SpendStates(StatesGroup):
    waiting_amount = State()
    waiting_type = State()
    waiting_date = State()
    waiting_owner = State()
    waiting_account = State()
    waiting_category = State()
    waiting_comment = State()
    waiting_owner_from = State()
    waiting_account_from = State()
    waiting_owner_to = State()
    waiting_account_to = State()

def is_admin(user_id: int) -> bool:
    return int(user_id) in set(CONFIG.get("admins", []))

def is_numeric(text):
    pattern = r'^[-+]?[0-9]+([.,][0-9]+)?$'
    return bool(re.match(pattern, text))


def find_project_for_user(user_id: int):
    for p in CONFIG["projects"]:
        if user_id in p["allowed_user_ids"]:
            return p
    raise PermissionError(f"User {user_id} не найден в списке allowed_user_ids")


def open_sheets(project):
    creds = Credentials.from_service_account_file(
        project["service_account_file"],
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(project["spreadsheet_id"])
    ws_fact = sh.worksheet(project["fact_sheet"])
    ws_ref = sh.worksheet(project["ref_sheet"])
    return ws_fact, ws_ref



def fmt_date(d: dt.date): return d.strftime(DATE_FMT)


def month_anchor(d: dt.date): return d.replace(day=1).strftime(MONTH_FMT)


def now_local(): return dt.datetime.now(TZ).date()


def _iter_choice_buttons(kb):
    """Итерируемся только по «нормальным» кнопкам, игнорируя 'Назад'."""
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data == "back":
                continue
            yield btn


def _count_buttons(kb) -> int:
    """Считает только реальные варианты (без кнопки 'Назад')."""
    return sum(1 for _ in _iter_choice_buttons(kb))


def _first_button_text(kb) -> str:
    """Возвращает текст первой реальной кнопки (без 'Назад')."""
    for btn in _iter_choice_buttons(kb):
        return btn.text
    return ""


def build_summary_text(data: dict) -> str:
    import datetime as dt
    date_str = data.get("date") or dt.datetime.now().strftime("%d.%m.%Y")
    lines = [f"Дата {date_str}"]

    exp_type = data.get("expense_type")

    # всегда показываем сумму, если есть
    if data.get("amount") not in (None, "", 0): lines.append(f"Сумма - {data['amount']}")

    if exp_type == "Перевод":
        if data.get("owner_from"):   lines.append(f"Откуда (владелец) - {data['owner_from']}")
        if data.get("account_from"): lines.append(f"Откуда (счёт) - {data['account_from']}")
        if data.get("owner_to"):     lines.append(f"Куда (владелец) - {data['owner_to']}")
        if data.get("account_to"):   lines.append(f"Куда (счёт) - {data['account_to']}")
        if data.get("comment"):      lines.append(f"Комментарий - {data['comment']}")
    else:
        if data.get("owner"):     lines.append(f"Владелец - {data['owner']}")
        if data.get("account"):   lines.append(f"Счёт - {data['account']}")
        if data.get("category"):  lines.append(f"Категория - {data['category']}")
        if data.get("comment"):   lines.append(f"Комментарий - {data['comment']}")

    return "\n".join(lines)


def build_row_for_sheet(data: dict) -> list:
    """
    Собирает строку для записи в Google Sheets.

    Формат (по колонкам):
    A: первый день месяца
    B: дата операции
    C: тип (Траты / Поступления)
    D: владелец
    E: счёт
    F: категория / подкатегория
    G: сумма
    H: комментарий
    """

    # Дата операции
    date_str = data.get("date")
    if not date_str:
        date_str = dt.datetime.now().strftime("%d.%m.%Y")

    # Первый день месяца
    first_day_month = (
        dt.datetime.strptime(date_str, "%d.%m.%Y")
        .replace(day=1)
        .strftime("%d.%m.%Y")
    )

    # Тип операции (Траты / Поступления)
    expense_type = data.get("expense_type") or data.get("type")
    if not expense_type:
        # запасной вариант — дефолт из конфига, если есть
        try:
            expense_type = DEFAULT_EXPENSE_TYPE
        except NameError:
            expense_type = "Траты"

    row = [
        first_day_month,              # A
        date_str,                     # B
        expense_type,                 # C
        data.get("owner", ""),        # D
        data.get("account", ""),      # E
        data.get("category", ""),     # F
        data.get("amount", ""),       # G
        data.get("comment", ""),      # H
    ]
    return row


#Запись «Перевода» в таблицу (две строки)
async def write_transfer_rows(data: dict, user_id: int):
    """
    Пишет две строки: расход с источника и доход на получателя.
    Категория = 'Перевод', комментарий содержит направление.
    """
    sid = find_spreadsheet_id_for_user(user_id)
    rng = "РеестрФакт"

    # базовые поля
    date_str = data.get("date")
    comment = data.get("comment", "")
    amount = data.get("amount", "")

    # строка расхода (откуда)
    d1 = dict(data)  # копия
    d1["expense_type"] = "Траты"
    d1["owner"]   = data.get("owner_from", "")
    d1["account"] = data.get("account_from", "")
    d1["category"]= "Перевод"
    d1["comment"] = (comment + f" -> {data.get('account_to','')}").strip()

    # строка дохода (куда)
    d2 = dict(data)
    d2["expense_type"] = "Поступления"
    d2["owner"]   = data.get("owner_to", "")
    d2["account"] = data.get("account_to", "")
    d2["category"]= "Перевод"
    d2["comment"] = (comment + f" <- {data.get('account_from','')}").strip()

    row1 = build_row_for_sheet(d1)
    row2 = build_row_for_sheet(d2)

    await insert_values_async(sid, rng, [row1, row2])


async def _resume_after_date_change(prev_state: str | None, user_id: int, state: FSMContext, message):
    """
    Вернуть пользователя на прежний шаг после смены даты
    и показать нужную клавиатуру (поддерживает режим 'Перевод').
    """
    data = await state.get_data()
    summary = build_summary_text(data)

    async def _reply(text: str, kb=None):
        # cb.message.edit_text(...) или msg.answer(...) — абстрагируемся
        if hasattr(message, "edit_text"):
            await message.edit_text(text, reply_markup=kb)
        else:
            await message.answer(text, reply_markup=kb)

    # если неизвестно откуда пришли — начнем заново
    if not prev_state:
        await state.set_state(SpendStates.waiting_amount)
        await _reply(summary + "\n\nВведите сумму:")
        return

    # восстановим прежнее состояние
    await state.set_state(prev_state)

    # --- перевод (from/to) ---
    if prev_state == SpendStates.waiting_owner_from.state:
        _, _, owners_kb = get_user_keyboards(user_id)
        await _reply(summary + "\n\nВыберите владельца (откуда):", owners_kb)
        return

    if prev_state == SpendStates.waiting_account_from.state:
        _, acc_kb, _ = get_user_keyboards(user_id)
        await _reply(summary + "\n\nВыберите счёт (откуда):", acc_kb)
        return

    if prev_state == SpendStates.waiting_owner_to.state:
        _, _, owners_kb = get_user_keyboards(user_id)
        await _reply(summary + "\n\nВыберите владельца (куда):", owners_kb)
        return

    if prev_state == SpendStates.waiting_account_to.state:
        _, acc_kb, _ = get_user_keyboards(user_id)
        await _reply(summary + "\n\nВыберите счёт (куда):", acc_kb)
        return

    # --- обычный сценарий ---
    if prev_state == SpendStates.waiting_type.state:
        type_kb = get_type_keyboard_for_user(user_id)
        await _reply(summary + "\n\nВыберите тип:", type_kb)
        return

    if prev_state == SpendStates.waiting_owner.state:
        _, _, owners_kb = get_user_keyboards(user_id)
        await _reply(summary + "\n\nВыберите владельца:", owners_kb)
        return

    if prev_state == SpendStates.waiting_account.state:
        _, acc_kb, _ = get_user_keyboards(user_id)
        await _reply(summary + "\n\nВыберите счёт:", acc_kb)
        return

    if prev_state == SpendStates.waiting_category.state:
        exp_type = data.get("expense_type")
        cat_kb = get_category_keyboard_for_user_and_type(user_id, exp_type) if exp_type else None
        if cat_kb:
            await _reply(summary + "\n\nВыберите категорию:", cat_kb)
        else:
            type_kb = get_type_keyboard_for_user(user_id)
            await state.set_state(SpendStates.waiting_type)
            await _reply(summary + "\n\nВыберите тип:", type_kb)
        return

    if prev_state == SpendStates.waiting_comment.state:
        await _reply(
            summary + "\n\nПришлите комментарий или нажмите «Без комментария».",
            comment_skip_keyboard
        )
        return

    # запасной вариант
    await state.set_state(SpendStates.waiting_amount)
    await _reply(summary + "\n\nВведите сумму:")


#
#
# ==========================  Инициализация бота ==========================
#
#
bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())


# ИСПРАВЛЕНО: убрали дублирование и объединили startup функции
async def on_startup(bot: Bot):
    """Инициализация при старте бота"""
    # 1) Инициализация Google Sheets сервиса
    init_sheets_service("creds_service_acc_google.json")
    logging.info("Google Sheets сервис инициализирован")

    # 2) Построение кэша клавиатур для всех пользователей
    await build_and_cache_all_keyboards()
    logging.info("Клавиатуры собраны при запуске")


dp.startup.register(on_startup)


#
#
# ========================== Старт ==========================
#
#


@dp.startup.register
async def on_startup():
    init_sheets_service("creds_service_acc_google.json")  # путь к твоему JSON


@dp.message(Command("start"))
async def cmd_start(msg: Message):
    await msg.answer("Привет! Отправь число чтобы внести трату.")


#============= ADD USER ===============
@dp.message(Command("add_user"))
async def cmd_add_user(msg: Message, state: FSMContext):
    admin_projects = get_admin_projects(msg.from_user.id, CONFIG)
    if not admin_projects:
        return await msg.answer("⛔ У вас нет прав управлять пользователями в каких-либо проектах.")

    await state.clear()

    # если проект у админа один — пропускаем выбор проекта
    if len(admin_projects) == 1:
        await state.update_data(project_name=admin_projects[0])
        await state.set_state(AddUserStates.waiting_user_id)
        return await msg.answer(
            f"Проект: {admin_projects[0]}\nПришлите user_id (числом) или перешлите сообщение от пользователя."
        )

    kb = build_projects_keyboard(CONFIG, msg.from_user.id)
    await state.set_state(AddUserStates.waiting_project)
    await msg.answer("Выберите проект, в который добавить пользователя:", reply_markup=kb)


@dp.callback_query(AddUserStates.waiting_project, F.data.startswith("admproj_"))
async def pick_project(cb: CallbackQuery, state: FSMContext):
    project_name = cb.data.split("admproj_", 1)[1]
    if not can_admin_project(cb.from_user.id, project_name, CONFIG):
        return await cb.answer("⛔ Нет прав для этого проекта", show_alert=True)

    await state.update_data(project_name=project_name)
    await state.set_state(AddUserStates.waiting_user_id)
    await cb.message.edit_text(
        f"Проект: {project_name}\nПришлите user_id (числом) или перешлите сообщение от пользователя."
    )


@dp.message(AddUserStates.waiting_user_id)
async def add_user_receive_id(msg: Message, state: FSMContext):
    data = await state.get_data()
    project_name = data.get("project_name")
    if not project_name:
        await state.clear()
        return await msg.answer("Проект не выбран. Начните заново: /add_user")

    # определить user_id
    new_user_id = msg.forward_from.id if msg.forward_from else (int(msg.text.strip()) if msg.text and msg.text.strip().isdigit() else None)
    if not new_user_id:
        return await msg.answer("Не удалось определить user_id. Пришлите цифрами или перешлите сообщение от пользователя.")

    if not can_admin_project(msg.from_user.id, project_name, CONFIG):
        await state.clear()
        return await msg.answer("⛔ У вас нет прав на этот проект.")

    project = find_project(CONFIG, project_name)
    if not project:
        await state.clear()
        return await msg.answer(f"Проект '{project_name}' не найден.")

    allowed = project.setdefault("allowed_user_ids", [])
    if int(new_user_id) in map(int, allowed):
        await state.clear()
        return await msg.answer(f"Пользователь {new_user_id} уже есть в проекте '{project_name}'.")

    allowed.append(int(new_user_id))
    try:
        save_config(CONFIG)
    except Exception as e:
        await state.clear()
        return await msg.answer(f"❌ Не удалось сохранить конфиг: {e}")

    # построить клавиатуры для нового пользователя
    try:
        await refresh_user_keyboards(new_user_id)
    except Exception as e:
        await msg.answer(f"⚠️ Пользователь добавлен, но кэш клавиатур не обновлён: {e}")

    await state.clear()
    await refresh_user_keyboards(new_user_id)
    await msg.answer(f"✅ Пользователь {new_user_id} добавлен в проект '{project_name}'.")


# Удаление пользователя
@dp.message(Command("remove_user"))
async def cmd_remove_user(msg: Message, state: FSMContext):
    parts = msg.text.split()
    if len(parts) != 3 or not parts[2].isdigit():
        return await msg.answer("Использование: /remove_user <project_name> <user_id>")

    project_name, uid_str = parts[1], parts[2]
    user_id = int(uid_str)

    if not can_admin_project(msg.from_user.id, project_name, CONFIG):
        return await msg.answer("⛔ Нет прав на этот проект.")

    project = find_project(CONFIG, project_name)
    if not project:
        return await msg.answer(f"Проект '{project_name}' не найден.")

    allowed = project.setdefault("allowed_user_ids", [])
    if user_id not in map(int, allowed):
        return await msg.answer(f"user_id {user_id} не найден в проекте '{project_name}'.")

    allowed[:] = [int(x) for x in allowed if int(x) != user_id]
    from config_helpers import save_config
    save_config(CONFIG)
    remove_user_from_cache(user_id)
    await msg.answer(f"✅ Пользователь {user_id} удалён из проекта '{project_name}'. Доступ закрыт.")


@dp.message(Command("list_users"))
async def cmd_list_users(msg: Message):
    parts = msg.text.split(maxsplit=1)
    if len(parts) != 2:
        return await msg.answer("Использование: /list_users <project_name>")
    project_name = parts[1]
    if not can_admin_project(msg.from_user.id, project_name, CONFIG):
        return await msg.answer("⛔ Нет прав на этот проект.")
    project = find_project(CONFIG, project_name)
    if not project:
        return await msg.answer(f"Проект '{project_name}' не найден.")
    users = project.get("allowed_user_ids", [])
    await msg.answer(f"Пользователи в '{project_name}':\n" + "\n".join(map(str, users)) if users else "Пока пусто.")

#=======================================


# Команда /update_keyboards
@dp.message(Command("update_keyboards"))
async def cmd_update_keyboards(msg: Message):
    await msg.answer("⏳ Обновляю ваши клавиатуры…")
    try:
        await refresh_user_keyboards(msg.from_user.id)
        await msg.answer("✅ Готово! Ваши клавиатуры обновлены.")
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")


@dp.message(Command("reset"))
async def cmd_reset(msg: Message, state: FSMContext):
    """Сброс состояния FSM для пользователя."""
    await state.clear()
    await msg.answer("🔄 Текущее состояние сброшено. Можно начать заново.")


@dp.callback_query(F.data == "back")
async def handle_back_button(cb: CallbackQuery, state: FSMContext):
    cur_state = await state.get_state()
    data = await state.get_data()
    summary = build_summary_text(data)

    if cur_state == SpendStates.waiting_comment:
        # Назад к категории
        exp_type = data.get("expense_type")
        cat_kb = get_category_keyboard_for_user_and_type(cb.from_user.id, exp_type) if exp_type else None
        if cat_kb and _count_buttons(cat_kb) > 0:
            await cb.message.edit_text(summary + "\n\nВыберите категорию:", reply_markup=cat_kb)
            await state.set_state(SpendStates.waiting_category)
        else:
            # если категорий нет — к счёту
            _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
            await cb.message.edit_text(summary + "\n\nВыберите счёт:", reply_markup=acc_kb)
            await state.set_state(SpendStates.waiting_account)

    elif cur_state == SpendStates.waiting_category:
        # ✅ Назад к СЧЁТУ (исправление)
        _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
        await cb.message.edit_text(summary + "\n\nВыберите счёт:", reply_markup=acc_kb)
        await state.set_state(SpendStates.waiting_account)

    elif cur_state == SpendStates.waiting_account:
        # Назад к владельцу
        _, _, owners_kb = get_user_keyboards(cb.from_user.id)
        await cb.message.edit_text(summary + "\n\nВыберите владельца:", reply_markup=owners_kb)
        await state.set_state(SpendStates.waiting_owner)

    elif cur_state == SpendStates.waiting_owner:
        # Назад к типу
        type_kb = get_type_keyboard_for_user(cb.from_user.id)
        await cb.message.edit_text(summary + "\n\nВыберите тип:", reply_markup=type_kb)
        await state.set_state(SpendStates.waiting_type)

    elif cur_state == SpendStates.waiting_type:
        # Назад к сумме
        await cb.message.edit_text("Ввод отменен.\n\nВведите сумму снова:")
        await state.set_state(SpendStates.waiting_amount)

    elif cur_state == SpendStates.waiting_account_to:
        _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
        await cb.message.edit_text(summary + "\n\nВыберите владельца (куда):", reply_markup=acc_kb)
        await state.set_state(SpendStates.waiting_owner_to)

    elif cur_state == SpendStates.waiting_owner_to:
        _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
        await cb.message.edit_text(summary + "\n\nВыберите счёт (откуда):", reply_markup=acc_kb)
        await state.set_state(SpendStates.waiting_account_from)

    elif cur_state == SpendStates.waiting_account_from:
        _, _, owners_kb = get_user_keyboards(cb.from_user.id)
        await cb.message.edit_text(summary + "\n\nВыберите владельца (откуда):", reply_markup=owners_kb)
        await state.set_state(SpendStates.waiting_owner_from)

    else:
        await cb.message.edit_text("Возврат невозможен, начните заново /spent")
        await state.clear()


@dp.message(Command("change_date"))
async def cmd_change_date(msg: Message, state: FSMContext):
    prev_state = await state.get_state()
    await state.update_data(_prev_state=prev_state)

    kb = build_change_date_keyboard(days_back=10)
    summary = build_summary_text(await state.get_data())
    await msg.answer(
        summary + "\n\nВыберите дату из списка или нажмите «Ввести дату вручную»:",
        reply_markup=kb
    )
    await state.set_state(SpendStates.waiting_date)


@dp.callback_query(SpendStates.waiting_date, F.data.startswith("cd_"))
async def handle_date_pick(cb: CallbackQuery, state: FSMContext):
    if cb.data == "cd_manual":
        await cb.message.edit_text("Введите дату вручную в формате ДД.ММ.ГГГГ:")
        return

    # cd_<ДД.ММ.ГГГГ>
    full_date = cb.data.split("cd_", 1)[1]
    await state.update_data(date=full_date)

    data = await state.get_data()
    prev_state = data.get("_prev_state")
    await state.update_data(_prev_state=None)

    # вернёмся на прежний шаг и покажем нужную клавиатуру
    await _resume_after_date_change(prev_state, cb.from_user.id, state, cb.message)


@dp.message(SpendStates.waiting_date, F.text)
async def cd_manual_input(msg: Message, state: FSMContext):
    s = msg.text.strip()
    try:
        # принимаем и ДД.ММ.ГГГГ, и ДД.ММ.ГГ
        from datetime import datetime as _dt
        full = _dt.strptime(s, "%d.%m.%Y") if len(s) == 10 else _dt.strptime(s, "%d.%m.%y")
        full_date = full.strftime("%d.%m.%Y")
    except ValueError:
        await msg.answer("❌ Неверный формат. Введите дату в виде ДД.ММ.ГГГГ")
        return

    await state.update_data(date=full_date)

    data = await state.get_data()
    prev_state = data.get("_prev_state")
    await state.update_data(_prev_state=None)

    # возвращаем и показываем клавиатуру соответствующего шага
    await _resume_after_date_change(prev_state, msg.from_user.id, state, msg)



def build_change_date_keyboard(days_back: int = 10) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора даты:
    [Ввести дату вручную]
    [сегодня] [вчера] [позавчера]
    ...
    [последний день предыдущего месяца]
    """
    today = dt.date.today()

    rows: list[list[InlineKeyboardButton]] = []
    # 1) первая строка
    rows.append([InlineKeyboardButton(text="Ввести дату вручную", callback_data="cd_manual")])

    # 2) последние N дней по 3 в ряд
    labels = []
    for i in range(days_back):
        d = today - dt.timedelta(days=i)
        labels.append((
            d.strftime(DATE_FMT_SHORT),  # текст
            d.strftime(DATE_FMT_FULL)    # дата в callback
        ))

    for i in range(0, len(labels), 3):
        chunk = labels[i:i+3]
        row = [InlineKeyboardButton(text=t, callback_data=f"cd_{full}") for t, full in chunk]
        rows.append(row)

    # 3) последний день прошлого месяца — одна кнопка в строке
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - dt.timedelta(days=1)
    last_short = last_day_prev_month.strftime(DATE_FMT_SHORT)
    last_full  = last_day_prev_month.strftime(DATE_FMT_FULL)

    # если уже попал в список последних дней — всё равно вынесем отдельной строкой
    rows.append([InlineKeyboardButton(text=last_short, callback_data=f"cd_{last_full}")])

    return InlineKeyboardMarkup(inline_keyboard=rows)



#=========== Хендлеры пути «Перевод» ===================

#Выбор владельца «откуда»
@dp.callback_query(SpendStates.waiting_owner_from, F.data.startswith("owner_"))
async def transfer_owner_from(cb: CallbackQuery, state: FSMContext):
    owner_from = cb.data.split("owner_", 1)[1]
    await state.update_data(owner_from=owner_from)

    # выбор счёта-источника
    _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
    # авто-пропуск если один счёт
    if _count_buttons(acc_kb) == 1:
        account_from = _first_button_text(acc_kb)
        await state.update_data(account_from=account_from)
        # далее — выбор владельца-получателя
        _, _, owners_kb = get_user_keyboards(cb.from_user.id)
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(summary + "\n\nВыберите владельца (куда):", reply_markup=owners_kb)
        await state.set_state(SpendStates.waiting_owner_to)
        return

    summary = build_summary_text(await state.get_data())
    await cb.message.edit_text(summary + "\n\nВыберите счёт (откуда):", reply_markup=acc_kb)
    await state.set_state(SpendStates.waiting_account_from)


#Выбор счёта «откуда»
@dp.callback_query(SpendStates.waiting_account_from, F.data.startswith("account_"))
async def transfer_account_from(cb: CallbackQuery, state: FSMContext):
    account_from = cb.data.split("account_", 1)[1]
    await state.update_data(account_from=account_from)

    # выбор владельца-получателя
    _, _, owners_kb = get_user_keyboards(cb.from_user.id)
    summary = build_summary_text(await state.get_data())
    await cb.message.edit_text(summary + "\n\nВыберите владельца (куда):", reply_markup=owners_kb)
    await state.set_state(SpendStates.waiting_owner_to)


#Выбор владельца «куда»
@dp.callback_query(SpendStates.waiting_owner_to, F.data.startswith("owner_"))
async def transfer_owner_to(cb: CallbackQuery, state: FSMContext):
    owner_to = cb.data.split("owner_", 1)[1]
    await state.update_data(owner_to=owner_to)

    # выбор счёта-получателя
    _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
    if _count_buttons(acc_kb) == 1:
        account_to = _first_button_text(acc_kb)
        await state.update_data(account_to=account_to)

        # сразу к комментарию
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(
            summary + "\n\nПришлите комментарий или нажмите «Без комментария».",
            reply_markup=comment_skip_keyboard
        )
        await state.set_state(SpendStates.waiting_comment)
        return

    summary = build_summary_text(await state.get_data())
    await cb.message.edit_text(summary + "\n\nВыберите счёт (куда):", reply_markup=acc_kb)
    await state.set_state(SpendStates.waiting_account_to)


#Выбор счёта «куда»
@dp.callback_query(SpendStates.waiting_account_to, F.data.startswith("account_"))
async def transfer_account_to(cb: CallbackQuery, state: FSMContext):
    account_to = cb.data.split("account_", 1)[1]
    await state.update_data(account_to=account_to)

    # сразу к комментарию
    summary = build_summary_text(await state.get_data())
    await cb.message.edit_text(
        summary + "\n\nПришлите комментарий или нажмите «Без комментария».",
        reply_markup=comment_skip_keyboard
    )
    await state.set_state(SpendStates.waiting_comment)


#============== Основная логика ===================


# Обработчик ввода числа
@dp.message(F.text.func(lambda t: is_numeric(t)), ~StateFilter(SpendStates.waiting_comment))
async def handle_amount(msg: Message, state: FSMContext):
    # парсим сумму
    text = msg.text.strip().replace(",", ".")
    amount = float(text)

    # сохраняем сумму и дату (фиксируем один раз)
    await state.update_data(amount=amount)
    data = await state.get_data()
    if "date" not in data or not data.get("date"):
        await state.update_data(date=dt.datetime.now().strftime("%d.%m.%Y"))

    # удаляем сообщение с суммой (на всякий случай — тихо)
    try:
        await msg.delete()
    except Exception:
        pass

    # показываем выбор Типа (Траты/Поступления)
    type_kb = get_type_keyboard_for_user(msg.from_user.id)  # row_width внутри функции
    summary = build_summary_text(await state.get_data())
    await msg.answer(summary + "\n\nВыберите тип:", reply_markup=type_kb)

    await state.set_state(SpendStates.waiting_type)


#=========== TYPE     ==========
@dp.callback_query(SpendStates.waiting_type, F.data.startswith("type_"))
async def choose_type(cb: CallbackQuery, state: FSMContext):
    exp_type = cb.data.split("type_", 1)[1]
    await state.update_data(expense_type=exp_type)

    if exp_type == "Перевод":
        # начинаем путь перевода: выбираем ОТКУДА (владелец_from)
        _, _, owners_kb = get_user_keyboards(cb.from_user.id)
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(summary + "\n\nВыберите владельца (откуда):", reply_markup=owners_kb)
        await state.set_state(SpendStates.waiting_owner_from)
        return

    # шаг владельца
    _, _, owners_kb = get_user_keyboards(cb.from_user.id)

    # вспомогательные функции должны игнорировать кнопку "Назад"
    owners_cnt = _count_buttons(owners_kb)
    if owners_cnt == 1:
        owner = _first_button_text(owners_kb)
        await state.update_data(owner=owner)

        # шаг счета
        _, acc_kb, _ = get_user_keyboards(cb.from_user.id)
        accounts_cnt = _count_buttons(acc_kb)

        if accounts_cnt == 1:
            account = _first_button_text(acc_kb)
            await state.update_data(account=account)

            # шаг категории (фильтр по выбранному типу)
            cat_kb = get_category_keyboard_for_user_and_type(cb.from_user.id, exp_type)
            cats_cnt = _count_buttons(cat_kb)

            if cats_cnt == 0:
                summary = build_summary_text(await state.get_data())
                await cb.message.edit_text(summary + f"\n\nДля типа «{exp_type}» пока нет категорий.")
                await state.set_state(SpendStates.waiting_type)
                return

            if cats_cnt == 1:
                category = _first_button_text(cat_kb)
                await state.update_data(category=category)
                summary = build_summary_text(await state.get_data())
                await cb.message.edit_text(
                    summary + "\n\nПришлите комментарий или нажмите «Без комментария».",
                    reply_markup=comment_skip_keyboard
                )
                await state.set_state(SpendStates.waiting_comment)
                return

            # несколько категорий
            summary = build_summary_text(await state.get_data())
            await cb.message.edit_text(
                summary + "\n\nВыберите категорию:",
                reply_markup=cat_kb
            )
            await state.set_state(SpendStates.waiting_category)
            return

        # несколько счетов
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(
            summary + "\n\nВыберите счёт:",
            reply_markup=acc_kb
        )
        await state.set_state(SpendStates.waiting_account)
        return

    # несколько владельцев
    summary = build_summary_text(await state.get_data())
    await cb.message.edit_text(
        summary + "\n\nВыберите владельца:",
        reply_markup=owners_kb
    )
    await state.set_state(SpendStates.waiting_owner)


# ========== OWNER ==========
@dp.callback_query(SpendStates.waiting_owner, F.data.startswith("owner_"))
async def choose_owner(cb: CallbackQuery, state: FSMContext):
    owner = cb.data.split("owner_", 1)[1]
    await state.update_data(owner=owner)

    cat_kb, acc_kb, _ = get_user_keyboards(cb.from_user.id)
    accounts_cnt = _count_buttons(acc_kb)

    # 1 счёт → сразу к категории
    if accounts_cnt == 1:
        account = _first_button_text(acc_kb)
        await state.update_data(account=account)
        data = await state.get_data()
        summary = build_summary_text(data)

        await cb.message.edit_text(
            summary + "\n\nВыберите категорию:",
            reply_markup=cat_kb
        )
        await state.set_state(SpendStates.waiting_category)
        return

    # несколько счетов → выбор счета
    data = await state.get_data()
    summary = build_summary_text(data)
    await cb.message.edit_text(
        summary + "\n\nВыберите счёт:",
        reply_markup=acc_kb
    )
    await state.set_state(SpendStates.waiting_account)


# ========== ACCOUNT ==========
@dp.callback_query(SpendStates.waiting_account, F.data.startswith("account_"))
async def choose_account(cb: CallbackQuery, state: FSMContext):
    account = cb.data.split("account_", 1)[1]
    await state.update_data(account=account)

    data = await state.get_data()
    exp_type = data.get("expense_type")

    # Если почему-то тип ещё не выбран (например, пришли "сбоку") — подстрахуемся и спросим тип.
    if not exp_type:
        type_kb = get_type_keyboard_for_user(cb.from_user.id)
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(summary + "\n\nВыберите тип:", reply_markup=type_kb)
        await state.set_state(SpendStates.waiting_type)
        return

    # Категории по выбранному типу
    cat_kb = get_category_keyboard_for_user_and_type(cb.from_user.id, exp_type)
    cats_cnt = _count_buttons(cat_kb)  # важно: эта функция игнорирует кнопку "Назад"

    if cats_cnt == 0:
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(
            summary + f"\n\nДля типа «{exp_type}» пока нет категорий."
        )
        # остаёмся на выборе типа или вернём на шаг назад — на твой вкус.
        await state.set_state(SpendStates.waiting_type)
        return

    if cats_cnt == 1:
        category = _first_button_text(cat_kb)  # тоже игнорирует "Назад"
        await state.update_data(category=category)
        summary = build_summary_text(await state.get_data())
        await cb.message.edit_text(
            summary + "\n\nПришлите комментарий или нажмите «Без комментария».",
            reply_markup=comment_skip_keyboard
        )
        await state.set_state(SpendStates.waiting_comment)
        return

    # Несколько категорий — показываем выбор
    summary = build_summary_text(await state.get_data())
    await cb.message.edit_text(
        summary + "\n\nВыберите категорию:",
        reply_markup=cat_kb
    )
    await state.set_state(SpendStates.waiting_category)




# ========== CATEGORY ==========
@dp.callback_query(SpendStates.waiting_category, F.data.startswith("category_"))
async def choose_category(cb: CallbackQuery, state: FSMContext):
    category = cb.data.split("category_", 1)[1]
    await state.update_data(category=category)

    data = await state.get_data()
    summary = build_summary_text(data)

    await cb.message.edit_text(
        summary + "\n\nПришлите комментарий или нажмите «Без комментария».",
        reply_markup=comment_skip_keyboard
    )
    await state.set_state(SpendStates.waiting_comment)


# ========== NO COMMENT ==========
@dp.callback_query(F.data == "no_comment", SpendStates.waiting_comment)
async def skip_comment(cb: CallbackQuery, state: FSMContext):
    await state.update_data(comment="")
    data = await state.get_data()
    if "date" not in data or not data.get("date"):
        await state.update_data(date=dt.datetime.now().strftime("%d.%m.%Y"))
        data = await state.get_data()

    try:
        if data.get("expense_type") == "Перевод":
            await write_transfer_rows(data, cb.from_user.id)
        else:
            row = build_row_for_sheet(data)
            sid = find_spreadsheet_id_for_user(cb.from_user.id)
            rng = "РеестрФакт"
            await insert_values_async(sid, rng, row)

        summary = build_summary_text(data)
        await cb.message.edit_text(summary + "\n\n✅ Записано.")
    except Exception as e:
        await cb.message.edit_text(f"❌ Ошибка записи: {e}")
    finally:
        await state.clear()



# ========== COMMENT ==========
@dp.message(SpendStates.waiting_comment, F.text)
async def handle_comment_text(msg: Message, state: FSMContext):
    await state.update_data(comment=msg.text.strip())
    data = await state.get_data()
    if "date" not in data or not data.get("date"):
        await state.update_data(date=dt.datetime.now().strftime("%d.%m.%Y"))
        data = await state.get_data()

    try:
        if data.get("expense_type") == "Перевод":
            await write_transfer_rows(data, msg.from_user.id)
        else:
            row = build_row_for_sheet(data)
            sid = find_spreadsheet_id_for_user(msg.from_user.id)
            rng = "РеестрФакт"
            await insert_values_async(sid, rng, row)

        summary = build_summary_text(data)
        await msg.answer(summary + "\n\n✅ Записано.")
    except Exception as e:
        await msg.answer(f"❌ Ошибка записи: {e}")
    finally:
        await state.clear()




# Запуск бота
def main() -> None:
    import asyncio
    asyncio.run(dp.start_polling(bot))

if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))
