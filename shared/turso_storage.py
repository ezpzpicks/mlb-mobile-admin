"""Turso storage backend for EZPZ model/admin data.

This module intentionally keeps the database contract independent of Streamlit
and Google Sheets. During migration the existing Sheets storage layer can call
``replace_dataset`` after a successful Sheet write. Once validation is complete,
the same functions can become the authoritative read/write path and the Sheets
implementation can be removed.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterable, Mapping

import pandas as pd


_URL_NAMES = (
    "TURSO_DATABASE_URL",
    "TURSO_URL",
    "turso_TURSO_DATABASE_URL",
    "DATABASE_URL",
)
_TOKEN_NAMES = (
    "TURSO_AUTH_TOKEN",
    "TURSO_DATABASE_AUTH_TOKEN",
    "turso_TURSO_AUTH_TOKEN",
    "TURSO_TOKEN",
    "DATABASE_AUTH_TOKEN",
)

_TRANSIENT_HTTP_CODES = {409, 429, 500, 502, 503, 504}
_TRANSIENT_TURSO_MARKERS = (
    "sqlite_busy",
    "database is locked",
    "database is busy",
    "transaction busy",
    "transaction is busy",
    "write conflict",
    "temporarily unavailable",
    "too many requests",
    "rate limit",
)


def _first_env(names: Iterable[str]) -> str:
    for name in names:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _endpoint(value: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if text.startswith("libsql://"):
        return "https://" + text[len("libsql://") :]
    if text.startswith("https://") or text.startswith("http://"):
        return text
    return f"https://{text}" if text else ""


def is_turso_ready() -> bool:
    return bool(_first_env(_URL_NAMES) and _first_env(_TOKEN_NAMES))


def _is_transient_turso_error(message: object) -> bool:
    text = str(message or "").lower()
    return any(marker in text for marker in _TRANSIENT_TURSO_MARKERS)


def _pipeline(requests: list[dict], timeout: float = 30.0, max_attempts: int = 4) -> list[dict]:
    url = _endpoint(_first_env(_URL_NAMES))
    token = _first_env(_TOKEN_NAMES)
    if not url or not token:
        raise RuntimeError("Turso is not configured.")

    payload = {"requests": [*requests, {"type": "close"}]}
    encoded_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    attempts = max(1, int(max_attempts or 1))
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            f"{url}/v2/pipeline",
            data=encoded_payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            error = RuntimeError(f"Turso HTTP {exc.code}: {body[:800]}")
            last_error = error
            if attempt < attempts and (exc.code in _TRANSIENT_HTTP_CODES or _is_transient_turso_error(body)):
                time.sleep(0.20 * (2 ** (attempt - 1)))
                continue
            raise error from exc
        except urllib.error.URLError as exc:
            error = RuntimeError(f"Could not reach Turso: {exc}")
            last_error = error
            if attempt < attempts:
                time.sleep(0.20 * (2 ** (attempt - 1)))
                continue
            raise error from exc

        decoded = json.loads(raw or "{}")
        results = list(decoded.get("results") or [])
        statement_error = None
        for result in results:
            if result.get("type") == "error" or (result.get("response") or {}).get("type") == "error":
                statement_error = RuntimeError(f"Turso statement failed: {json.dumps(result)[:1000]}")
                break
        if statement_error is None:
            return results

        last_error = statement_error
        if attempt < attempts and _is_transient_turso_error(statement_error):
            time.sleep(0.20 * (2 ** (attempt - 1)))
            continue
        raise statement_error

    if last_error is not None:
        raise last_error
    raise RuntimeError("Turso request failed without a response.")


def _execute(sql: str) -> dict:
    results = _pipeline([{"type": "execute", "stmt": {"sql": sql, "args": []}}])
    return results[0] if results else {}


def _sql_text(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _result_rows(result: dict) -> list[dict[str, str]]:
    data = ((result.get("response") or {}).get("result") or {})
    columns = [str((column or {}).get("name") or "") for column in data.get("cols") or []]
    rows: list[dict[str, str]] = []
    for source in data.get("rows") or []:
        item: dict[str, str] = {}
        for index, column in enumerate(columns):
            if not column:
                continue
            cell = source[index] if index < len(source) else {}
            if not cell or cell.get("type") == "null":
                item[column] = ""
            else:
                item[column] = str(cell.get("value") or "")
        rows.append(item)
    return rows


def _metadata(row: Mapping[str, object]) -> dict[str, str]:
    def first(*names: str) -> str:
        for name in names:
            value = row.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    return {
        "date_key": first("Date", "date", "Record Date"),
        "game_key": first("Game Key", "gameKey", "Game ID", "gameId"),
        "game": first("Game", "game", "Game Label"),
        "market": first("Market", "market", "Bet Type", "Play Type"),
        "selection": first("Selection", "selection", "Play", "Side"),
        "result": first("Result", "result", "Status"),
        "snapshot_time": first("Snapshot Time ET", "snapshotTime", "Locked At", "Result Updated"),
    }


def _normalize_records(dataframe: pd.DataFrame | None, columns: Iterable[str]) -> list[dict[str, str]]:
    columns = list(columns)
    if dataframe is None:
        return []
    out = dataframe.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = ""
    out = out[columns].fillna("").astype(str)
    return [
        {column: str(value or "") for column, value in record.items()}
        for record in out.to_dict(orient="records")
    ]


_DATASET_WRITE_BATCH = contextvars.ContextVar("ezpz_turso_dataset_write_batch", default=None)


def _dataset_replace_requests(
    sport: str,
    dataset: str,
    records: list[dict[str, str]],
    headers: list[str],
    saved_at: str,
) -> list[dict]:
    """Build the statements for one logical dataset replacement without opening a transaction."""
    requests: list[dict] = [
        {
            "type": "execute",
            "stmt": {
                "sql": f"DELETE FROM dataset_rows WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)}",
                "args": [],
            },
        },
    ]

    prefix = (
        "INSERT OR REPLACE INTO dataset_rows "
        "(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) VALUES "
    )
    tuples: list[str] = []
    size = len(prefix)

    def flush() -> None:
        nonlocal tuples, size
        if not tuples:
            return
        requests.append(
            {"type": "execute", "stmt": {"sql": prefix + ",".join(tuples), "args": []}}
        )
        tuples = []
        size = len(prefix)

    for index, row in enumerate(records, start=1):
        payload = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        info = _metadata(row)
        values = (
            _sql_text(sport),
            _sql_text(dataset),
            str(index),
            _sql_text(payload),
            _sql_text(digest),
            _sql_text(info["date_key"]),
            _sql_text(info["game_key"]),
            _sql_text(info["game"]),
            _sql_text(info["market"]),
            _sql_text(info["selection"]),
            _sql_text(info["result"]),
            _sql_text(info["snapshot_time"]),
            _sql_text(saved_at),
        )
        item = "(" + ",".join(values) + ")"
        if tuples and (len(tuples) >= 100 or size + len(item) > 350_000):
            flush()
        tuples.append(item)
        size += len(item) + 1
    flush()

    manifest_sql = (
        "INSERT OR REPLACE INTO dataset_manifest "
        "(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) VALUES ("
        f"{_sql_text(sport)},{_sql_text(dataset)},'admin-turso-native',{_sql_text(dataset)},"
        f"{_sql_text(json.dumps(headers, separators=(',', ':')))},"
        f"{len(records)},{_sql_text(saved_at)},'turso')"
    )
    requests.append({"type": "execute", "stmt": {"sql": manifest_sql, "args": []}})
    return requests


@contextmanager
def batch_dataset_writes():
    """Commit multiple replace_dataset calls in one atomic Turso transaction.

    Reads inside the block see the latest queued version of a dataset, so existing
    read-modify-write helpers keep their current semantics even when the same
    dataset (for example Bet Tracker) is updated more than once during one save.
    """
    existing = _DATASET_WRITE_BATCH.get()
    if existing is not None:
        yield
        return

    pending: dict[tuple[str, str], dict] = {}
    token = _DATASET_WRITE_BATCH.set(pending)
    try:
        yield
        if not pending:
            return

        requests: list[dict] = [
            {"type": "execute", "stmt": {"sql": "BEGIN IMMEDIATE", "args": []}},
        ]
        saved_at = pd.Timestamp.utcnow().isoformat()
        for (sport, dataset), entry in pending.items():
            requests.extend(
                _dataset_replace_requests(
                    sport,
                    dataset,
                    list(entry.get("records") or []),
                    list(entry.get("headers") or []),
                    saved_at,
                )
            )
        requests.append({"type": "execute", "stmt": {"sql": "COMMIT", "args": []}})
        _pipeline(requests, timeout=90.0)
    finally:
        _DATASET_WRITE_BATCH.reset(token)


def replace_dataset(
    sport: str,
    dataset: str,
    dataframe: pd.DataFrame | None,
    columns: Iterable[str],
) -> int:
    """Atomically replace one logical dataset in Turso."""
    if not is_turso_ready():
        return 0

    sport = str(sport or "").strip().upper()
    if sport == "CFB":
        sport = "NCAAF"
    if sport == "CBB":
        sport = "NCAAM"
    dataset = str(dataset or "").strip()
    if not sport or not dataset:
        raise ValueError("sport and dataset are required for Turso persistence")

    headers = list(columns)
    records = _normalize_records(dataframe, headers)

    active_batch = _DATASET_WRITE_BATCH.get()
    if active_batch is not None:
        active_batch[(sport, dataset)] = {"headers": headers, "records": records}
        return len(records)

    saved_at = pd.Timestamp.utcnow().isoformat()

    requests: list[dict] = [
        {"type": "execute", "stmt": {"sql": "BEGIN IMMEDIATE", "args": []}},
        {
            "type": "execute",
            "stmt": {
                "sql": f"DELETE FROM dataset_rows WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)}",
                "args": [],
            },
        },
    ]

    prefix = (
        "INSERT OR REPLACE INTO dataset_rows "
        "(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) VALUES "
    )
    tuples: list[str] = []
    size = len(prefix)

    def flush() -> None:
        nonlocal tuples, size
        if not tuples:
            return
        requests.append(
            {"type": "execute", "stmt": {"sql": prefix + ",".join(tuples), "args": []}}
        )
        tuples = []
        size = len(prefix)

    for index, row in enumerate(records, start=1):
        payload = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        info = _metadata(row)
        values = (
            _sql_text(sport),
            _sql_text(dataset),
            str(index),
            _sql_text(payload),
            _sql_text(digest),
            _sql_text(info["date_key"]),
            _sql_text(info["game_key"]),
            _sql_text(info["game"]),
            _sql_text(info["market"]),
            _sql_text(info["selection"]),
            _sql_text(info["result"]),
            _sql_text(info["snapshot_time"]),
            _sql_text(saved_at),
        )
        item = "(" + ",".join(values) + ")"
        if tuples and (len(tuples) >= 100 or size + len(item) > 350_000):
            flush()
        tuples.append(item)
        size += len(item) + 1
    flush()

    manifest_sql = (
        "INSERT OR REPLACE INTO dataset_manifest "
        "(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) VALUES ("
        f"{_sql_text(sport)},{_sql_text(dataset)},'admin-turso-native',{_sql_text(dataset)},"
        f"{_sql_text(json.dumps(headers, separators=(',', ':')))},"
        f"{len(records)},{_sql_text(saved_at)},'turso')"
    )
    requests.append({"type": "execute", "stmt": {"sql": manifest_sql, "args": []}})
    requests.append({"type": "execute", "stmt": {"sql": "COMMIT", "args": []}})
    _pipeline(requests, timeout=45.0)
    return len(records)


def read_dataset(sport: str, dataset: str, columns: Iterable[str]) -> pd.DataFrame:
    """Read one dataset from Turso into the DataFrame shape expected by builders."""
    columns = list(columns)
    if not is_turso_ready():
        return pd.DataFrame(columns=columns)
    sport = str(sport or "").strip().upper()
    if sport == "CFB":
        sport = "NCAAF"
    if sport == "CBB":
        sport = "NCAAM"
    dataset = str(dataset or "").strip()

    active_batch = _DATASET_WRITE_BATCH.get()
    if active_batch is not None:
        pending = active_batch.get((sport, dataset))
        if pending is not None:
            pending_records = list(pending.get("records") or [])
            return pd.DataFrame(
                [
                    {column: str(record.get(column, "") or "") for column in columns}
                    for record in pending_records
                ],
                columns=columns,
            )

    result = _execute(
        "SELECT payload_json FROM dataset_rows "
        f"WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)} ORDER BY row_index ASC"
    )
    records: list[dict[str, str]] = []
    for row in _result_rows(result):
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except Exception:
            payload = {}
        records.append({column: str(payload.get(column, "") or "") for column in columns})
    return pd.DataFrame(records, columns=columns)
