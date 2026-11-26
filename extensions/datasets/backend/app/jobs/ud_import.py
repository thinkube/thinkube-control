# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The step of a Universal Dependencies import, run by an Argo workflow in
Datasets' backend image (app/services/workflows.py), as a Hugging Face
import's is (app/jobs/hub_import.py).

It downloads each split's CoNLL-U files from GitHub, writes one Parquet file
per split with a row per sentence (app/services/conllu.py), the treebank of
each row in its column treebank, gives each row its `_row` and `_origin`,
and puts the files in the version's MLflow run. It writes what it made as
JSON to --result; a failure goes to the termination message, and the step
exits 1.

    python -m app.jobs.ud_import --experiment E --run R --first-row N \\
        --files '{"train": [{"url": ..., "name": ..., "treebank": ...}, ...]}' --result /tmp/result.json
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from app.services import conllu, mapping, storage, tables, ud

TERMINATION_LOG = Path("/dev/termination-log")


async def run(experiment_id: str, run_id: str, first_row: int, by_split: Dict[str, List[Dict[str, Any]]],
              work: Path) -> Dict[str, Any]:
    splits, columns = [], None
    for split, files in by_split.items():
        sources = []
        for n, f in enumerate(files):
            local = work / f"{split}-{n}.conllu"
            await ud.download(f["url"], local)
            sources.append((local, f["treebank"]))
        combined = work / f"{split}.parquet"
        await asyncio.to_thread(conllu.to_parquet, sources, combined)
        for local, _ in sources:
            local.unlink()
        identified = work / f"{split}.identified.parquet"
        first_row += await asyncio.to_thread(tables.identify, combined, identified, first_row)
        combined.unlink()
        described = await asyncio.to_thread(tables.describe, identified)
        columns = columns or described["columns"]
        await storage.upload(experiment_id, run_id, split, identified)
        splits.append({"name": split, "rows": described["rows"], "bytes": identified.stat().st_size})
        identified.unlink()
    return {
        "splits": splits,
        "columns": columns,
        "next_row": first_row,
        "mapping": mapping.guess([c["name"] for c in columns]),
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.ud_import")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--first-row", type=int, required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="ud-") as work:
            made = asyncio.run(run(args.experiment, args.run, args.first_row, json.loads(args.files), Path(work)))
    except (ud.UdError, tables.TableError, storage.StorageError) as e:
        TERMINATION_LOG.write_text(str(e))
        print(e, file=sys.stderr)
        return 1
    Path(args.result).write_text(json.dumps(made))
    print(json.dumps({k: made[k] for k in ("splits", "next_row")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
