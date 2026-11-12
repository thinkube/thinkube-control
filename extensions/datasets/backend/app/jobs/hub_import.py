# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""The step of a Hugging Face import, run by an Argo workflow in Datasets'
backend image (app/services/workflows.py).

It downloads each split's shards from the Hub, writes one Parquet file per
split with each row's `_row` and `_origin`, and puts the files in the
version's MLflow run, which the backend created. It touches no database:
it writes what it made as JSON to --result, an output of the step, and the
backend records it. A failure is written to the container's termination
message, which Argo shows as the step's message, and the step exits 1.

    python -m app.jobs.hub_import --experiment E --run R --first-row N \\
        --files '{"train": [{"url": ...}, ...]}' --result /tmp/result.json
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from app.services import huggingface, mapping, storage, tables

TERMINATION_LOG = Path("/dev/termination-log")


async def run(experiment_id: str, run_id: str, first_row: int, by_split: Dict[str, List[Dict[str, Any]]],
              work: Path) -> Dict[str, Any]:
    splits, columns, has_rows = [], None, False
    for split, shards in by_split.items():
        parts = []
        for n, shard in enumerate(shards):
            part = work / f"{split}-{n}.parquet"
            await huggingface.download(shard["url"], part)
            parts.append(part)
        combined = work / f"{split}.parquet"
        await asyncio.to_thread(tables.combine, parts, combined)
        for part in parts:
            part.unlink()
        identified = work / f"{split}.identified.parquet"
        first_row += await asyncio.to_thread(tables.identify, combined, identified, first_row)
        combined.unlink()
        described = await asyncio.to_thread(tables.describe, identified)
        columns = columns or described["columns"]
        has_rows = has_rows or described["rows"] > 0
        await storage.upload(experiment_id, run_id, split, identified)
        splits.append({"name": split, "rows": described["rows"], "bytes": identified.stat().st_size})
        identified.unlink()
    return {
        "splits": splits,
        "columns": columns,
        "next_row": first_row,
        "mapping": mapping.guess([c["name"] for c in columns]) if has_rows else None,
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="app.jobs.hub_import")
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--first-row", type=int, required=True)
    parser.add_argument("--files", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="hub-") as work:
            made = asyncio.run(run(args.experiment, args.run, args.first_row, json.loads(args.files), Path(work)))
    except (huggingface.HubError, tables.TableError, storage.StorageError) as e:
        TERMINATION_LOG.write_text(str(e))
        print(e, file=sys.stderr)
        return 1
    Path(args.result).write_text(json.dumps(made))
    print(json.dumps({k: made[k] for k in ("splits", "next_row")}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
