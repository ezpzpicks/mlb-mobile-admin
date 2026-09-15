from pathlib import Path


storage_path = Path("shared/turso_storage.py")
storage = storage_path.read_text(encoding="utf-8")

# 1) Add batching imports.
old_imports = '''import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Iterable, Mapping
'''
new_imports = '''import contextvars
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterable, Mapping
'''
if storage.count(old_imports) != 1:
    raise SystemExit(f"Expected one Turso import block, found {storage.count(old_imports)}")
storage = storage.replace(old_imports, new_imports, 1)

# 2) Insert the transaction-batch machinery immediately before replace_dataset.
marker = '''def replace_dataset(
    sport: str,
    dataset: str,
    dataframe: pd.DataFrame | None,
    columns: Iterable[str],
) -> int:
'''
if storage.count(marker) != 1:
    raise SystemExit(f"Expected one replace_dataset definition, found {storage.count(marker)}")

batch_support = '''_DATASET_WRITE_BATCH = contextvars.ContextVar("ezpz_turso_dataset_write_batch", default=None)


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


'''
storage = storage.replace(marker, batch_support + marker, 1)

# 3) Queue replace_dataset calls when a batch is active.
old_records = '''    headers = list(columns)
    records = _normalize_records(dataframe, headers)
    saved_at = pd.Timestamp.utcnow().isoformat()

    requests: list[dict] = [
'''
new_records = '''    headers = list(columns)
    records = _normalize_records(dataframe, headers)

    active_batch = _DATASET_WRITE_BATCH.get()
    if active_batch is not None:
        active_batch[(sport, dataset)] = {"headers": headers, "records": records}
        return len(records)

    saved_at = pd.Timestamp.utcnow().isoformat()

    requests: list[dict] = [
'''
if storage.count(old_records) != 1:
    raise SystemExit(f"Expected one replace_dataset records block, found {storage.count(old_records)}")
storage = storage.replace(old_records, new_records, 1)

# 4) Reads inside an active batch see the latest queued dataframe.
old_read = '''    if sport == "CBB":
        sport = "NCAAM"
    result = _execute(
        "SELECT payload_json FROM dataset_rows "
        f"WHERE sport={_sql_text(sport)} AND dataset={_sql_text(dataset)} ORDER BY row_index ASC"
    )
'''
new_read = '''    if sport == "CBB":
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
'''
if storage.count(old_read) != 1:
    raise SystemExit(f"Expected one read_dataset lookup block, found {storage.count(old_read)}")
storage = storage.replace(old_read, new_read, 1)
storage_path.write_text(storage, encoding="utf-8")


# 5) Use the batch only around the explicit MLB Save Matchup Summary operation.
builder_path = Path("builders/mlb_builder.py")
builder = builder_path.read_text(encoding="utf-8")

old_import = 'from shared.turso_storage import is_turso_ready, read_dataset, replace_dataset\n'
new_import = 'from shared.turso_storage import batch_dataset_writes, is_turso_ready, read_dataset, replace_dataset\n'
if builder.count(old_import) != 1:
    raise SystemExit(f"Expected one MLB Turso import, found {builder.count(old_import)}")
builder = builder.replace(old_import, new_import, 1)

save_start = '        save_started_at = time.perf_counter()\n'
save_end = '        add_bets_batch(tracker_bet_batch)\n'
start_index = builder.index(save_start) + len(save_start)
end_index = builder.index(save_end, start_index) + len(save_end)
body = builder[start_index:end_index]
if 'with batch_dataset_writes()' in body:
    raise SystemExit('MLB save body is already batched')
indented_body = ''.join(('    ' + line) if line.strip() else line for line in body.splitlines(keepends=True))
builder = builder[:start_index] + '        with batch_dataset_writes():\n' + indented_body + builder[end_index:]
builder_path.write_text(builder, encoding="utf-8")
