"""Email capture: persist submitted emails to Supabase, with CSV fallback.

Order of attempt:
  1. Supabase ``emails`` table (production-grade, survives restarts).
  2. ``/tmp/geo_toolkit_emails.csv`` (ephemeral but still better than dropping
     the email; useful on local dev where Supabase isn't configured).

Both paths are best-effort — if neither succeeds, the email is silently lost
rather than blocking the user from completing the form.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from shared.supabase_client import save_email as _supabase_save_email

_CSV_PATH = Path("/tmp/geo_toolkit_emails.csv")  # writable on both macOS dev + Streamlit Cloud


def save_email(email: str, source: str = "unknown") -> None:
    """Persist an email. Tries Supabase first, falls back to /tmp CSV."""
    if _supabase_save_email(email, source):
        return
    _save_email_to_csv(email, source)


def _save_email_to_csv(email: str, source: str) -> None:
    """Append to /tmp CSV (fallback when Supabase isn't reachable)."""
    try:
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
    except OSError as exc:
        print(f"[email_capture] CSV fallback failed: {exc}", flush=True)


def list_emails() -> list[dict]:
    """Read all captured emails from the local CSV (admin / debug use only)."""
    if not _CSV_PATH.exists():
        return []
    with _CSV_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))
