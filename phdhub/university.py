"""University source, QS-rank lookup, and school-name normalization.

Backed by a built-in static QS World University Rankings 2027 top-500 list
(``phdhub/qs_top500.py``) so the dropdown, the QS-rank lookup and the
import-time name normalization all share one source of truth — manual entries
and bulk imports therefore always store the exact same school string.
"""

import re
import unicodedata

from phdhub.qs_top500 import QS_TOP500, QS_EDITION, CONTINENTS, COUNTRY_FLAG
from phdhub.usnews_top100 import USNEWS_TOP100, USNEWS_EDITION


def _norm(s):
    """Normalize a school name for fuzzy matching (accent/case/punct-insensitive)."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"\([^)]*\)", " ", s)          # drop parentheticals e.g. "(MIT)"
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"^the ", "", s)               # leading "The"
    return re.sub(r"\s+", " ", s).strip()


# norm_key -> (rank, official_name, country). Built once at import.
_LOOKUP = {}
# Manual aliases for common abbreviations GPT may emit, mapped to the official name.
_MANUAL_ALIASES = {
    "uc berkeley": "University of California, Berkeley",
    "berkeley": "University of California, Berkeley",
    "ucla": "University of California, Los Angeles",
    "uc san diego": "University of California, San Diego",
    "ucsd": "University of California, San Diego",
    "oxford": "University of Oxford",
    "cambridge": "University of Cambridge",
    "eth": "ETH Zurich",
    "epfl": "EPFL",
    "hku": "The University of Hong Kong",
    "cuhk": "The Chinese University of Hong Kong",
    "hkust": "The Hong Kong University of Science and Technology",
    "ntu": "Nanyang Technological University, Singapore",
    "sjtu": "Shanghai Jiao Tong University",
    "ust": "The Hong Kong University of Science and Technology",
}

for _rk, _name, _cty, _alias in QS_TOP500:
    _LOOKUP.setdefault(_norm(_name), (_rk, _name, _cty))
    if _alias:
        _LOOKUP.setdefault(_norm(_alias), (_rk, _name, _cty))

# Resolve manual aliases against the official names already loaded.
_name_to_rec = {_norm(n): (rk, n, c) for rk, n, c, _ in QS_TOP500}
for _alias_key, _official in _MANUAL_ALIASES.items():
    _rec = _name_to_rec.get(_norm(_official))
    if _rec:
        _LOOKUP.setdefault(_norm(_alias_key), _rec)


# US News rank lookup (norm_key -> rank). And enrich the general lookup so US-News-only
# schools (not in QS top 500) still resolve to a canonical name + country "United States".
_USNEWS_RANK = {}
for _rk, _name, _alias in USNEWS_TOP100:
    _key = _norm(_name)
    _USNEWS_RANK.setdefault(_key, _rk)
    if _alias:
        _USNEWS_RANK.setdefault(_norm(_alias), _rk)
    # If this school isn't already known via QS, register it (country = United States).
    if _key not in _LOOKUP:
        _LOOKUP.setdefault(_key, ("", _name, "United States"))
    if _alias and _norm(_alias) not in _LOOKUP:
        _LOOKUP.setdefault(_norm(_alias), _LOOKUP[_key])


def usnews_rank_for(name):
    """Return the US News national-university rank (int) for a school, or "" if not top 100."""
    if not name:
        return ""
    return _USNEWS_RANK.get(_norm(name), "")


def qs_lookup(name):
    """Return (rank, official_name, country) for a school name, or None if no match."""
    if not name:
        return None
    return _LOOKUP.get(_norm(name))


def canonical_school_name(name):
    """Snap a school name to its official QS string; return input unchanged if unknown."""
    rec = qs_lookup(name)
    return rec[1] if rec else str(name or "").strip()


def qs_rank_for(name):
    """Return the QS rank (int) for a school name, or "" if it isn't in the top 500."""
    rec = qs_lookup(name)
    return rec[0] if rec else ""


def country_for(name):
    """Return the QS country for a school name, or "" if unknown."""
    rec = qs_lookup(name)
    return rec[2] if rec else ""


def get_qs_rank(name):
    """Backward-compatible 0-based rank (9999 if unranked)."""
    rec = qs_lookup(name)
    return (rec[0] - 1) if rec else 9999


def get_world_universities():
    """Return {"<flag> <country>": ["Official Name (QS <edition> #rank)", ...]}.

    Grouped by country, sorted by QS rank. The "<flag> <country>" key shape and
    the trailing "(... #rank)" suffix match what the add/edit dialogs expect
    (the dialogs strip any trailing parenthetical before saving).

    The United States group is additionally enriched with the US News 2026 top-100
    national universities that aren't already in the QS top 500, labelled
    "(USNews <edition> #rank)".
    """
    by_country = {}
    for rk, name, cty, _alias in QS_TOP500:
        by_country.setdefault(cty, []).append((rk, name))
    out = {}
    for cty, items in by_country.items():
        items.sort(key=lambda t: (t[0], t[1]))
        flag = COUNTRY_FLAG.get(cty, "🎓")
        labels = []
        for rk, name in items:
            ranks = [f"QS {QS_EDITION} #{rk}"]
            usnews_rank = usnews_rank_for(name) if cty == "United States" else ""
            if usnews_rank:
                ranks.append(f"US News {USNEWS_EDITION} #{usnews_rank}")
            labels.append(f"{name} ({' · '.join(ranks)})")
        out[f"{flag} {cty}"] = labels

    # Enrich the US group with US-News-only schools (canonicalized, deduped by norm key).
    us_key = f"{COUNTRY_FLAG.get('United States', '🎓')} United States"
    us_list = out.get(us_key, [])
    seen = {_norm(re.sub(r'\s*\([^)]*\)\s*$', '', s)) for s in us_list}
    extra = []
    for rk, name, _alias in USNEWS_TOP100:
        canon = canonical_school_name(name)
        if _norm(canon) in seen:
            continue
        seen.add(_norm(canon))
        extra.append((rk, f"{canon} (US News {USNEWS_EDITION} #{rk})"))
    extra.sort(key=lambda t: (t[0], t[1]))
    if extra:
        out[us_key] = us_list + [label for _rk, label in extra]
    return out


def qs_top_by_country(country, limit=None):
    """Return [(rank, official_name), ...] for a country from the QS top-500, sorted by rank."""
    items = [(rk, name) for rk, name, cty, _alias in QS_TOP500
             if str(cty).strip().lower() == str(country).strip().lower()]
    items.sort(key=lambda t: (t[0], t[1]))
    return items[:limit] if limit else items


def usnews_top100_list():
    """Return [(rank, official_name), ...] for the US News 2026 top-100 national universities."""
    return [(rk, name) for rk, name, _alias in USNEWS_TOP100]
