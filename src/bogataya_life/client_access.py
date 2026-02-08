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
    cfg = load_clients_config()
    clients = cfg.get("clients", {}) or {}

    found = None
    for ck, c in clients.items():
        allowed = c.get("allowed_user_ids", []) or []
        if user_id in set(map(int, allowed)):
            if found and found != ck:
                raise PermissionError(f"User {user_id} mapped to multiple clients: {found}, {ck}")
            found = ck

    if not found:
        raise PermissionError("User not allowed for any client")
    return found


def resolve_registry_sheet_id(user_id: int) -> str:
    cfg = load_clients_config()
    ck = resolve_client_key(user_id)
    return cfg["clients"][ck]["registry_sheet_id"]


def is_user_allowed(user_id: int) -> bool:
    try:
        resolve_client_key(user_id)
        return True
    except Exception:
        return False


def update_users_for_requester(requester_id: int) -> tuple[str, list[int], list[int], int]:
    """
    Обновляет allowed_user_ids ТОЛЬКО для компании requester-а:
    - requester должен уже быть в allowed_user_ids этой компании (bootstrap)
    Источник: admins_sheet -> вкладка "Пользователи бота"
    """
    cfg = load_clients_config()
    clients = cfg.get("clients", {}) or {}

    # определить компанию requester-а по allowed_user_ids
    requester_client = None
    for ck, c in clients.items():
        if requester_id in set(map(int, (c.get("allowed_user_ids", []) or []))):
            requester_client = ck
            break
    if not requester_client:
        raise PermissionError("Requester is not in allowed_user_ids of any client (bootstrap required)")

    client = clients[requester_client]
    admins_sheet_id = client["admins_sheet_id"]
    users_range = client.get("users_range", "'Пользователи бота'!A:A")

    import bogataya_life.google_sheets as gs
    values = gs.get_values_from_spreadsheet(users_range, admins_sheet_id) or []
    new_ids = _parse_user_ids(values)

    old_ids = list(map(int, client.get("allowed_user_ids", []) or []))

    new_set = set(new_ids)
    old_set = set(old_ids)

    added = sorted(list(new_set - old_set))
    removed = sorted(list(old_set - new_set))

    client["allowed_user_ids"] = new_ids
    clients[requester_client] = client
    cfg["clients"] = clients

    _save_clients_config(cfg)
    return requester_client, added, removed, len(new_ids)
