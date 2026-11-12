# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A dataset's split is one Parquet file; DuckDB reads and writes it.

An uploaded file is JSON Lines, a JSON array, CSV or Parquet. It is turned
into Parquet once, and every later read is DuckDB on that file.

Every split file carries two columns besides the data:

- `_row` (BIGINT): the row's identity, unique within the dataset across its
  versions. An import numbers its rows; a later version keeps the `_row` of
  each row it keeps, and gives a new one to each row it adds.
- `_origin` (STRUCT): where the row came from. `kind` is real or synthetic;
  `method`, `model` and `model_version` say how a synthetic row was made, and
  `source_version` and `source_row` the row it was made from. A real
  imported row has only `kind`.

They are not data: the columns a dataset lists, its mapping and the
examples a trainer reads leave them out.
"""

import datetime
import decimal
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

import duckdb

READERS = {
    ".jsonl": "read_json_auto({src}, format='newline_delimited')",
    ".json": "read_json_auto({src})",
    ".csv": "read_csv_auto({src})",
    ".parquet": "read_parquet({src})",
}


IDENTITY = {"_row", "_origin"}
REAL_ORIGIN = ("struct_pack(kind := 'real', method := NULL::VARCHAR, model := NULL::VARCHAR, "
               "model_version := NULL::VARCHAR, source_version := NULL::INTEGER, source_row := NULL::BIGINT)")


# DuckDB's own limit is 80% of the container's memory, which leaves too little for the process that
# runs it: the backend's 512 MiB, or an import job's. Past this limit a query writes to SPILL on disk.
MEMORY_LIMIT = "192MB"
SPILL = Path(tempfile.gettempdir()) / "duckdb-spill"
# A reader and a writer each hold a whole row group; DuckDB's 122,880 rows of long text are tens of MiB
# each, more than MEMORY_LIMIT leaves them. Every split file is written in groups of this many rows.
ROW_GROUP_ROWS = 20_000


def _connect() -> duckdb.DuckDBPyConnection:
    SPILL.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(config={"memory_limit": MEMORY_LIMIT, "temp_directory": str(SPILL)})


class TableError(ValueError):
    """The file cannot be read as a table; the message says why."""


def reader(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in READERS:
        raise TableError(f"{filename} must be .jsonl, .json, .csv or .parquet")
    return READERS[suffix]


def to_parquet(source: Path, filename: str, target: Path) -> None:
    """Write the rows of an uploaded file to a Parquet file."""
    try:
        with _connect() as db:
            relation = db.sql("SELECT * FROM " + reader(filename).format(src=_literal(source)))
            relation.write_parquet(str(target), row_group_size=ROW_GROUP_ROWS)
    except duckdb.Error as e:
        raise TableError(f"{filename} cannot be read: {e}") from e


def combine(sources: List[Path], target: Path) -> None:
    """Write the shards of one split as one Parquet file."""
    files = ", ".join(_literal(s) for s in sources)
    try:
        with _connect() as db:
            db.sql(f"SELECT * FROM read_parquet([{files}], union_by_name = true)").write_parquet(str(target), row_group_size=ROW_GROUP_ROWS)
    except duckdb.Error as e:
        raise TableError(f"the Parquet files cannot be combined: {e}") from e


def identify(source: Path, target: Path, first_row: int) -> int:
    """Write a split's rows with their identity and origin, as real rows of an
    import: `_row` is first_row plus the row's position in the file, so the
    same file always gets the same ids. Returns the row count.

    The rows are written in the file's order without a sort: DuckDB keeps a
    scan's order when it writes (preserve_insertion_order), and a sort of a
    large split does not fit in MEMORY_LIMIT."""
    columns = _columns(source)
    taken = sorted(IDENTITY & set(columns))
    if taken:
        raise TableError(f"the columns {taken} are reserved for each row's identity and origin; rename them")
    data = ", ".join(_name(c) for c in columns)
    try:
        with _connect() as db:
            db.sql(
                f"SELECT {data}, ({int(first_row)} + file_row_number)::BIGINT AS _row, {REAL_ORIGIN} AS _origin "
                f"FROM read_parquet({_literal(source)}, file_row_number = true)"
            ).write_parquet(str(target), row_group_size=ROW_GROUP_ROWS)
    except duckdb.Error as e:
        raise TableError(f"the rows cannot be given their identity: {e}") from e
    return describe(target)["rows"]


def identified(path: Path) -> bool:
    """Whether a Parquet file carries each row's identity and origin."""
    return IDENTITY <= set(_columns(path))


def describe(path: Path) -> Dict[str, Any]:
    """The row count and the data columns, with their types, of a Parquet
    file; `_row` and `_origin` are not data and are not among them."""
    with _connect() as db:
        relation = db.sql(f"SELECT * FROM read_parquet({_literal(path)})")
        rows = db.sql(f"SELECT count(*) FROM read_parquet({_literal(path)})").fetchone()[0]
        return {
            "rows": rows,
            "columns": [{"name": n, "type": str(t)} for n, t in zip(relation.columns, relation.types)
                        if n not in IDENTITY],
        }


def _columns(path: Path) -> List[str]:
    try:
        with _connect() as db:
            return db.sql(f"SELECT * FROM read_parquet({_literal(path)})").columns
    except duckdb.Error as e:
        raise TableError(f"{path.name} is not a readable Parquet file: {e}") from e


def _name(column: str) -> str:
    return '"' + column.replace('"', '""') + '"'


def head(path: Path, offset: int, limit: int) -> List[Dict[str, Any]]:
    """Rows of a Parquet file, as JSON values."""
    with _connect() as db:
        relation = db.sql(f"SELECT * FROM read_parquet({_literal(path)}) LIMIT {int(limit)} OFFSET {int(offset)}")
        names = relation.columns
        return [{n: _json(v) for n, v in zip(names, row)} for row in relation.fetchall()]


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value
