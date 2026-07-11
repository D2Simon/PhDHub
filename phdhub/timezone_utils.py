"""Timezone helpers for professor local-time display."""

from datetime import datetime
from zoneinfo import ZoneInfo


COUNTRY_TZ_MAP = {
    "united states": "America/New_York",
    "usa": "America/New_York",
    "united kingdom": "Europe/London",
    "uk": "Europe/London",
    "china": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong",
    "macau": "Asia/Macau",
    "singapore": "Asia/Singapore",
    "japan": "Asia/Tokyo",
    "france": "Europe/Paris",
    "germany": "Europe/Berlin",
    "netherlands": "Europe/Amsterdam",
    "switzerland": "Europe/Zurich",
    "canada": "America/Toronto",
    "australia": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
    "korea": "Asia/Seoul",
    "south korea": "Asia/Seoul",
    "taiwan": "Asia/Taipei",
    "italy": "Europe/Rome",
    "spain": "Europe/Madrid",
    "sweden": "Europe/Stockholm",
    "denmark": "Europe/Copenhagen",
    "norway": "Europe/Oslo",
    "finland": "Europe/Helsinki",
    "austria": "Europe/Vienna",
    "ireland": "Europe/Dublin",
    "portugal": "Europe/Lisbon",
    "greece": "Europe/Athens",
    "belgium": "Europe/Brussels",
}


def _normalize_country(raw_country):
    s = str(raw_country or "").strip().lower()
    # "🇺🇸 United States" -> "united states"
    if " " in s:
        parts = s.split(" ")
        if parts and len(parts[0]) <= 3:
            s = " ".join(parts[1:])
    return s


def get_timezone_by_country(raw_country):
    c = _normalize_country(raw_country)
    for k, v in COUNTRY_TZ_MAP.items():
        if k in c:
            return v
    return ""


def format_local_time(raw_country):
    tz_name = get_timezone_by_country(raw_country)
    if not tz_name:
        return "未知"
    try:
        now = datetime.now(ZoneInfo(tz_name))
        return now.strftime("%H:%M · %Y-%m-%d")
    except Exception:
        return "未知"
