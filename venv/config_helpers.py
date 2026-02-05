# config_helpers.py
import json, pyjson5, os

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.json5")


def save_config(CONFIG: dict):
    """Сохраняем CONFIG в JSON5 (через json.dump для совместимости)."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)
