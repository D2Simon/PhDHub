"""Shared file paths and defaults for local persistence."""

import os


_DATA_DIR = os.environ.get("PHDHUB_DATA_DIR", "").strip()


def _data_path(name):
    """Return a local path, optionally rooted under PHDHUB_DATA_DIR."""
    if not _DATA_DIR:
        return name
    os.makedirs(_DATA_DIR, exist_ok=True)
    return os.path.join(_DATA_DIR, name)


CONFIG_FILE = _data_path("phdhub_config.json")
DB_FILE = _data_path("phdhub_db.json")
EMAILS_CACHE_FILE = _data_path("phdhub_emails_cache.json")
LITE_EMAILS_FILE = _data_path("phdhub_lite_emails.json")

DEFAULT_CONFIG = {
    "email": "",
    "password": "",
    "imap_server": "imap.gmail.com",
    "smtp_server": "smtp.gmail.com",
}

RESUME_DIR = _data_path("resumes")
RESUME_INDEX_FILE = _data_path("phdhub_resumes.json")

RP_DIR = _data_path("rps")
RP_INDEX_FILE = _data_path("phdhub_rps.json")
