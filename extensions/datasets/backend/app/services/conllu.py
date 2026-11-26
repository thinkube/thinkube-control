# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""CoNLL-U files as rows of words: one row per sentence.

A row holds the sentence's syntactic words, as Meep²'s tagging reads them by
default (words="syntactic"): a multiword token's range line (id 3-4) and an
empty node (id 5.1) are left out, and its parts are the words. Each column is
one list with a value per word: tokens (FORM), lemma, upos, xpos, feats and
deprel as text, and head as a number (from 1, 0 for the root). A value of "_"
stays "_". sent_id and text come from the sentence's `# sent_id =` and
`# text =` comments, and treebank names the file's source.
"""

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import duckdb

from app.services import tables

# The Parquet columns of a words dataset, in this order
COLUMNS = {
    "tokens": "VARCHAR[]",
    "lemma": "VARCHAR[]",
    "upos": "VARCHAR[]",
    "xpos": "VARCHAR[]",
    "feats": "VARCHAR[]",
    "head": "INTEGER[]",
    "deprel": "VARCHAR[]",
    "treebank": "VARCHAR",
    "sent_id": "VARCHAR",
    "text": "VARCHAR",
}
FIELDS = 10


class ConlluError(tables.TableError):
    """A CoNLL-U file that cannot be read; the message names the file and the line."""


def sentences(path: Path, treebank: Optional[str]) -> Iterator[Dict[str, Any]]:
    """The file's sentences as rows."""
    words: List[List[str]] = []
    comments: Dict[str, str] = {}
    with path.open(encoding="utf-8") as f:
        for number, raw in enumerate(f, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip():
                if words:
                    yield _row(words, comments, treebank, path, number)
                words, comments = [], {}
                continue
            if line.startswith("#"):
                key, sep, value = line[1:].partition("=")
                if sep:
                    comments[key.strip()] = value.strip()
                continue
            fields = line.split("\t")
            if len(fields) != FIELDS:
                raise ConlluError(f"{path.name} line {number} has {len(fields)} fields, not {FIELDS}")
            if "-" in fields[0] or "." in fields[0]:
                continue
            words.append(fields)
    if words:
        yield _row(words, comments, treebank, path, number)


def _row(words: List[List[str]], comments: Dict[str, str], treebank: Optional[str], path: Path,
         number: int) -> Dict[str, Any]:
    try:
        heads = [int(w[6]) for w in words]
    except ValueError:
        raise ConlluError(f"{path.name}: the sentence ending at line {number} has a head that is not a number")
    return {
        "tokens": [w[1] for w in words],
        "lemma": [w[2] for w in words],
        "upos": [w[3] for w in words],
        "xpos": [w[4] for w in words],
        "feats": [w[5] for w in words],
        "head": heads,
        "deprel": [w[7] for w in words],
        "treebank": treebank,
        "sent_id": comments.get("sent_id"),
        "text": comments.get("text"),
    }


def to_parquet(sources: List[Tuple[Path, Optional[str]]], target: Path) -> int:
    """Write the sentences of the files, in their order, as one Parquet file;
    returns the number of sentences."""
    count = 0
    with tempfile.TemporaryDirectory(prefix="conllu-") as work:
        lines = Path(work) / "sentences.jsonl"
        with lines.open("w", encoding="utf-8") as out:
            for path, treebank in sources:
                for row in sentences(path, treebank):
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    count += 1
        if count == 0:
            raise ConlluError(f"{', '.join(p.name for p, _ in sources)} hold no sentence")
        columns = "{" + ", ".join(f"'{n}': '{t}'" for n, t in COLUMNS.items()) + "}"
        try:
            with tables._connect() as db:
                db.sql(f"SELECT * FROM read_json({tables._literal(lines)}, format='newline_delimited', "
                       f"columns={columns})").write_parquet(str(target), row_group_size=tables.ROW_GROUP_ROWS)
        except duckdb.Error as e:
            raise ConlluError(f"the sentences cannot be written as Parquet: {e}") from e
    return count
