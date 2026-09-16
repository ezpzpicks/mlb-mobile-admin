"""Turso storage backend for EZPZ model/admin data.

The public API intentionally stays compatible with the builders, but dataset
replacement is differential: unchanged rows are never rewritten. This matters
because Turso bills/limits row writes and the previous implementation deleted
and reinserted every row for even a one-row change.
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


def _canonical_sport(sport: str) -> str:
    value = str(sport or "").strip().upper()
    if value == "CFB":
        return "NCAAF"
    if value == "CBB":
        return "NCAAM"
    return value


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


def _normalized_payload(value: str) -> dict[str, str]:
    try:
        raw = json.loads(value or "{}")
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): "" if item is None else str(item) for key, item in raw.items()}


def _row_tuple(sport: str, dataset: str, index: int, row: Mapping[str, str], saved_at: str) -> str:
    payload = json.dumps(dict(row), separators=(",", ":"), ensure_ascii=False)
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
    return "(" + ",".join(values) + ")"


def _insert_requests(
    sport: str,
    dataset: str,
    indexed_rows: list[tuple[int, Mapping[str, str]]],
    saved_at: str,
) -> list[dict]:
    if not indexed_rows:
        return []
    prefix = (
        "INSERT OR REPLACE INTO dataset_rows "
        "(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) VALUES "
    )
    requests: list[dict] = []
    tuples: list[str] = []
    size = len(prefix)

    def flush() -> None:
        nonlocal tuples, size
        if not tuples:
            return
        requests.append({"type": "execute", "stmt": {"sql": prefix + ",".join(tuples), "args": []}})
        tuples = []
        size = len(prefix)

    for index, row in indexed_rows:
        item = _row_tuple(sport, dataset, index, row, saved_at)
        if tuples and (len(tuples) >= 100 or size + len(item) > 350_000):
            flush()
        tuples.append(item)
        size += len(item) + 1
    flush()
    return requests


def _manifest_sql(sport: str, dataset: str, headers: list[str], row_count: int, saved_at: str) -> str:
    return (
        "INSERT OR REPLACE INTO dataset_manifest "
        "(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) VALUES ("
        f"{_sql_text(sport)},{_sql_text(dataset)},'admin-turso-native',{_sql_text(dataset)},"
        f"{_sql_text(json.dumps(headers, separators=(',', ':')))},"
        f"{int(row_count)},{_sql_text(saved_at)},'turso')"
    )


def _current_state(sport: str, dataset: str) -> tuple[dict[int, dict[str, str]], list[str], int | None]:
    requests = [
        {
            "type": "execute",
            "stmt": {
                "sql": (
                    "SELECT row_index,payload_json FROM dataset_rows "
                    f"WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)} ORDER BY row_index ASC"
                ),
                "args": [],
            },
        },
        {
            "type": "execute",
            "stmt": {
                "sql": (
                    "SELECT headers_json,row_count FROM dataset_manifest "
                    f"WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)} LIMIT 1"
                ),
                "args": [],
            },
        },
    ]
    results = _pipeline(requests)
    rows_result = results[0] if results else {}
    manifest_result = results[1] if len(results) > 1 else {}

    current: dict[int, dict[str, str]] = {}
    for item in _result_rows(rows_result):
        try:
            index = int(item.get("row_index") or 0)
        except Exception:
            continue
        if index > 0:
            current[index] = _normalized_payload(item.get("payload_json") or "{}")

    manifest_rows = _result_rows(manifest_result)
    headers: list[str] = []
    row_count: int | None = None
    if manifest_rows:
        try:
            parsed_headers = json.loads(manifest_rows[0].get("headers_json") or "[]")
            if isinstance(parsed_headers, list):
                headers = [str(value) for value in parsed_headers]
        except Exception:
            headers = []
        try:
            row_count = int(manifest_rows[0].get("row_count") or 0)
        except Exception:
            row_count = None
    return current, headers, row_count


def _dataset_sync_requests(
    sport: str,
    dataset: str,
    records: list[dict[str, str]],
    headers: list[str],
    saved_at: str,
) -> list[dict]:
    """Return only SQL statements needed to make one dataset match ``records``."""
    current, current_headers, current_row_count = _current_state(sport, dataset)
    changed: list[tuple[int, Mapping[str, str]]] = []
    for index, row in enumerate(records, start=1):
        if current.get(index) != row:
            changed.append((index, row))

    requests = _insert_requests(sport, dataset, changed, saved_at)
    max_existing = max(current.keys(), default=0)
    if max_existing > len(records):
        requests.append(
            {
                "type": "execute",
                "stmt": {
                    "sql": (
                        "DELETE FROM dataset_rows "
                        f"WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)} "
                        f"AND row_index>{len(records)}"
                    ),
                    "args": [],
                },
            }
        )

    manifest_changed = current_headers != headers or current_row_count != len(records)
    if changed or max_existing > len(records) or manifest_changed:
        requests.append(
            {"type": "execute", "stmt": {"sql": _manifest_sql(sport, dataset, headers, len(records), saved_at), "args": []}}
        )
    return requests


_DATASET_WRITE_BATCH = contextvars.ContextVar("ezpz_turso_dataset_write_batch", default=None)


@contextmanager
def batch_dataset_writes():
    """Commit multiple logical dataset updates in one Turso transaction.

    Each queued replacement is diffed against the database first, so unchanged
    rows are not charged as writes.
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

        saved_at = pd.Timestamp.utcnow().isoformat()
        write_requests: list[dict] = []
        for (sport, dataset), entry in pending.items():
            write_requests.extend(
                _dataset_sync_requests(
                    sport,
                    dataset,
                    list(entry.get("records") or []),
                    list(entry.get("headers") or []),
                    saved_at,
                )
            )
        if not write_requests:
            return
        requests = [
            {"type": "execute", "stmt": {"sql": "BEGIN IMMEDIATE", "args": []}},
            *write_requests,
            {"type": "execute", "stmt": {"sql": "COMMIT", "args": []}},
        ]
        _pipeline(requests, timeout=90.0)
    finally:
        _DATASET_WRITE_BATCH.reset(token)


def replace_dataset(
    sport: str,
    dataset: str,
    dataframe: pd.DataFrame | None,
    columns: Iterable[str],
) -> int:
    """Synchronize one logical dataset, writing only changed/new/deleted rows."""
    if not is_turso_ready():
        return 0

    sport = _canonical_sport(sport)
    dataset = str(dataset or "").strip()
    if not sport or not dataset:
        raise ValueError("sport and dataset are required for Turso persistence")

    headers = [str(column) for column in columns]
    records = _normalize_records(dataframe, headers)

    active_batch = _DATASET_WRITE_BATCH.get()
    if active_batch is not None:
        active_batch[(sport, dataset)] = {"headers": headers, "records": records}
        return len(records)

    saved_at = pd.Timestamp.utcnow().isoformat()
    write_requests = _dataset_sync_requests(sport, dataset, records, headers, saved_at)
    if not write_requests:
        return len(records)
    requests = [
        {"type": "execute", "stmt": {"sql": "BEGIN IMMEDIATE", "args": []}},
        *write_requests,
        {"type": "execute", "stmt": {"sql": "COMMIT", "args": []}},
    ]
    _pipeline(requests, timeout=60.0)
    return len(records)


def append_dataset_rows(
    sport: str,
    dataset: str,
    rows: Iterable[Mapping[str, object]],
    columns: Iterable[str],
) -> int:
    """Append rows without reading/replacing the existing logical dataset."""
    if not is_turso_ready():
        return 0

    sport = _canonical_sport(sport)
    dataset = str(dataset or "").strip()
    headers = [str(column) for column in columns]
    if not sport or not dataset:
        raise ValueError("sport and dataset are required for Turso persistence")

    normalized: list[dict[str, str]] = []
    for source in rows:
        normalized.append({column: str(source.get(column, "") or "") for column in headers})
    if not normalized:
        return 0

    active_batch = _DATASET_WRITE_BATCH.get()
    if active_batch is not None:
        pending = active_batch.get((sport, dataset))
        if pending is None:
            current = read_dataset(sport, dataset, headers)
            records = _normalize_records(current, headers)
        else:
            records = list(pending.get("records") or [])
        records.extend(normalized)
        active_batch[(sport, dataset)] = {"headers": headers, "records": records}
        return len(normalized)

    max_result = _execute(
        "SELECT COALESCE(MAX(row_index),0) AS max_index FROM dataset_rows "
        f"WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)}"
    )
    max_rows = _result_rows(max_result)
    try:
        start_index = int((max_rows[0] if max_rows else {}).get("max_index") or 0)
    except Exception:
        start_index = 0

    saved_at = pd.Timestamp.utcnow().isoformat()
    indexed = [(start_index + offset, row) for offset, row in enumerate(normalized, start=1)]
    write_requests = _insert_requests(sport, dataset, indexed, saved_at)
    write_requests.append(
        {
            "type": "execute",
            "stmt": {
                "sql": _manifest_sql(sport, dataset, headers, start_index + len(normalized), saved_at),
                "args": [],
            },
        }
    )
    requests = [
        {"type": "execute", "stmt": {"sql": "BEGIN IMMEDIATE", "args": []}},
        *write_requests,
        {"type": "execute", "stmt": {"sql": "COMMIT", "args": []}},
    ]
    _pipeline(requests, timeout=60.0)
    return len(normalized)


def read_dataset(sport: str, dataset: str, columns: Iterable[str]) -> pd.DataFrame:
    """Read one dataset from Turso into the DataFrame shape expected by builders."""
    columns = [str(column) for column in columns]
    if not is_turso_ready():
        return pd.DataFrame(columns=columns)
    sport = _canonical_sport(sport)
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
        payload = _normalized_payload(row.get("payload_json") or "{}")
        records.append({column: str(payload.get(column, "") or "") for column in columns})
    return pd.DataFrame(records, columns=columns)
