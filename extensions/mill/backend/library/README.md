# thinkube-mill

The task standard of Thinkube Mill. A task is a data transformation step:
it calls a model over items, cleans a dataset, or scores results. This
package is how a task is written, how it is run, and how its run is
recorded so the control center can show it.

## A task

```python
from thinkube_mill import task, track, show, count

@task(
    inputs={"rows": "jsonl"},
    outputs={"cleaned": "jsonl"},
    settings={"min_words": 5},
)
def clean(rows, settings):
    """Keep the rows with enough words."""
    for row in track(rows, "cleaning"):
        if len(row["text"].split()) < settings["min_words"]:
            count("dropped: too short")
            continue
        out = {"id": row["id"], "text": row["text"].strip()}
        show(input=row, output=out)
        yield out
```

- `@task` declares the inputs, the outputs and the settings. A workflow
  reads the declaration to chain tasks; `mill describe <file>` prints it.
- The function has one parameter for each input. It may also have
  `settings` (a dict) and `out` (the outputs, by name).
- A function that yields writes each item to the task's one jsonl output.
  A function returns a dict to set its json and text outputs, or writes
  through `out`: `out["cleaned"].write(item)`, `out["report"].set(value)`,
  `out["model"].path`.

| Type | An input is | An output is written with |
|---|---|---|
| `jsonl` | the items, read one by one; `len()` says how many | `yield`, or `out[name].write(item)` |
| `json` | the value | the returned dict, or `out[name].set(value)` |
| `text` | the string | the returned dict, or `out[name].set(text)` |
| `path` | a `pathlib.Path` | the task itself, at `out[name].path` |

## What a task reports

| Call | Shows in the control center |
|---|---|
| `track(items, "name")` | a progress bar for the stage `name` |
| `show(input=..., output=...)` | a sample; any field names |
| `count("name")` | a running count |
| `measure("name", value)` | a running measure: count, mean, least, most |
| `fail("reason", input=...)` | a failed item; the run goes on |

A task that yields and never calls `show` has its items sampled for it.
A task that must stop raises an exception. Called outside a run, as plain
Python, all of these do nothing, so a task's function can be imported and
called anywhere.

tqdm bars drawn by other libraries become stages too: `mill run` puts a
reporting class in tqdm's place before it loads the task.

## Running a task

```bash
mill run clean.py --in rows=in.jsonl --out cleaned=out.jsonl --limit 50
mill run clean.py --in rows=in.jsonl --out cleaned=out.jsonl --set min_words=8
mill run tasks.py:clean ...          # a file with several tasks
mill status <run folder> --samples
```

`--limit N` reads only the first N items of each jsonl input: a way to try
a task in seconds. The command prints the run's folder when it starts and
the last status, as JSON, when it ends. Exit code 0 is completed, 1 failed,
143 stopped, 2 a wrong command or declaration.

An output is written beside its path, as `<name>.partial`, and takes its
name when the run completes. A run that fails or is stopped leaves the
partial file.

## The record of a run

Each run has a folder `<runs>/<run id>/`. `<runs>` is the folder given with
`--runs-dir`, else the one `MILL_RUNS_DIR` names, else `.mill/runs` at the
root of the git repository the task is in.

| File | Holds |
|---|---|
| `run.json` | the task, its inputs, outputs and settings, the commit, the host |
| `status.json` | the state (running, completed, failed, stopped), the stages, the counts and measures |
| `samples.json` | the latest 10 samples, and up to 20 spaced across the run |
| `failures.jsonl` | the first 200 failed items; `status.json` counts them all |

Text in a sample is cut at 2000 characters, with the rest counted.

## Tests

`./run_tests.sh`. The build of the Mill extension runs them.
