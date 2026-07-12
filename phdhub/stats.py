"""Analytics helpers for dashboard metrics."""

from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime


def get_email_stats_from_emails(emails, recent_days=None):
    stats = {
        "sent_inquiry": 0,
        "replied_total": 0,
        "positive_reply": 0,
        "negative_reply": 0,
        "neutral_reply": 0,
    }

    if not emails:
        return stats

    today = datetime.now().date()
    window_start = None
    if isinstance(recent_days, int) and recent_days > 0:
        window_start = today - timedelta(days=recent_days - 1)

    seen_ids = set()
    for em in emails:
        if not em.get("is_phd_related"):
            continue

        mail_id = str(em.get("id", "") or "").strip()
        if mail_id and mail_id in seen_ids:
            continue
        if mail_id:
            seen_ids.add(mail_id)

        cat = em.get("phd_category")
        if cat not in [1, 2, 3, 4]:
            continue

        if window_start is not None:
            raw_date = em.get("date", "")
            try:
                dt = parsedate_to_datetime(raw_date)
                if dt is None:
                    continue
                if dt.tzinfo is not None:
                    dt = dt.astimezone()
                mail_date = dt.date()
            except Exception:
                continue
            if not (window_start <= mail_date <= today):
                continue

        if cat == 1:
            stats["sent_inquiry"] += 1
        elif cat == 2:
            stats["positive_reply"] += 1
            stats["replied_total"] += 1
        elif cat == 3:
            stats["negative_reply"] += 1
            stats["replied_total"] += 1
        elif cat == 4:
            stats["neutral_reply"] += 1
            stats["replied_total"] += 1

    return stats


def get_recent_7d_email_stats_from_emails(emails):
    return get_email_stats_from_emails(emails, recent_days=7)


def get_daily_sent_inquiries_from_emails(emails, recent_days=7, today=None):
    """Return a complete daily series for sent-inquiry emails in the date window."""
    end_date = today or datetime.now().date()
    start_date = end_date - timedelta(days=recent_days - 1)
    counts = {start_date + timedelta(days=i): 0 for i in range(recent_days)}
    seen_ids = set()
    for em in emails or []:
        if not em.get("is_phd_related") or em.get("phd_category") != 1:
            continue
        mail_id = str(em.get("id", "") or "").strip()
        if mail_id and mail_id in seen_ids:
            continue
        if mail_id:
            seen_ids.add(mail_id)
        try:
            dt = parsedate_to_datetime(em.get("date", ""))
            if dt is None:
                continue
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            mail_date = dt.date()
        except Exception:
            continue
        if start_date <= mail_date <= end_date:
            counts[mail_date] += 1
    return [{"日期": day, "发信数量": count} for day, count in counts.items()]
