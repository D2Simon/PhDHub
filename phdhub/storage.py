"""Local JSON persistence helpers."""

import json
import os
from copy import deepcopy

from .constants import CONFIG_FILE, DB_FILE, DEFAULT_CONFIG, LITE_EMAILS_FILE, TEMPLATES_FILE


def _read_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return deepcopy(default)


def _write_json(path, data, *, indent=4, ensure_ascii=False):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)


def load_config():
    return _read_json(CONFIG_FILE, DEFAULT_CONFIG)


def save_config(config_data):
    _write_json(CONFIG_FILE, config_data, indent=4, ensure_ascii=False)


def load_db():
    return _read_json(DB_FILE, [])


def save_db(data):
    _write_json(DB_FILE, data, indent=4, ensure_ascii=False)


def load_lite_emails():
    """Manually entered email records used by Lite mode."""
    return _read_json(LITE_EMAILS_FILE, [])


def save_lite_emails(data):
    _write_json(LITE_EMAILS_FILE, data, indent=4, ensure_ascii=False)


def load_templates():
    """User-authored cold-email (套瓷信) templates."""
    data = _read_json(TEMPLATES_FILE, [])
    return data if isinstance(data, list) else []


def save_templates(data):
    _write_json(TEMPLATES_FILE, data, indent=4, ensure_ascii=False)
