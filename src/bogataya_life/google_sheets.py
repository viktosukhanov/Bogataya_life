import httplib2
import apiclient.discovery
from pathlib import Path
from oauth2client.service_account import ServiceAccountCredentials
import datetime

#from config import spreadsheet_id
import logging
from typing import Any, Dict, List
import os
import pyjson5

# корень проекта: .../Bogataya_life
PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Грузим конфиг (json или json5 — оба прочтёт pyjson5)
CONFIG_PATH = Path(os.getenv("BOGATAYA_CONFIG", PROJECT_ROOT / "config.json5"))
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG: Dict[str, Any] = pyjson5.load(f)
# --- загрузка clients.json5 ---
CLIENTS_PATH = Path(os.getenv("BOGATAYA_CLIENTS_CONFIG", PROJECT_ROOT / "clients.json5"))

def _load_clients_config() -> dict:
    if not CLIENTS_PATH.exists():
        return {"clients": {}, "users": {}}
    with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
        return pyjson5.load(f)


# --- временный мост совместимости ---
# Старый код ждёт CONFIG["projects"]
# Генерируем его из clients.json5
# генерируем projects из clients.json5 (читаем файл свежим)
def _build_projects_from_clients() -> list[dict]:
    from bogataya_life.client_access import load_clients_config
    cfg = load_clients_config()
    return [
        {
            "name": client_key,
            "spreadsheet_id": client_cfg["registry_sheet_id"],
            "admins_sheet_id": client_cfg["admins_sheet_id"],
        }
        for client_key, client_cfg in (cfg.get("clients", {}) or {}).items()
    ]

CONFIG["projects"] = _build_projects_from_clients()



# Файл, полученный в Google Developer Console
CREDENTIALS_FILE = 'creds_service_acc_google.json'


# Устанавливаем уровень логов
logging.basicConfig(level=logging.INFO, filename='log.txt', filemode='w', format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logging.getLogger('').addHandler(console_handler)

credentials = ServiceAccountCredentials.from_json_keyfile_name(
    CREDENTIALS_FILE,
    ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
)
http_auth = credentials.authorize(httplib2.Http())
service = apiclient.discovery.build('sheets', 'v4', http=http_auth)


# --- ниже: добавь саму функцию ---


def find_spreadsheet_id_for_user(user_id: int) -> str:
    from bogataya_life.client_access import load_clients_config
    cfg = load_clients_config()
    client_key = cfg.get("users", {}).get(str(user_id))
    if not client_key:
        raise PermissionError("User not mapped to any client in clients.json5")
    return cfg["clients"][client_key]["registry_sheet_id"]


def is_user_allowed(user_id: int) -> bool:
    client_key = CLIENTS_CONFIG.get("users", {}).get(str(user_id))
    if not client_key:
        return False

    client = CLIENTS_CONFIG["clients"].get(client_key)
    if not client:
        return False

    admins_sheet_id = client["admins_sheet_id"]
    admins_range = client.get("admins_range", "A:A")

    values = get_values_from_spreadsheet(admins_range, admins_sheet_id) or []
    admins = set()
    for row in values:
        if not row:
            continue
        try:
            admins.add(int(str(row[0]).strip()))
        except ValueError:
            continue

    return user_id in admins


# Авторизуемся и получаем service — экземпляр доступа к API
def get_values_from_spreadsheet(named_range, spreadsheet_id):
    """credentials = ServiceAccountCredentials.from_json_keyfile_name(
        CREDENTIALS_FILE,
        ['https://www.googleapis.com/auth/spreadsheets',
         'https://www.googleapis.com/auth/drive'])
    httpAuth = credentials.authorize(httplib2.Http())
    service = apiclient.discovery.build('sheets', 'v4', http=httpAuth)"""

    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=named_range, #f'Mapping!{list_range}',
        majorDimension='COLUMNS',
        valueRenderOption = 'UNFORMATTED_VALUE'
    ).execute()
    return values.get('values', [])

def get_last_filled_row(named_range, spreadsheet_id):
    """credentials = ServiceAccountCredentials.from_json_keyfile_name(
        CREDENTIALS_FILE,
        ['https://www.googleapis.com/auth/spreadsheets',
         'https://www.googleapis.com/auth/drive'])
    httpAuth = credentials.authorize(httplib2.Http())
    service = apiclient.discovery.build('sheets', 'v4', http=httpAuth)"""

    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=named_range,
        majorDimension='COLUMNS',
        valueRenderOption='UNFORMATTED_VALUE'
    ).execute()

    if 'values' in values:
        rows = values['values']
        last_filled_row = len(rows[0]) + 3  # Получаем номер следующей строки
    else:
        last_filled_row = 3  # Если нет заполненных строк, начинаем с первой

    return last_filled_row


def insert_values_to_spreadsheet(values, range_name):
    #credentials = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'])
    #http_auth = credentials.authorize(httplib2.Http())
    #service = apiclient.discovery.build('sheets', 'v4', http=http_auth)

    body = {
        'values': [values],
    }

def _load_clients_config() -> dict:
    if not CLIENTS_PATH.exists():
        # чтобы бот не падал сразу при импорте, если конфиг не создан
        return {"clients": {}, "users": {}}
    with open(CLIENTS_PATH, "r", encoding="utf-8") as f:
        return pyjson5.load(f)

CLIENTS_CONFIG = _load_clients_config()


def get_admin_ids_for_client(client: str) -> set[int]:
    sheet_id = CLIENTS_CONFIG["clients"][client]["admins_sheet_id"]

    values = get_values_from_spreadsheet(
        spreadsheet_id=sheet_id,
        range_name="A:A"
    )

    return {int(row[0]) for row in values if row}

    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='USER_ENTERED',
        insertDataOption='OVERWRITE',
        body=body
    ).execute()

    logging.info(f'{datetime.datetime.now()} Data inserted successfully {range_name}')


