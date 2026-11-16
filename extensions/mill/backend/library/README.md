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
```

`--limit N` reads only the first N items of each jsonl input: a way to try
a task in seconds. The command prints the run's id when it starts and the
last status, as JSON, when it ends.

| Exit code | Means |
|---|---|
| 0 | completed |
| 1 | failed |
| 143 | stopped |
| 2 | the command or the declaration is wrong, or NATS cannot be reached |
| 4 | the run ended, and its last record could not be sent |

An output is written beside its path, as `<name>.partial`, and takes its
name when the run completes. A run that fails or is stopped leaves the
partial file.

## How a run reports

A run reports through NATS, wherever it executes, and the Mill control
center keeps its record. The server is the platform's NATS
(`nats://nats.nats.svc.cluster.local:4222`) unless `MILL_NATS_URL` names
another. A run publishes on `<subjects>.runs.<run id>.<kind>`; `<subjects>`
is `mill` unless `MILL_SUBJECTS` names another prefix.

| Kind | Holds | Sent |
|---|---|---|
| `started` | the task, its inputs, outputs and settings, the commit, the host | once |
| `status` | the state (running, completed, failed, stopped), the stages, the counts and measures | the whole status, at most twice a second while it changes |
| `samples` | the latest 10 samples, and up to 20 spaced across the run | the whole set, when it changed |
| `failure` | one failed item | for the first 200; the status counts them all |
| `log` | a piece of what the run printed, libraries and child processes included | every second while there is output |
| `alive` | nothing | every 10 seconds |

A JetStream stream named after the subjects (`MILL`) keeps the messages.
The control center creates it; a run started while it does not exist is
refused. A run waits until the stream has each message. While NATS cannot
be reached it tries again for 60 seconds, so a restart of NATS does not end
the runs in progress.

A run is asked to stop with a request on
`<subjects>.control.<run id>.stop`, which its process answers before it
stops.

Text in a sample is cut at 2000 characters, and a whole sample at 20,000,
with the rest counted.

## Tests

`./run_tests.sh`. The build of the Mill extension runs them. `tests/test_nats.py`
uses the platform's NATS, with subjects and a stream of its own for each test.
