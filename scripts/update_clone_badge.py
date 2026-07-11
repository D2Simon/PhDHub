#!/usr/bin/env python3
"""Accumulate GitHub clone traffic and render a 30-day README badge."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fetch_traffic(repository: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/traffic/clones",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PhDHub-traffic-badge",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def update_history(history_path: Path, traffic: dict, today: datetime) -> dict[str, int]:
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
    else:
        history = {}
    for item in traffic.get("clones", []):
        day = str(item.get("timestamp", ""))[:10]
        if day:
            history[day] = int(item.get("count", 0))
    cutoff = (today.date() - timedelta(days=29)).isoformat()
    history = {day: count for day, count in sorted(history.items()) if day >= cutoff}
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    return history


def render_badge(output_path: Path, clone_count: int, updated: str) -> None:
    value = f"{clone_count:,}"
    label_width = 148
    value_width = max(64, 16 + len(value) * 9)
    width = label_width + value_width
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="28" role="img" aria-label="Clones in last 30 days: {value}">
  <title>Clones in last 30 days: {value}; updated {updated} UTC</title>
  <linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#fff" stop-opacity=".12"/><stop offset="1" stop-opacity=".12"/></linearGradient>
  <clipPath id="r"><rect width="{width}" height="28" rx="4"/></clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="28" fill="#333"/>
    <rect x="{label_width}" width="{value_width}" height="28" fill="#6f42c1"/>
    <rect width="{width}" height="28" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="Verdana,Arial,sans-serif" font-size="11">
    <text x="{label_width / 2}" y="18">clones / last 30 days</text>
    <text x="{label_width + value_width / 2}" y="18" font-weight="bold">{value}</text>
  </g>
</svg>
'''
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", "D2Simon/PhDHub"))
    parser.add_argument("--history", type=Path, default=Path("metrics/clone_history.json"))
    parser.add_argument("--output", type=Path, default=Path("fig/clone-count.svg"))
    parser.add_argument("--input", type=Path, help="Use a saved API response instead of making a request")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    if args.input:
        traffic = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        token = os.getenv("TRAFFIC_TOKEN", "")
        if not token:
            raise SystemExit("TRAFFIC_TOKEN is required")
        traffic = fetch_traffic(args.repository, token)
    history = update_history(args.history, traffic, now)
    render_badge(args.output, sum(history.values()), now.strftime("%Y-%m-%d %H:%M"))


if __name__ == "__main__":
    main()
