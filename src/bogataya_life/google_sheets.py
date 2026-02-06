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


# Грузим конфиг (json или json5 — оба прочтёт pyjson5)
CONFIG_PATH = Path(os.getenv("BOGATAYA_CONFIG", PROJECT_ROOT / "config.json5"))
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG: Dict[str, Any] = pyjson5.load(f)

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
    """
    Возвращает spreadsheet_id по user_id на основе CONFIG["projects"][...]["allowed_user_ids"].
    """
    for project in CONFIG.get("projects", []):
        if int(user_id) in [int(u) for u in project.get("allowed_user_ids", [])]:
            return project["spreadsheet_id"]
    raise PermissionError(f"user_id {user_id} не найден в allowed_user_ids ни одного проекта")

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

    result = service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption='USER_ENTERED',
        insertDataOption='OVERWRITE',
        body=body
    ).execute()

    logging.info(f'{datetime.datetime.now()} Data inserted successfully {range_name}')


