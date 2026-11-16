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

A run publishes what it does on NATS: its start, its status (stages, counts
and measures), its samples, its failed items, its log and a heartbeat. A
JetStream stream keeps the messages. The control center reads the stream
and keeps each run's record in its database. A run needs only the network
to report, and it works the same in the main pod, in the IDE and in a Forge
run on any node. The control center asks a run to stop with a request on
NATS that the run's process answers.

The first version wrote the record as files on the shared volume, which the
control center read. NATS replaced it before step 5: it stops a run on any
node, it tells when a run's process died (the heartbeats stop), and it does
not rewrite small files on the shared file system twice a second.

The library keeps samples small: the latest few items, one item every so
often across the run, and the items that failed. The full output goes where
the task declared it.

For frameworks that draw their own tqdm bars, the library supplies a tqdm
class that reports to the control center.

Mill needs the optional component NATS with JetStream. The playbook of
thinkube deployed NATS with JetStream off; it is fixed
(`optional/nats/10_deploy.yaml`).

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
5. **The generic run in Forge.** A run that must wait its turn or execute
   on a chosen node goes through Forge's queue of that node, with or
   without a GPU. Mill has no queue of its own; its main pod keeps only
   quick tries.
   Done when: a script trains on a chosen node, and its bars are seen in
   the control center.

   Forge starts the pod of a task run itself, as a Kubernetes job: on
   the run's node, with its GPUs and memory, on an AI base image of the
   node's architecture, with the shared file system mounted (the tasks
   repository, the datasets and the models). It does not use JupyterHub,
   notebook servers or notebook jobs. Forge's own fine-tunes keep their
   notebook jobs until they move to the same kind of pod (see "Not in this
   plan").

   In parts, each seen before the next:

   1. `thinkube-mill` is in the platform's package index. Done: the
      playbook `thinkube-control/16_publish_packages.yaml` of thinkube
      publishes it from this folder; the index has 0.1.0.
   2. A pod that Forge can start runs a task: the AI base image of the
      node's architecture with `thinkube-mill` installed, the GPUs asked
      for, and the shared file system mounted where the main pod has it,
      so a path means the same in both.
      Seen when: such a pod, started by hand on a GPU node, runs the
      sample task, and the run appears in the control center with that
      node's name.
   3. Forge has the run kind "task": a file of the tasks repository with
      its inputs, outputs and settings, a node, GPUs, memory, a priority
      and a Python environment. It skips preprocessing and the plan, waits
      in the node's queue like a fine-tune, and has no checkpoint: when
      higher work needs the node it is stopped, and starts again from the
      beginning when it gets the node back.
      Seen when: two task runs queued on one node execute one after the
      other, and a third on another node executes at the same time.
   4. Mill's start takes a node: with one, it creates the Forge run; with
      none, it runs the task in the main pod, which is for quick tries. The
      Runs page shows the waiting runs of each node, with what they wait
      for.
      Seen when: an agent starts a task on a node through Mill, and stops
      it there.
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

Steps 2 and 3 are done, seen on the development cluster:

- The library is `backend/library`, with its tests. It lives in the
  backend's folder because the backend's image installs it and the
  backend's build runs its tests. Publishing it to the platform's package
  index and installing it in the AI base image belong to step 5, where a
  pod other than the main pod first needs it.
- The main pod is the extension's backend, on the platform's `python-base`
  image, which has pandas and httpx. A library a task needs is added to
  `backend/requirements.txt`.
- The control center is the extension's frontend and `backend/app/api/runs.py`.
- Seen: the sample task `demo/clean.py` of the tasks repository, started
  through the API, ran in the main pod over 400 rows. The Runs page showed
  its bar advance with the time left; the run's page showed its totals,
  failed items, samples and log; the output file belongs to the IDE user.

The change to NATS is done, seen on the development cluster: a run of the
sample task started through the API showed its bar and totals on the run's
page; a second one was stopped through the API and ended as stopped; a
second request to stop it was refused with the reason.

Step 4 is half done:

- The backend's MCP server answers at `/api/v1/mcp`, and the role
  `agent_clients` of thinkube lists it.
- Left: run `code-server/15_configure_environment.yaml`, so the IDE's
  agents get the tools, and start a run with them from a new session.

Step 5 is started: its part 1 is done. Next: its part 2.

Open point, to settle in step 5 or 6: a task reads the platform's datasets
and models at paths that differ between the IDE, the main pod and a
notebook server. Working data under `data/` of the tasks repository has the
same path everywhere.
