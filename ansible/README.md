# Ansible Directory Structure

This directory holds Ansible playbooks and roles that thinkube-control runs
itself.

## Directory Structure

```
ansible/
├── playbooks/   # Build, sync and delete Jupyter venvs
└── roles/       # Roles on ANSIBLE_ROLES_PATH for these runs
```

## Important Notes

1. **Playbooks** (`ansible/playbooks/`):
   - The backend runs the venv playbooks from
     `/home/thinkube/thinkube-control/ansible/playbooks/`
     (`backend/app/api/jupyter_venvs.py`, `backend/app/api/nodes.py`,
     `backend/app/api/websocket_executor.py`)

2. **Roles** (`ansible/roles/`):
   - Set as `ANSIBLE_ROLES_PATH` for these runs
     (`get_roles_path` in `backend/app/services/ansible_environment.py`)
   - Optional component playbooks use the roles of the `thinkube`
     repository instead
   - Can be safely committed to version control

3. **Template deployment** does not use Ansible. It runs
   `scripts/deploy_application.py`.

4. **Inventory** is not in this directory:
   - Every run reads `/home/thinkube/.ansible/inventory/inventory.yaml`
     (`get_inventory_path` in `backend/app/services/ansible_environment.py`)
   - The code-server playbook copies it there from the installer's
     inventory (`ansible/40_thinkube/core/code-server/15_configure_environment.yaml`
     in the `thinkube` repository)
   - `ansible/inventory/` is listed in `.gitignore` to prevent accidental
     commits

## Security

The inventory file contains sensitive data such as:
- Host IP addresses
- SSH keys or credentials
- Domain names
- Service passwords

This is why it must never be included in source control.
