"""Persist and retrieve report dicts as JSON files under ``data/reports/``.

A small ``_meta`` block is injected into every saved report so listings can
be built without parsing the full file.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared import PROJECT_ROOT

_REPORTS_DIR: Path = PROJECT_ROOT / "data" / "reports"
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


class StorageError(RuntimeError):
    """Raised when a report can't be saved or loaded."""


# --- Save / load ---------------------------------------------------------

def save_report(
    data: dict[str, Any],
    *,
    report_id: str | None = None,
    report_type: str = "generic",
) -> str:
    """Persist ``data`` as JSON and return its ``report_id``.

    A ``_meta`` block (``{report_id, report_type, created_at}``) is merged
    into the saved JSON. If ``report_id`` is provided, the existing report
    (if any) is overwritten.

    Args:
        data: The report payload. Must be JSON-serializable.
        report_id: Optional caller-supplied id. If omitted, an id of the
            form ``"{report_type}_{YYYYMMDD-HHMMSS}_{6hex}"`` is generated.
        report_type: Free-form string used in the generated id and stored
            in ``_meta``.

    Returns:
        The ``report_id`` under which the report was saved.

    Raises:
        StorageError: If the report can't be written.
    """
    if report_id is None:
        report_id = _generate_id(report_type)
    elif not _ID_PATTERN.match(report_id):
        raise StorageError(
            f"Invalid report_id {report_id!r}: only letters, digits, '_' and '-' are allowed."
        )

    payload = dict(data)
    payload["_meta"] = {
        "report_id": report_id,
        "report_type": report_type,
        "created_at": _now_iso(),
    }

    try:
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTS_DIR / f"{report_id}.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except (OSError, TypeError) as exc:
        raise StorageError(f"Could not save report {report_id!r}: {exc}") from exc

    return report_id


def load_report(report_id: str) -> dict[str, Any]:
    """Load and return the report saved as ``report_id``.

    Raises:
        StorageError: If the report doesn't exist or can't be parsed.
    """
    if not _ID_PATTERN.match(report_id):
        raise StorageError(f"Invalid report_id {report_id!r}.")
    path = _REPORTS_DIR / f"{report_id}.json"
    if not path.is_file():
        raise StorageError(f"Report not found: {report_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"Could not read report {report_id!r}: {exc}") from exc


def list_reports(*, report_type: str | None = None) -> list[dict[str, Any]]:
    """List saved reports, newest first.

    Returns a list of ``_meta`` dicts (only — full report bodies are not
    loaded). Files that fail to parse are skipped.

    Args:
        report_type: If given, only reports with a matching ``report_type``
            are returned.
    """
    if not _REPORTS_DIR.is_dir():
        return []
    metas: list[dict[str, Any]] = []
    for path in _REPORTS_DIR.glob("*.json"):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = raw.get("_meta")
        if not isinstance(meta, dict):
            continue
        if report_type is not None and meta.get("report_type") != report_type:
            continue
        metas.append(meta)
    metas.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return metas


# --- Internals -----------------------------------------------------------

def _generate_id(report_type: str) -> str:
    safe_type = re.sub(r"[^A-Za-z0-9]+", "_", report_type).strip("_") or "report"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{safe_type}_{stamp}_{uuid.uuid4().hex[:6]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
