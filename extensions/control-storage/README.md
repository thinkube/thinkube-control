# Thinkube Storage

The Storage page of Thinkube Control: the shared folders and their quotas,
the disk under them, each node's disks and every volume claim.

Storage is an extension of Thinkube Control, deployed as `control-storage`:
Control lists it in its navigation and draws its page. The name `storage`
is not used because `storage.<domain>` is the SeaweedFS UI.

## What it does

- **Shared folders**: the top-level folders of the two JuiceFS file systems
  (`thinkube-shared`, `mlflow`) with their size, quota and the volume claims
  that mount them; the entries of a folder, largest first; setting a quota;
  deleting a file or folder inside a top-level folder
  (`backend/app/services/storage.py`, with the `juicefs` tool the backend
  image installs, `backend/scripts/install_juicefs.py`).
- **Disk under the shared folders**: the disks of the SeaweedFS volume
  servers, which hold the JuiceFS data.
- **Node disks**: each node's mounted disks and its training folder, as the
  node-metrics pod of Thinkube Control reads them.
- **Volume claims**: every claim with its size and, where the kubelet
  measures it, the space it uses.

The API is `backend/app/api/storage.py`, under `/api/v1/storage`. The page
is `frontend/src/pages/StoragePage.tsx`.

## What it needs from the cluster

`thinkube.yaml` declares it on the backend container:

- `filesystems: [thinkube-shared, mlflow]` mounts both file systems at
  their root, at `/storage/<name>`. A delete there reaches every folder.
- `access` lists the Kubernetes rules of the backend's account: reading the
  JuiceFS secrets, listing and patching volumes (the JuiceFS driver sets a
  folder's quota from its volume's capacity at every mount, so a quota
  change sets that too), listing claims, pods and nodes, and reading each
  node's kubelet statistics.

## Agents

The MCP server is at `https://control-storage.<domain>/api/v1/mcp`
(`backend/app/mcp_server.py`). Its tools are `storage_get`,
`storage_list_folder`, `storage_get_nodes`, `storage_get_claims`,
`storage_set_quota` and `storage_delete_path`. A Thinkube Control API token
signs in.

## Change it

See `CLAUDE.md`: the source is this folder of the thinkube-control
repository, and a deploy of the folder runs the tests.

## License

MIT - See [LICENSE](LICENSE)
