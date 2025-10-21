# Ansible Directory Structure

This directory contains Ansible-related files for thinkube-control template deployment.

## Directory Structure

```
ansible/
├── roles/              # Ansible roles that travel with source code
│   └── (template deployment roles)
└── inventory/          # SENSITIVE - Deployed by installer, NOT in source control
    └── inventory.yaml  # Generated at deployment time
```

## Important Notes

1. **Roles** (`ansible/roles/`):
   - These roles travel with the thinkube-control source code
   - Used by templates for deployment automation
   - Can be safely committed to version control

2. **Inventory** (`ansible/inventory/`):
   - **NEVER COMMIT TO VERSION CONTROL**
   - Contains sensitive installation-specific data
   - Deployed by the installer during thinkube-control setup
   - Listed in `.gitignore` to prevent accidental commits

## Usage

When thinkube-control executes template deployments:
1. Templates download their `ansible/deploy.yaml` playbook
2. The playbook uses roles from `/home/thinkube-control/ansible/roles/`
3. The inventory at `/home/thinkube-control/ansible/inventory/inventory.yaml` provides host information

## Security

The inventory file contains sensitive data such as:
- Host IP addresses
- SSH keys or credentials
- Domain names
- Service passwords

This is why it must never be included in source control and is generated/deployed separately by the installer.