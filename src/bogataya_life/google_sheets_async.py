import datetime as dt
import logging
import asyncio
from typing import Sequence

import httplib2
from oauth2client.service_account import ServiceAccountCredentials
import apiclient.discovery

# --- Глобальный сервис переиспользуем между вызовами ---
_service = None

def init_sheets_service(credentials_file: str) -> None:
    """Вызывается один раз при старте бота."""
    global _service
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(credentials_file, scopes)
    http_auth = creds.authorize(httplib2.Http())
    _service = apiclient.discovery.build("sheets", "v4", http=http_auth, cache_discovery=False)

def _insert_values_sync(spreadsheet_id: str, range_name: str, values: Sequence[Sequence]):
    """Синхронная вставка (в отдельном потоке)."""
    assert _service is not None, "init_sheets_service() must be called first"
    body = {"values": values if values and isinstance(values[0], (list, tuple)) else [values]}
    result = (
        _service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="OVERWRITE",
            body=body,
        )
        .execute()
    )
    logging.info(f"{dt.datetime.now()} Data inserted successfully {range_name} -> {result.get('updates',{})}")
    return result

async def insert_values_async(spreadsheet_id: str, range_name: str, values: Sequence[Sequence]):
    """Асинхронная обёртка: не блокирует цикл событий."""
    return await asyncio.to_thread(_insert_values_sync, spreadsheet_id, range_name, values)
