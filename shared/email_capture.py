"""Email capture: persist submitted emails to a CSV for later review.

Notes:
- On Streamlit Cloud, the CSV is stored under /tmp (ephemeral — survives
  reruns within a session, but is wiped on each redeploy).
- For real production we'd back this with a database or Mailchimp/Resend
  webhook. For Day-6 MVP we just want a "did emails come in?" signal.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

_CSV_PATH = Path("/tmp/geo_toolkit_emails.csv")  # writable on both macOS dev + Streamlit Cloud


def save_email(email: str, source: str = "unknown") -> None:
    """Append an email submission to the CSV. Creates header row on first write."""
    _CSV_PATH.parent.mkdir(parents=True, exist_ok=True)

    is_new_file = not _CSV_PATH.exists()
    with _CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(["timestamp_utc", "email", "source"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            email,
            source,
        ])


def list_emails() -> list[dict]:
    """Read all captured emails (for admin / debug use)."""
    if not _CSV_PATH.exists():
        return []
    with _CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))
