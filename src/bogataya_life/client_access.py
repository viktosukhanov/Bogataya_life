from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple, List, Dict, Any

import pyjson5

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_PATH = Path(os.getenv("BOGATAYA_CLIENTS_CONFIG", PROJECT_ROOT / "clients.json5"))


def load_clients_config() -> dict:
    if not CLIENTS_PATH.exists():
        raise FileNotFoundError(f"clients config not found: {CLIENTS_PATH}")
    with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
        return pyjson5.load(f)


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


def resolve_client_key(user_id: int) -> str:
    cfg = load_clients_config()  # всегда свежий файл
    client_key = (cfg.get("users") or {}).get(str(user_id))
    if not client_key:
        raise PermissionError("User not mapped to any client in clients.json5")
    return client_key


def resolve_registry_sheet_id(user_id: int) -> str:
    cfg = load_clients_config()  # всегда свежий файл
    client_key = (cfg.get("users") or {}).get(str(user_id))
    if not client_key:
        raise PermissionError("User not mapped to any client in clients.json5")
    return cfg["clients"][client_key]["registry_sheet_id"]


def is_user_allowed(user_id: int) -> bool:
    cfg = load_clients_config()  # всегда свежий файл
    client_key = (cfg.get("users") or {}).get(str(user_id))
    if not client_key:
        return False
    allowed = ((cfg.get("clients") or {}).get(client_key) or {}).get("allowed_user_ids", [])
    return user_id in set(map(int, allowed))


def update_users_for_requester(requester_id: int) -> tuple[str, list[int], list[int], int]:
    """
    Пересобирает пользователей ТОЛЬКО компании requester-а.

    Источник: admins_sheet_id компании, вкладка "Пользователи бота" (колонка A по умолчанию).
    Записывает:
      - clients[client]["allowed_user_ids"] = полный список из таблицы
      - users: удаляет все старые записи этой компании и ставит новые

    Возвращает: (client_key, added_ids, removed_ids, total_now)
    """
    cfg = load_clients_config()

    client_key = (cfg.get("users") or {}).get(str(requester_id))
    if not client_key:
        raise PermissionError("Requester is not mapped to any client in clients.json5")

    client = (cfg.get("clients") or {}).get(client_key)
    if not client:
        raise PermissionError("Client not found in clients.json5")

    admins_sheet_id = client["admins_sheet_id"]
    users_range = client.get("users_range", "'Пользователи бота'!A:A")

    # локальный импорт -> нет циклических импортов
    import bogataya_life.google_sheets as gs
    values = gs.get_values_from_spreadsheet(users_range, admins_sheet_id) or []
    new_ids = _parse_user_ids(values)

    users_map: dict[str, str] = cfg.get("users") or {}
    old_ids = [int(uid_s) for uid_s, ck in users_map.items() if ck == client_key]

    new_set = set(new_ids)
    old_set = set(old_ids)

    added = sorted(list(new_set - old_set))
    removed = sorted(list(old_set - new_set))

    # 1) сохранить полный список в clients[client]
    client["allowed_user_ids"] = new_ids
    (cfg.get("clients") or {})[client_key] = client

    # 2) удалить все старые users для этой компании
    for uid_s, ck in list(users_map.items()):
        if ck == client_key:
            users_map.pop(uid_s, None)

    # 3) добавить актуальные users для этой компании
    for uid in new_ids:
        users_map[str(uid)] = client_key

    cfg["users"] = users_map

    _save_clients_config(cfg)
    return client_key, added, removed, len(new_ids)
