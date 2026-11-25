# Thinkube Scheduler: plan

The scheduler is the platform's service for running work on the nodes. It
decides whose turn it is on a node and on which GPU, and it runs the work in
a pod. This file is the plan agreed before anything was built. A step is done
when the thing it names was seen running.

## Why

The platform has a service for each kind of work:

- Mill: data transformations.
- Forge: trainings.
- The LLM gateway: running models.

All of them use the same nodes and GPUs. Deciding who gets a node was never a
service of its own, so it was put where it was first needed:

- Forge holds the queue of each node, the priorities and the slots. Because
  of that, Mill's transformations run as Forge runs, and Forge starts Mill's
  pods.
- Thinkube Control holds training mode, the GPU slices of each node and the
  notebook jobs.
- A training runs in a JupyterHub pod, started as a notebook job, although
  the notebook only starts a command.
- The gateway loads a model by rules of its own.

The rules were made for the DGX Spark, where everything shares one memory.
On a node with separate cards they reserve the whole node for work that uses
one card.

## The design

- **A job.** What the scheduler is given: a command, a node, the GPUs and
  the GPU memory it needs, its memory and CPU, its Python environment, the
  folders it mounts, a priority, and whether it can stop and go on later (a
  training with checkpoints) or starts again from the beginning (a
  transformation).
- **The scheduler does not know what the work is.** It knows no plan, no
  milestone and no dataset.
- **What each service keeps.** Forge: the dataset, the plan, the training,
  the milestones, registering the model. Mill: the tasks, the runs, their
  progress. The gateway: the models and their servers. JupyterHub: the
  notebooks a person opens.
- **Everything that uses a GPU asks the scheduler for it.** A training, a
  transformation, a served model, a notebook server and an app. A service
  that starts its own pods (the gateway, JupyterHub) asks for the GPUs and
  starts the pod on the ones it was given.

## Priorities

A priority is a number from 1 (highest) to 5. Work of a higher priority takes
from work of a lower one; work of the same priority waits its turn. The table
of today stays: notebook server 1, a run 3 unless it says otherwise, served
model 4, app 5. Four things change:

1. **Everyone asks, by the same rule.** Today only a Forge run reads the
   table; a model or a notebook server takes what is free.
2. **Work conflicts only over what it shares.** A node has a type. On a node
   with separate cards, two pieces of work conflict when they need the same
   card. On a node whose GPU shares the system's memory (the DGX Spark), they
   conflict when their memory together is more than the node's. Reserving a
   whole node for one training is what the scheduler does where memory is
   shared, not a rule for every node.
3. **A model has the priority of the work that uses it.** A job names the
   models it calls; while it runs, they hold at least its priority.
4. **A node's limits are not priorities.** "One GPU training on this node"
   is a limit of heat. The scheduler keeps such limits for each node.

A job is the scheduler's, with the priority its owner gives it: "notebook
job" is no longer a kind of work.

## How a pod is given a GPU

The scheduler names the GPU to the pod, by its id. Kubernetes's own count of
GPUs is not used.

Seen on 24 November 2026, on tkamd2 (two RTX 3090) and tkspark (GB10): a pod
that asks Kubernetes for no GPU, with the runtime class `nvidia-legacy` and
`NVIDIA_VISIBLE_DEVICES` set to a GPU's id, sees that GPU alone and computes
on it (PyTorch from the meepsquared venv). With the runtime classes `nvidia`
and `nvidia-cdi` a GPU's id is refused ("unresolvable CDI devices"); they
would need a device list on the node that names each GPU by its id.

What follows from it:

- Two pods share a GPU by being given the same one. How many share it is the
  scheduler's decision, by the GPU memory each job needs; a GPU does not keep
  one job's memory from another's, so the scheduler keeps a margin, and a job
  that must not be disturbed gets its GPUs alone.
- Time slicing does nothing for the scheduler's pods. It stays while any pod
  still asks Kubernetes for a GPU, and goes with the last of them (step 9).
- Kubernetes does not count the scheduler's pods. Until step 9 the scheduler
  reads what each GPU really holds, to see the work it did not start.

## Steps

Each step is seen before the next begins.

1. **The scheduler knows the nodes.** An extension of Thinkube Control,
   `scheduler`. It lists each node's GPUs by id, with their memory and what
   they hold now, and the node's type.
   Seen when: its page lists the two cards of tkamd2 and the GPU of tkspark.
   Done, 25 November: the route lists tkamd2's two cards and tkspark's GPU by
   id, with what each holds. Thinkube Control's node-metrics pods give each
   GPU's id for it.
2. **It runs a job.** Given a job, it starts the pod on the GPUs it chose,
   says the job's state, and stops it when asked. The pods run in a namespace
   of the scheduler's, which holds the claims of the shared file systems.
   Seen when: two jobs asked for one card each run at once on the two cards
   of tkamd2, and a third waits.
   Seen, 25 November: a job computed on tkspark's GPU, given by its id; a
   job without a GPU ran on tkamd1 and saw none; of two jobs that each asked
   for tkspark's GPU alone, the second waited for the first; three jobs that
   asked for a card of tkamd2 alone waited, saying what each card held of
   work the scheduler did not start (a training on both cards).
   Seen, 25 November, on tkamd2, once that training had ended: of three
   jobs that each took 20 GiB of a card, two ran at once, one on each card
   (the one on card 0 beside a served model's hold), and the third waited,
   saying what each card had free.
3. **It decides the turn.** The queue of each node, the priorities and the
   four changes above, with the work it did not start read from the GPUs.
   Seen when: a job of a higher priority stops one of a lower priority on the
   card they share, and a job on the other card keeps running.
   Seen, 25 November, on tkspark: a job of priority 2 that asked for the GPU
   alone stopped a running job of priority 4; the stopped job waited again,
   and ran a second time when the first had ended. A node's limit of GPUs at
   work, and the order on a node, are in the tests.
   Seen, 25 November, on tkamd2: with a job of priority 5 on each card, a
   job of priority 4 that needed 20 GiB of a card stopped the one on card 1
   and ran there; the job on card 0 and the served model beside it kept
   running, and the stopped job ran a second time when the other had ended.
4. **Forge's trainings run as jobs.** Preprocessing, the plan, the training
   and the registration: each a command in a job. Forge keeps no queue for
   them and starts no notebook job.
   Seen when: a training planned and trained through the scheduler registers
   its model.
   Built so, 25 November: Forge's script runs as a process in the job's pod,
   in the Python environment it names, with its parameters in
   FORGE_PARAMETERS; a training is resumable. Forge no longer turns training
   mode on and no longer reads the work outside it: it keeps two of its own
   runs off one node at once and pauses its own lower runs with their stop
   file, and the scheduler weighs everything else. A job's pod requests half
   its CPU cores and all its memory, and runs as `scheduler-job`, which may
   read the key of MLflow's storage to upload a registered model.
   Seen, 25 November: a LoRA run of Qwen3 0.6B on tkspark was preprocessed on
   tkamd2 without a GPU, planned from earlier measurements there, measured
   again on tkspark's GPU, trained 20 steps there and registered its model,
   every part a job of the scheduler and none a notebook job. The test model
   was deleted after.
   Seen, 25 November, on tkamd2: a choice run of Gemma 4 12B on both cards,
   planned from earlier measurements and training at 2.7 s a step, its
   pod the scheduler's; the embedding model on card 1 was unloaded for it.
   Also in this step: the table of priorities moved here from Forge. A person
   sets the priority of each kind of work on the Priorities page, and each
   owner reads its kind's when it gives work: JupyterHub for notebook servers
   and unattended runs, Forge for a run given none, Mill for its tasks, the
   gateway for served models, Control for apps.
5. **Mill's transformations run as jobs of Mill.** Mill gives them to the
   scheduler itself. Forge loses the run kind "task", the code that starts
   Mill's pods, and its CPU lane.
   Seen when: a transformation started in Mill runs on a node with no Forge
   run made for it.
6. **The gateway asks for a model's GPUs.** It starts the model's pod on the
   GPUs it was given. Training mode is no longer turned on by Forge.
   Seen when: a model loads on the card a training does not use, while the
   training runs.
   This step was made before steps 4 and 5, as the person decided on 25
   November: while trainings still run as before, a model the scheduler knows
   is never in a training's way, and a training that moved first would have
   waited for every model it could not see.
   Done, 25 November: holds, GPUs the scheduler keeps for work whose pod its
   owner starts, with the owner asked to give them up for work of a higher
   priority; and the gateway's pods of one model on them. Seen on tkspark: a
   model's pod started on the GPU its hold was given, named by its id and
   asking Kubernetes for none; a job of priority 2 that needed the GPU alone
   had the model unloaded, ran, and the model was loaded again by itself.
   The gateway's pods that serve several models (the trained heads, Ollama)
   have one hold each, for the memory of the models on them: it grows before
   a model is loaded, when the scheduler lets it, and shrinks as models
   leave. Asked to give its GPU up, such a pod is stopped and its models are
   loaded again when it has a GPU.
   Seen on tkspark, 25 November: the Ollama pod started on the GPU its hold
   was given; a job of priority 2 that needed the GPU alone had the pod
   stopped, ran, and the pod's model was loaded again by itself, the pod
   still on its hold. The trained heads' pod on tkspark was running before
   with a GPU asked of Kubernetes, and stays so until it is made again.
   Seen, 25 November, on tkamd2: with a job of priority 3 that had card 0
   alone, in a training's place, a model loaded on card 1 and was served
   there. The served model that was on card 0 was unloaded for the job, and
   was loaded again by itself on card 1 when that card was free.
   Open in this step: a server that takes a share of a GPU's memory whatever
   the model needs (vLLM, SGLang, Flare) is held by the gateway's estimate of
   the model, and counted for the rest by what the GPU reports once it has
   loaded. A backend type could say that it takes a share, so that its hold
   says the share from the start.
   Open in this step: the gateway still refuses a load by its own count of
   GPU slots, two models on a card of tkamd2. That count goes with time
   slicing (step 9).
   Open in this step: the scheduler counts the work it did not start on a
   GPU as what the GPU reports in use beyond what its own jobs were given. A
   job of its own that uses less than it was given hides that much of other
   work on the same GPU. The node-metrics pods could report the memory each
   pod holds on a GPU, which would count both exactly.
7. **Notebook servers ask.** JupyterHub writes a server's pod, for the form
   in the browser and for the starts Thinkube Control makes, so JupyterHub
   asks: its start hook asks the scheduler for a hold, waits for its GPUs,
   and starts the pod on them. The hold ends when the server stops. A server
   asks for a number of the node's GPUs and may say the memory it takes on
   each, with which it shares the GPU with served models; without it the
   server has its GPUs alone. An interactive server asks with priority 1, an
   unattended run with priority 2. A start that is given no GPU fails with
   the scheduler's reason.
   A hold says where its pod is, and the pod carries the hold's id as a
   label: the scheduler ends a hold that has its GPUs and no pod for 10
   minutes, so a server that is gone leaves no hold of priority 1 on a GPU.
   Seen when: a notebook server gets a GPU through the scheduler.
   Seen, 25 November, on tkspark: a notebook server started with 1 GPU and
   8 GB of it got a hold of priority 1; its pod asked Kubernetes for no GPU,
   ran with the GB10's id, and nvidia-smi in it showed the GB10. The hold
   ended when the server stopped.
   Open in this step: the priority of each kind of work (notebook server 1,
   notebook job 2, run 3, served model 4, app 5) is a table a person edits
   in Forge, and the owners that ask the scheduler today say the default
   numbers themselves. The table moves to the scheduler with step 4.
8. **Apps that use a GPU ask.** Decided on 25 November: an app asks, like
   everything else; the scheduler does not read what Kubernetes gave. An app
   says the GPUs it needs in its `thinkube.yaml` (`gpu: count, memory`), and
   Thinkube Control writes its manifests from that, so the change is made
   once, in Thinkube Control, and the templates stay as they are.
   Built so: when the manifests are rendered, a container with `gpu` is
   pinned to the node whose GPUs hold it with the most memory free (the node
   it is on already while it still fits). Its pod asks Kubernetes for no GPU,
   runs with `nvidia-legacy`, and reads `NVIDIA_VISIBLE_DEVICES` from the
   ConfigMap `<deployment>-gpus`, which Control writes. A loop in Control
   asks for a hold for each such Deployment that has replicas (priority 5),
   writes the GPU ids once the hold has them, and the pod starts. Asked for
   the GPUs, Control takes the ids out and deletes the pods, which wait
   without starting, and releases the hold once none runs. Replicas are
   never changed, so an Argo CD sync of the app undoes nothing. A
   component's containers keep asking Kubernetes: the gateway starts their
   pods.
   Seen when: an app with a GPU runs on the GPU its hold was given, stops
   for a job of a higher priority, and comes back when the job has ended.
   Seen, 25 November, on tkspark, with a test app that took 2 GiB of a GPU:
   its manifests pinned it to tkspark, Control's hold of priority 5 was
   given the GB10, and the app computed there, asking Kubernetes for no GPU.
   A job of priority 2 that needed the GPU alone had the app's pod stopped;
   the pod waited without starting while the job ran, and the app came back
   by itself on the GPU when the job had ended. The app was deleted after.
9. **Kubernetes hands out no GPUs.** Time slicing is turned off and the GPU
   plugin offers none on the nodes, so a pod that goes around the scheduler
   does not start.
   To check first: that the driver and the runtime keep working without the
   plugin's count.
   To change with it: Thinkube Control counts a node's GPUs from what
   Kubernetes offers, divided by the slices (`cluster_resources`), for the
   notebook servers' choices among others; it counts them from the GPUs the
   node reports instead.

## Not in this plan

- Notebooks are not removed.
- A node's pods are not moved between nodes by the scheduler: a job names its
  node.
