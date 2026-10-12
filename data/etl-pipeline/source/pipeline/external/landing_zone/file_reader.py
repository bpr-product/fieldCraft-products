import csv
import io
import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"csv", "tsv", "xlsx", "xls", "json", "jsonl", "parquet"}

# Hard cap on in-memory blob size before decode/parse — an unauthenticated
# upstream endpoint makes an oversized file trivially remotely triggerable
# (memory-exhaustion DoS) if this isn't enforced before decode(). Override via
# MAX_FILE_READ_BYTES; default 50 MB.
MAX_FILE_READ_BYTES = int(os.environ.get("MAX_FILE_READ_BYTES", str(50 * 1024 * 1024)))


class UnsupportedFormatError(ValueError):
    pass


class FileTooLargeError(ValueError):
    pass


def detect_format(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in SUPPORTED_FORMATS:
        raise UnsupportedFormatError(f"Unsupported file format: .{ext}")
    return ext


def read_file(
    data: bytes,
    fmt: str,
    delimiter: str = ",",
    has_header: bool = True,
    sheet_name: str | None = None,
) -> list[dict[str, Any]]:
    if len(data) > MAX_FILE_READ_BYTES:
        raise FileTooLargeError(
            f"File content ({len(data)} bytes) exceeds MAX_FILE_READ_BYTES "
            f"({MAX_FILE_READ_BYTES} bytes) — refusing to decode/parse"
        )

    if fmt in ("csv", "tsv"):
        sep    = "\t" if fmt == "tsv" else delimiter
        text   = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), delimiter=sep)
        return [dict(row) for row in reader]

    if fmt in ("xlsx", "xls"):
        import openpyxl
        wb   = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws   = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        headers = [str(h) for h in rows[0]] if has_header else [str(i) for i in range(len(rows[0]))]
        start   = 1 if has_header else 0
        return [dict(zip(headers, (str(v) if v is not None else "" for v in row))) for row in rows[start:]]

    if fmt == "jsonl":
        result = []
        for line in data.decode("utf-8").splitlines():
            line = line.strip()
            if line:
                result.append(json.loads(line))
        return result

    if fmt == "json":
        parsed = json.loads(data.decode("utf-8"))
        if isinstance(parsed, list):
            return parsed
        return [parsed]

    if fmt == "parquet":
        import pyarrow.parquet as pq
        table = pq.read_table(io.BytesIO(data))
        return table.to_pylist()

    raise UnsupportedFormatError(f"No reader for format: {fmt}")


def peek_headers(
    data: bytes,
    fmt: str,
    delimiter: str = ",",
    has_header: bool = True,
    sheet_name: str | None = None,
) -> list[str]:
    """Return column names without reading all rows. Fast path for schema detection."""
    if fmt in ("csv", "tsv"):
        sep  = "\t" if fmt == "tsv" else delimiter
        text = data[:8192].decode("utf-8-sig", errors="replace")
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if not has_header:
            return []
        reader = csv.reader(io.StringIO(first_line), delimiter=sep)
        return next(reader, [])

    if fmt in ("xlsx", "xls"):
        import openpyxl
        wb   = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws   = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
        rows = ws.iter_rows(values_only=True, max_row=1)
        first = next(rows, None)
        return [str(h) for h in first] if first and has_header else []

    if fmt == "jsonl":
        first_line = data.decode("utf-8").splitlines()[0].strip() if data else ""
        obj = json.loads(first_line) if first_line else {}
        return list(obj.keys()) if isinstance(obj, dict) else []

    if fmt == "json":
        parsed = json.loads(data.decode("utf-8"))
        first  = parsed[0] if isinstance(parsed, list) and parsed else parsed
        return list(first.keys()) if isinstance(first, dict) else []

    if fmt == "parquet":
        import pyarrow.parquet as pq
        schema = pq.read_schema(io.BytesIO(data))
        return schema.names

    return []
