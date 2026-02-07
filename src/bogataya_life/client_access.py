from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pyjson5

from bogataya_life.google_sheets import get_values_from_spreadsheet
import json
from typing import Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_PATH = Path(os.getenv("BOGATAYA_CLIENTS_CONFIG", PROJECT_ROOT / "clients.json5"))


@dataclass(frozen=True)
class ClientInfo:
    key: str
    registry_sheet_id: str
    admins_sheet_id: str
    admins_range: str = "A:A"


def load_clients_config() -> dict:
    if not CLIENTS_PATH.exists():
        raise FileNotFoundError(
            f"clients config not found: {CLIENTS_PATH}. "
            f"Create it from clients.example.json5"
        )
    with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
        return pyjson5.load(f)


def get_client_key_for_user(user_id: int, cfg: dict) -> Optional[str]:
    return cfg.get("users", {}).get(str(user_id))


def get_client_info(client_key: str, cfg: dict) -> ClientInfo:
    raw = cfg["clients"][client_key]
    return ClientInfo(
        key=client_key,
        registry_sheet_id=raw["registry_sheet_id"],
        admins_sheet_id=raw["admins_sheet_id"],
        admins_range=raw.get("admins_range", "A:A"),
    )


# --- простое TTL-кеширование, чтобы не читать админ-лист на каждый апдейт ---
_ADMIN_CACHE: dict[str, tuple[float, set[int]]] = {}
_ADMIN_CACHE_TTL_SEC = int(os.getenv("ADMIN_CACHE_TTL_SEC", "30"))


def fetch_admin_ids(client: ClientInfo) -> set[int]:
    now = time.time()
    cached = _ADMIN_CACHE.get(client.key)
    if cached and now - cached[0] < _ADMIN_CACHE_TTL_SEC:
        return cached[1]

    values = get_values_from_spreadsheet(client.admins_range, client.admins_sheet_id)


    admin_ids: set[int] = set()
    for row in values or []:
        if not row:
            continue
        try:
            admin_ids.add(int(str(row[0]).strip()))
        except ValueError:
            # пропускаем мусор/заголовки
            continue

    _ADMIN_CACHE[client.key] = (now, admin_ids)
    return admin_ids


def is_user_allowed(user_id: int) -> bool:
    cfg = load_clients_config()
    client_key = get_client_key_for_user(user_id, cfg)
    if not client_key:
        return False

    client = get_client_info(client_key, cfg)
    return user_id in fetch_admin_ids(client)


def resolve_registry_sheet_id(user_id: int) -> str:
    cfg = load_clients_config()
    client_key = get_client_key_for_user(user_id, cfg)
    if not client_key:
        raise PermissionError("User not mapped to any client in clients.json5")

    client = get_client_info(client_key, cfg)
    return client.registry_sheet_id


def resolve_client_key(user_id: int) -> str:
    cfg = load_clients_config()
    client_key = get_client_key_for_user(user_id, cfg)
    if not client_key:
        raise PermissionError("User not mapped to any client in clients.json5")
    return client_key

def _save_clients_config(cfg: dict) -> None:
    tmp = CLIENTS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CLIENTS_PATH)


def _parse_user_ids(values: list[list[object]]) -> list[int]:
    out: list[int] = []
    seen = set()
    for row in values or []:
        if not row:
            continue
        s = str(row[0]).strip()
        if not s or not s.isdigit():
            continue
        uid = int(s)
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def update_users_for_requester(requester_id: int) -> tuple[str, list[int], list[int]]:
    """
    Обновляет users и allowed_user_ids ТОЛЬКО для компании requester-а.
    Источник: вкладка "Пользователи бота" в admins_sheet этой компании.

    Возвращает:
      (client_key, added_user_ids, removed_user_ids)
    """
    cfg = load_clients_config()

    client_key = cfg.get("users", {}).get(str(requester_id))
    if not client_key:
        raise PermissionError("Ваш telegram_id не привязан к компании в clients.json5")

    client = cfg["clients"].get(client_key)
    if not client:
        raise PermissionError("Компания не найдена в clients.json5")

    admins_sheet_id = client["admins_sheet_id"]
    users_range = client.get("users_range", "'Пользователи бота'!A:A")

    # сигнатура у тебя: get_values_from_spreadsheet(named_range, spreadsheet_id)
    values = get_values_from_spreadsheet(users_range, admins_sheet_id) or []
    new_ids = _parse_user_ids(values)  # list[int]

    # старые пользователи этой компании из clients.json5
    users_map: dict[str, str] = cfg.get("users", {}) or {}
    old_ids = [int(uid_s) for uid_s, ck in users_map.items() if ck == client_key]

    new_set = set(new_ids)
    old_set = set(old_ids)

    added = sorted(list(new_set - old_set))
    removed = sorted(list(old_set - new_set))

    # 1) обновляем список пользователей в clients[client]
    client["allowed_user_ids"] = new_ids
    cfg["clients"][client_key] = client

    # 2) убираем старые записи users_map для этой компании
    for uid_s, ck in list(users_map.items()):
        if ck == client_key:
            users_map.pop(uid_s, None)

    # 3) добавляем актуальные записи users_map для этой компании
    for uid in new_ids:
        users_map[str(uid)] = client_key

    cfg["users"] = users_map

    _save_clients_config(cfg)
    return client_key, added, removed
