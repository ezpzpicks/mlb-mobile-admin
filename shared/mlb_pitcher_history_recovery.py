"""One-time repair for the MLB pitcher_recent_form history table.

The live history tab was truncated after a transient Google Sheets read was
mistaken for an empty table. Recover the prior rows from the validated 2026-08-24
replay snapshot, merge them with whatever is currently live, and never create a
new model-version-specific history tab.

This helper is deliberately idempotent. Once the live workbook again contains a
normal amount of prior history, it returns without touching either workbook.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials


BACKUP_SPREADSHEET_ID = "17tbocGVEWlw09fDOxnC2mQjHKVMURUZ8Ecfxv64WJzk"
RECENT_FORM_TAB = "pitcher_recent_form"
MIN_HEALTHY_PRIOR_ROWS = 300


def _today_et() -> str:
    try:
        return datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def _authorized_client():
    raw = str(os.environ.get("GOOGLE_CREDENTIALS", "") or "").strip()
    if not raw:
        return None
    credentials = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    return gspread.authorize(credentials)


def _open_live_workbook(client):
    sheet_id = str(os.environ.get("GOOGLE_SHEET_ID", "") or "").strip()
    sheet_name = str(os.environ.get("GOOGLE_SHEET_NAME", "") or "").strip()
    if sheet_id:
        return client.open_by_key(sheet_id)
    if sheet_name:
        return client.open(sheet_name)
    return None


def _history_key(row: dict[str, str]) -> str:
    game_key = str(row.get("Game Key", "") or "").strip()
    if not game_key:
        game_key = "|".join(
            [
                str(row.get("Team", "") or "").strip(),
                str(row.get("Opponent", "") or "").strip(),
            ]
        )
    pitcher = str(row.get("Pitcher", "") or "").lower().replace(".", "").replace("'", "").strip()
    role = str(row.get("Role", "") or "").strip().upper()
    return "|".join([str(row.get("Date", "") or "").strip(), game_key, pitcher, role])


def _rows_from_values(values, target_header, *, historical_only=False):
    if not values:
        return []
    source_header = [str(value).strip() for value in values[0]]
    positions = {name: index for index, name in enumerate(source_header)}
    today = _today_et()
    rows = []

    # The replay sheet reused the first 50 production columns exactly through
    # Early Exit. Its final replay-analysis columns have different headers, so
    # intentionally restore only fields that exist under the same name in both
    # sheets. Recent Form Applied / Calibration Notes / Updated Time ET can stay
    # blank on recovered rows; none is needed to calculate recent-form results.
    for raw_row in values[1:]:
        row = {}
        for column in target_header:
            index = positions.get(column)
            row[column] = raw_row[index] if index is not None and index < len(raw_row) else ""
        row_date = str(row.get("Date", "") or "").strip()
        pitcher = str(row.get("Pitcher", "") or "").strip()
        if not row_date or not pitcher:
            continue
        if historical_only and row_date >= today:
            continue
        rows.append(row)
    return rows


def recover_pitcher_recent_form_if_needed() -> dict[str, object]:
    """Restore missing historical rows into the one persistent live history tab."""
    client = _authorized_client()
    if client is None:
        return {"status": "skipped", "reason": "no Google credentials"}

    live_book = _open_live_workbook(client)
    if live_book is None:
        return {"status": "skipped", "reason": "no live workbook configured"}

    live_ws = live_book.worksheet(RECENT_FORM_TAB)
    live_values = live_ws.get_all_values()
    if not live_values:
        return {"status": "skipped", "reason": "live history has no header"}

    live_header = [str(value).strip() for value in live_values[0]]
    if "Date" not in live_header or "Pitcher" not in live_header:
        return {"status": "skipped", "reason": "live history header is invalid"}

    live_rows = _rows_from_values(live_values, live_header, historical_only=False)
    today = _today_et()
    prior_rows = [row for row in live_rows if str(row.get("Date", "") or "").strip() < today]
    if len(prior_rows) >= MIN_HEALTHY_PRIOR_ROWS:
        return {
            "status": "healthy",
            "prior_rows": len(prior_rows),
            "total_rows": len(live_rows),
        }

    backup_book = client.open_by_key(BACKUP_SPREADSHEET_ID)
    backup_ws = backup_book.worksheet(RECENT_FORM_TAB)
    backup_values = backup_ws.get_all_values()
    backup_rows = _rows_from_values(backup_values, live_header, historical_only=True)
    if not backup_rows:
        return {"status": "skipped", "reason": "backup contained no historical rows"}

    # Backup first, then live: today's rows and any already-repaired live rows win
    # on duplicate keys. This keeps the repair additive and non-destructive.
    merged = {}
    for row in backup_rows:
        merged[_history_key(row)] = row
    for row in live_rows:
        merged[_history_key(row)] = row

    merged_rows = list(merged.values())
    merged_rows.sort(
        key=lambda row: (
            str(row.get("Date", "") or ""),
            str(row.get("Game Key", "") or ""),
            str(row.get("Pitcher", "") or ""),
            str(row.get("Role", "") or ""),
        )
    )

    matrix = [live_header] + [
        [str(row.get(column, "") or "") for column in live_header]
        for row in merged_rows
    ]

    # No clear(). The repaired table is larger than the truncated table and is
    # written in place. If the update request fails, the existing live data is
    # left intact rather than erased first.
    live_ws.update(matrix)
    restored_prior = sum(
        1 for row in merged_rows if str(row.get("Date", "") or "").strip() < today
    )
    return {
        "status": "restored",
        "prior_rows_before": len(prior_rows),
        "prior_rows_after": restored_prior,
        "total_rows_after": len(merged_rows),
    }
