# keyboards_store.py
from __future__ import annotations

import os
import json
import pyjson5
import logging
import datetime as dt
from typing import Callable, Dict, Any, List

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bogataya_life.permissions import get_admin_projects
from bogataya_life.client_access import resolve_client_key
from pathlib import Path




# ===== Настройки =====
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_PATH = Path(os.getenv("KEYBOARDS_CACHE_PATH", str(PROJECT_ROOT / "keyboards_cache.json5")))


# ===== Внутренние зависимости (DI) =====
_config: Dict[str, Any] | None = None
_get_values_from_spreadsheet: Callable[[str, str], List[List[Any]]] | None = None
_find_spreadsheet_id_for_user: Callable[[int], str] | None = None


def configure(
    *,
    CONFIG: Dict[str, Any],
    get_values_from_spreadsheet: Callable[[str, str], List[List[Any]]],
    find_spreadsheet_id_for_user: Callable[[int], str],
) -> None:
    """Подключение зависимостей из главного приложения (DI)."""
    global _config, _get_values_from_spreadsheet, _find_spreadsheet_id_for_user
    _config = CONFIG
    _get_values_from_spreadsheet = get_values_from_spreadsheet
    _find_spreadsheet_id_for_user = find_spreadsheet_id_for_user

#test
# ===== Работа с файлом кэша =====
def _load_cache() -> Dict[str, Any]:
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return pyjson5.load(f)
    except Exception as e:
        logging.error(f"{dt.datetime.now()} - Ошибка загрузки {CACHE_PATH}: {e}")
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(str(CACHE_PATH), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"{dt.datetime.now()} - Ошибка сохранения {CACHE_PATH}: {e}")


def clear_keyboards_cache() -> None:
    """Полностью удаляет файл кэша (по необходимости)."""
    if os.path.exists(CACHE_PATH):
        os.remove(CACHE_PATH)
        logging.info("🗑️ Кэш клавиатур очищен.")


def remove_user_from_cache(user_id: int) -> None:
    cache = _load_cache()
    key = str(int(user_id))
    if key in cache:
        del cache[key]
        _save_cache(cache)


def is_user_allowed(uid: int) -> bool:
    # В новой схеме доступ проверяется middleware (по admin-sheet).
    # Здесь достаточно проверить, что пользователь привязан к клиенту (и мы можем получить его spreadsheet_id).
    global _find_spreadsheet_id_for_user
    if _find_spreadsheet_id_for_user is None:
        return False
    try:
        _find_spreadsheet_id_for_user(uid)
        return True
    except Exception:
        return False



# ===== Построение клавиатур =====
def _build_keyboard(
    named_range: str,
    spreadsheet_id: str,
    cb_prefix: str,
    row_width: int,
) -> list[list[dict]]:
    """
    Формирует структуру клавиатуры:
    - Основные кнопки располагаются с заданным row_width;
    - Последней строкой всегда добавляется кнопка '🔙 Назад'.
    Возвращает список списков словарей (text, callback_data).
    """
    assert _get_values_from_spreadsheet is not None, "configure() не вызван"

    try:
        print("DEBUG build kb", named_range, "sid", spreadsheet_id)

        values = _get_values_from_spreadsheet(named_range, spreadsheet_id) or []
        if not values:
            return [[{"text": "🔙 Назад", "callback_data": "back"}]]
        print("DEBUG rows", len(values))

        row = values[0] if values else []
        main_buttons: list[dict] = []
        for v in row[1:]:
            text = str(v).strip()
            if text:
                main_buttons.append({"text": text, "callback_data": f"{cb_prefix}{text}"})

        rw = max(1, int(row_width or 1))
        keyboard_data: list[list[dict]] = [main_buttons[i:i + rw] for i in range(0, len(main_buttons), rw)]
        keyboard_data.append([{"text": "🔙 Назад", "callback_data": "back"}])
        return keyboard_data

    except Exception as e:
        logging.error(f"{dt.datetime.now()} - Ошибка при создании клавиатуры {named_range}: {e}")
        return [[{"text": "🔙 Назад", "callback_data": "back"}]]


def _to_inline_kb(data: List[List[Dict[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(**b) for b in row] for row in data]
    )


# ===== Структура Тип → Подкатегории для транспонированного 'Subcategory' =====
def _build_type_subcat_mapping(spreadsheet_id: str) -> dict:
    """
    Ожидаемый формат диапазона 'Subcategory':
      A1: 'Тип'           B1..N1: значения типа (Траты/Поступления/...)
      A2: 'Категория'     B2..N2: (не используется здесь)
      A3: 'Подкатегории'  B3..N3: подкатегории

    Возвращает:
      {"types": [...], "by_type": {"Траты":[...], "Поступления":[...]}}
    """
    assert _get_values_from_spreadsheet is not None, "configure() не вызван"

    values = _get_values_from_spreadsheet("Subcategory", spreadsheet_id) or []
    if not values or len(values) < 3:
        return {"types": [], "by_type": {}}

    row_type = row_sub = None
    for row in values:
        if not row:
            continue
        first = str(row[0]).strip().lower()
        if first == "тип":
            row_type = row
        elif first == "подкатегории":
            row_sub = row

    if not row_type or not row_sub:
        logging.warning("_build_type_subcat_mapping: строки 'Тип'/'Подкатегории' не найдены")
        return {"types": [], "by_type": {}}

    by_type: dict[str, list[str]] = {}
    types_order: list[str] = []

    max_len = min(len(row_type), len(row_sub))
    for col in range(1, max_len):
        t = str(row_type[col]).strip()
        s = str(row_sub[col]).strip()
        if not t or not s:
            continue
        if t not in by_type:
            by_type[t] = []
            types_order.append(t)
        if s not in by_type[t]:
            by_type[t].append(s)

    return {"types": types_order, "by_type": by_type}


# ===== Публичные функции =====
async def build_and_cache_all_keyboards() -> None:
    """
    Строит кэш клавиатур ДЛЯ ВСЕХ компаний из clients.json5.
    Никаких _config/CONFIG/projects.
    """
    from bogataya_life.client_access import load_clients_config

    cfg = load_clients_config()
    clients = cfg.get("clients", {}) or {}

    cache = _load_cache()
    cache["companies"] = {}
    _save_cache(cache)

    for client_key in clients.keys():
        # НИКАКИХ continue: пытаемся собрать, при ошибке падаем с понятной причиной
        await refresh_company_keyboards(client_key)

    # на всякий — сохраняем, но refresh_company_keyboards уже сохраняет
    _save_cache(_load_cache())





async def refresh_user_keyboards(user_id: int) -> None:
    """Пересобирает и сохраняет клавиатуры только для одного пользователя (после добавления)."""
    assert _find_spreadsheet_id_for_user is not None, "configure() не вызван"
    sid = _find_spreadsheet_id_for_user(user_id)

    kb_cat = _build_keyboard("Subcategory", sid, "category_", 3)
    kb_acc = _build_keyboard("Accounts",    sid, "account_",  2)
    kb_own = _build_keyboard("Owners",      sid, "owner_",    2)
    type_map = _build_type_subcat_mapping(sid)

    cache = _load_cache()
    cache[str(user_id)] = {
        "spreadsheet_id": sid,
        "category": kb_cat,
        "account":  kb_acc,
        "owners":   kb_own,
        "types": type_map["types"],
        "subcat_by_type": type_map["by_type"],
    }
    _save_cache(cache)
    logging.info(f"Кэш клавиатур обновлён для user_id={user_id}")


def get_user_keyboards(user_id: int) -> tuple[InlineKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardMarkup]:
    """
    Возвращает клавиатуры (категории, счета, владельцы) из кэша
    или создаёт на лету и кэширует (если записи нет).
    """
    cache = _load_cache()
    entry = cache.get(str(user_id))

    if entry is None:
        assert _find_spreadsheet_id_for_user is not None, "configure() не вызван"
        sid = _find_spreadsheet_id_for_user(user_id)

        kb_cat = _build_keyboard("Subcategory", sid, "category_", 3)
        kb_acc = _build_keyboard("Accounts",    sid, "account_",  2)
        kb_own = _build_keyboard("Owners",      sid, "owner_",    2)
        type_map = _build_type_subcat_mapping(sid)

        entry = {
            "spreadsheet_id": sid,
            "category": kb_cat,
            "account":  kb_acc,
            "owners":   kb_own,
            "types": type_map["types"],
            "subcat_by_type": type_map["by_type"],
        }
        cache[str(user_id)] = entry
        _save_cache(cache)

    return (
        _to_inline_kb(entry["category"]),
        _to_inline_kb(entry["account"]),
        _to_inline_kb(entry["owners"]),
    )


def get_type_keyboard_for_user(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура с типами операций. Если записи в кэше нет — создаём её на лету."""
    cache = _load_cache()
    client_key = resolve_client_key(user_id)
    entry = get_company_entry(client_key)

    if entry is None:
        # нет кэша — строим минимум (типовую мапу) на лету
        assert _find_spreadsheet_id_for_user is not None, "configure() не вызван"
        sid = _find_spreadsheet_id_for_user(user_id)
        type_map = _build_type_subcat_mapping(sid)

        # если хочется — можно сразу добить и прочие клавиатуры,
        # но для показа типов достаточно сохранить types/by_type
        entry = {
            "spreadsheet_id": sid,
            "category":  [],  # можно оставить пустым, их построит get_user_keyboards при необходимости
            "account":   [],
            "owners":    [],
            "types": type_map["types"],
            "subcat_by_type": type_map["by_type"],
        }
        cache[str(user_id)] = entry
        _save_cache(cache)

    types = entry.get("types", [])
    rows = [[{"text": t, "callback_data": f"type_{t}"}] for t in types]
    rows.append([{"text": "🔙 Назад", "callback_data": "back"}])
    return _to_inline_kb(rows)


def _chunk(lst, n: int):
    n = max(1, int(n or 1))
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def get_category_keyboard_for_user_and_type(user_id: int, exp_type: str, row_width: int = 3) -> InlineKeyboardMarkup:
    """Клавиатура подкатегорий (столбец C) для выбранного типа (столбец A)."""
    cache = _load_cache()
    entry = cache.get(str(user_id), {})
    subcats = entry.get("subcat_by_type", {}).get(exp_type, [])

    rows: list[list[dict]] = []
    for chunk in _chunk(subcats, row_width):
        rows.append([{"text": s, "callback_data": f"category_{s}"} for s in chunk])

    rows.append([{"text": "🔙 Назад", "callback_data": "back"}])
    return _to_inline_kb(rows)


def build_projects_keyboard(CONFIG: Dict[str, Any], admin_user_id: int, row_width: int = 2) -> InlineKeyboardMarkup:
    """Клавиатура проектов для админа/проект-админа."""
    allowed_names = get_admin_projects(admin_user_id, CONFIG)
    buttons = [InlineKeyboardButton(text=name, callback_data=f"admproj_{name}") for name in allowed_names]
    rows = [buttons[i:i + row_width] for i in range(0, len(buttons), row_width)]
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return pyjson5.load(f)
    except FileNotFoundError:
        return {"companies": {}}
    except Exception:
        return {"companies": {}}


def _save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        pyjson5.dump(cache, f, ensure_ascii=False, indent=2)


def remove_company_from_cache(client_key: str) -> None:
    cache = _load_cache()
    cache.setdefault("companies", {}).pop(client_key, None)
    _save_cache(cache)


async def refresh_company_keyboards(client_key: str) -> None:
    """
    Пересобирает клавиатуры для ОДНОЙ компании и сохраняет в кеш.
    Источник истины: clients.json5["clients"][client_key]["registry_sheet_id"].
    """
    from bogataya_life.client_access import load_clients_config

    assert _get_values_from_spreadsheet is not None, "configure() не вызван"

    cfg = load_clients_config()
    client = (cfg.get("clients") or {}).get(client_key)
    if not client:
        raise ValueError(f"Company '{client_key}' not found in clients.json5")

    sid = client.get("registry_sheet_id")
    if not sid:
        raise ValueError(f"Company '{client_key}' has no registry_sheet_id in clients.json5")

    kb_cat = _build_keyboard("Subcategory", sid, "category_", 3)
    kb_acc = _build_keyboard("Accounts",    sid, "account_",  2)
    kb_own = _build_keyboard("Owners",      sid, "owner_",    2)
    type_map = _build_type_subcat_mapping(sid)

    cache = _load_cache()
    cache.setdefault("companies", {})[client_key] = {
        "spreadsheet_id": sid,
        "category": kb_cat,
        "account":  kb_acc,
        "owners":   kb_own,
        "types": type_map["types"],
        "subcat_by_type": type_map["by_type"],
    }
    _save_cache(cache)


def get_company_keyboards(client_key: str) -> tuple[InlineKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardMarkup]:
    """
    Возвращает (категории, счета, владельцы) для компании из кеша.
    Если в кеше нет — бросаем, чтобы вызывающий решил (пересобрать/сообщить).
    """
    cache = _load_cache()
    entry = (cache.get("companies") or {}).get(client_key)
    if not entry:
        raise KeyError(f"No keyboards cache for company {client_key}")

    return (
        _to_inline_kb(entry["category"]),
        _to_inline_kb(entry["account"]),
        _to_inline_kb(entry["owners"]),
    )


def get_company_entry(client_key: str) -> dict:
    cache = _load_cache()
    entry = (cache.get("companies") or {}).get(client_key)
    if not entry:
        raise KeyError(f"No keyboards cache for company {client_key}")
    return entry


def get_user_keyboards(user_id: int) -> tuple[InlineKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardMarkup]:
    """
    Сохраняем совместимость со старым кодом:
    пользователь -> компания -> клавиатуры компании.
    """
    client_key = resolve_client_key(user_id)
    return get_company_keyboards(client_key)

