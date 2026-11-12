# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""An uploaded file becomes one Parquet file; DuckDB describes and reads it."""

import json

import pytest

from app.services import tables


@pytest.mark.parametrize("name,content", [
    ("qa.jsonl", '{"q": "Hi?", "a": "Hello"}\n{"q": "Bye?", "a": "Bye"}\n'),
    ("qa.json", json.dumps([{"q": "Hi?", "a": "Hello"}, {"q": "Bye?", "a": "Bye"}])),
    ("qa.csv", "q,a\nHi?,Hello\nBye?,Bye\n"),
])
def test_each_upload_format_becomes_parquet(tmp_path, name, content):
    source = tmp_path / name
    source.write_text(content)
    target = tmp_path / "train.parquet"
    tables.to_parquet(source, name, target)
    described = tables.describe(target)
    assert described["rows"] == 2
    assert [c["name"] for c in described["columns"]] == ["q", "a"]
    assert tables.head(target, 1, 5) == [{"q": "Bye?", "a": "Bye"}]


def test_nested_turns_are_read_as_json_values(tmp_path):
    source = tmp_path / "chat.jsonl"
    source.write_text(json.dumps({"messages": [{"role": "user", "content": "Hi"}]}) + "\n")
    target = tmp_path / "train.parquet"
    tables.to_parquet(source, "chat.jsonl", target)
    assert tables.head(target, 0, 1) == [{"messages": [{"role": "user", "content": "Hi"}]}]


def test_shards_of_a_split_are_combined(tmp_path):
    parts = []
    for n in range(2):
        source = tmp_path / f"part{n}.csv"
        source.write_text(f"x\n{n}\n")
        parts.append(tmp_path / f"part{n}.parquet")
        tables.to_parquet(source, source.name, parts[-1])
    tables.combine(parts, tmp_path / "all.parquet")
    assert tables.describe(tmp_path / "all.parquet")["rows"] == 2


def test_an_unknown_file_type_is_refused():
    with pytest.raises(tables.TableError, match=".jsonl, .json, .csv or .parquet"):
        tables.reader("data.xlsx")


def test_an_unreadable_file_names_itself(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text("{not json")
    with pytest.raises(tables.TableError, match="bad.json"):
        tables.to_parquet(source, "bad.json", tmp_path / "out.parquet")


def test_duckdb_is_bounded_and_spills_to_disk():
    with tables._connect() as db:
        limit, spill = db.sql("SELECT current_setting('memory_limit'), current_setting('temp_directory')").fetchone()
    assert limit == "183.1 MiB"
    assert spill == str(tables.SPILL)


def test_split_files_are_written_in_small_row_groups(tmp_path):
    source = tmp_path / "rows.jsonl"
    source.write_text("".join(json.dumps({"q": f"q{i}", "a": f"a{i}"}) + "\n" for i in range(5 * tables.ROW_GROUP_ROWS)))
    target = tmp_path / "train.parquet"
    tables.to_parquet(source, source.name, target)
    with tables._connect() as db:
        largest = db.sql(f"SELECT max(row_group_num_rows) FROM parquet_metadata('{target}')").fetchone()[0]
    # DuckDB fills a row group in whole vectors of 2,048 rows
    assert largest <= tables.ROW_GROUP_ROWS + 2048


def test_identify_keeps_the_files_order_across_row_groups(tmp_path):
    source = tmp_path / "rows.jsonl"
    source.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(5 * tables.ROW_GROUP_ROWS)))
    parquet, identified = tmp_path / "train.parquet", tmp_path / "train.identified.parquet"
    tables.to_parquet(source, source.name, parquet)
    assert tables.identify(parquet, identified, 7) == 5 * tables.ROW_GROUP_ROWS
    with tables._connect() as db:
        rows = db.sql(f"SELECT n, _row FROM read_parquet('{identified}')").fetchall()
    assert rows == [(i, 7 + i) for i in range(5 * tables.ROW_GROUP_ROWS)]
