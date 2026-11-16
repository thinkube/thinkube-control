# Thinkube Mill: plan

Mill is a component for data transformation work: calling a model over
items, cleaning a dataset, scoring results. It replaces notebooks for that
work. This file is the plan agreed before anything was built. A step is
done when the thing it names was seen running.

## Why

Work done in a notebook has three problems:

- It is hard to see what is running and how far it is.
- The work stays as cells in one kernel. It is not a step that a workflow
  can use later.
- The state is hidden in the kernel's memory.

## The design

- **One tasks repository per user.** A normal git repository on the user's
  GitHub account. A folder for each piece of work, and a common folder for
  shared code. It holds scripts, small configuration files and notes. It
  does not hold data or models.
- **A shared volume.** The repository lives on shared storage (JuiceFS), so
  the IDE and a pod on any node mount the same files.
- **A task.** A script in the repository that follows one standard:
  - it declares its inputs, its outputs and its settings;
  - it reports progress and samples of processed data through a small
    library, `thinkube-mill`;
  - it reads and writes data only through its declared inputs and outputs,
    so tasks can be chained.
- **The main pod.** One pod with the AI base image and the repository
  mounted. It needs no GPU. It runs light tasks: calling models through the
  LLM backends, cleaning data, scoring.
- **The control center.** A page in Thinkube Control, served by the Mill
  extension. It lists every run, wherever it executes. Each run shows its
  state, a progress bar for each stage, live samples (input and output side
  by side), running totals, failures, the log, and the commit it ran from.
  Runs are started and stopped there.
- **GPU work goes through Forge.** Forge gets a new kind of run: a command
  from the tasks repository at a commit, with an image, a node, GPUs,
  memory and a priority. Forge schedules it with its existing queue,
  priorities, training mode and slots. It makes no assumption about the
  technology, so a new training method runs with no change to Forge.
- **A script that does not follow the standard still runs.** It shows its
  state and its log, with no progress bar.

## How progress reaches the control center

The library writes small progress and sample records to the shared volume.
The control center reads them. A task needs no network setup, and it works
the same in the main pod and in a Forge run on any node.

The library keeps samples small: the latest few items, one item every so
often across the run, and the items that failed. The full output goes where
the task declared it.

For frameworks that draw their own tqdm bars, the library supplies a tqdm
class that reports to the control center.

## Steps

1. **The repository and its volume.** A new shared volume, mounted in the
   IDE, holding the user's tasks repository.
   Done when: a file edited in the IDE is seen by a pod on another node.
2. **The task standard and the library.** The `thinkube-mill` Python
   package: the declaration of inputs, outputs and settings; `track(...)`
   for progress; `show(...)` for samples; the tqdm class. Published to the
   platform's package index and installed in the AI base image.
   Done when: a sample task writes correct records.
3. **The main pod and the control center.** The Mill extension of Thinkube
   Control: the main pod and the page.
   Done when: a task is started from the page, and its bars and samples
   are seen there.
4. **Tools for agents.** MCP tools to start a run, stop it, and read its
   progress and samples.
   Done when: an agent starts a run and it appears on the page.
5. **The generic run in Forge.** Forge starts a pod for it directly.
   Done when: a script trains on a chosen node, and its bars are seen in
   the control center.
6. **A first real case.** `kubexlat/notes-kftt` as standard tasks: corpus,
   label, resolve, score.
   Done when: the four tasks run one after another, each reading the output
   of the one before.
7. **Tasks as workflow steps.** An Argo workflow runs the four tasks of
   step 6 as a chain, at a commit.
   Done when: the workflow finishes and its runs appear in the control
   center.

## Not in this plan

- Forge's own fine-tunes keep running as notebook jobs. Moving them to pods
  is a separate change, made when someone asks for it.
- Notebooks are not removed.

## To check before step 3

- Whether an extension's `thinkube.yaml` can use the AI base image and
  mount a shared volume. If it cannot, the platform needs that first.

## Progress

Step 1 is done, seen on the development cluster:

- The tasks repository is `<GITHUB_USERNAME>/mill-tasks` on GitHub,
  private.
- The playbook `jupyterhub/16_configure_code_server_storage.yaml` of
  thinkube mounts the folder `mill` of the shared file system in the IDE at
  `~/thinkube-ai/mill`, owned by the IDE user.
- The repository is in that folder. `git clone` into it is refused, because
  the shared file system shows its own files at the root of a mount
  (`.accesslog`, `.config`, `.stats`); `git init` with the remote added
  works, and `.gitignore` names those files.
- A pod on another node (the Storage extension's backend, on `tkspark`;
  the IDE is on `tkamd1`) reads the same files at
  `/storage/thinkube-shared/mill`.

Found while preparing step 3: an extension can already mount the shared
file system (`filesystems: thinkube-shared` in `thinkube.yaml`, at
`/storage/thinkube-shared`), so the Mill pod reads the tasks repository at
`/storage/thinkube-shared/mill`.

Next: step 2.
