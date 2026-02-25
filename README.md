# Ansible Windows IAP

Custom Ansible connection plugin for managing Windows VMs on GCP that sit behind [Identity-Aware Proxy (IAP)](https://cloud.google.com/iap/docs/using-tcp-forwarding) with no public IP.

## Problem

Windows VMs behind IAP cannot use the SSH wrapper approach that works for Linux (there is no WinRM equivalent of `ssh_executable`). This collection provides a `winrm_iap` connection plugin that manages an IAP TCP tunnel per host internally, so Ansible's WinRM transport works transparently.

## How It Works

```
Ansible Controller                          GCP
┌──────────────┐    IAP Tunnel    ┌──────────────────┐
│  winrm_iap   │───localhost:N────│  IAP TCP Forward  │
│  plugin       │                 │                    │
│  (WinRM over  │                 │  ┌──────────────┐ │
│   localhost)  │                 │  │  Windows VM   │ │
│              │                 │  │  :5986 WinRM  │ │
└──────────────┘                 │  └──────────────┘ │
                                  └──────────────────┘
```

1. The plugin launches `gcloud compute start-iap-tunnel <instance> 5986 --local-host-port=localhost:0`
2. The OS assigns a random free port, which the plugin detects from gcloud's stderr
3. WinRM connects to `localhost:<port>` using NTLM over HTTPS
4. Each Ansible fork gets its own tunnel, so parallel execution works safely

## Installation

### From Ansible Galaxy

```bash
ansible-galaxy collection install vennemp.windows_iap
```

Or add to your `requirements.yml`:

```yaml
collections:
  - name: vennemp.windows_iap
    version: ">=1.0.0"
```

Then install:

```bash
ansible-galaxy collection install -r requirements.yml
```

The collection depends on `ansible.windows` and will install it automatically.

### From source (this repo)

```bash
git clone https://github.com/gcp-thearmory/ansible-windows-iap.git
cd ansible-windows-iap
ansible-galaxy collection build .
ansible-galaxy collection install vennemp-windows_iap-*.tar.gz
```

## Quick Start

### 1. Requirements

- Python 3.10+
- `gcloud` CLI with IAP tunnel permissions
- GCP Application Default Credentials (`gcloud auth application-default login`)

### 2. Configure your inventory

Use any inventory format. Each Windows host needs `gcp_project` and `gcp_zone`:

```yaml
# inventory/hosts.yml
all:
  children:
    windows:
      hosts:
        my-win-vm:
          gcp_project: my-gcp-project
          gcp_zone: us-east4-a
```

Or use the `google.cloud.gcp_compute` dynamic inventory plugin to discover VMs automatically (see [Dynamic Inventory](#dynamic-inventory) below).

### 3. Set connection variables

```yaml
# group_vars/windows.yml
ansible_connection: vennemp.windows_iap.winrm_iap
ansible_winrm_transport:
  - ntlm
ansible_winrm_scheme: https
ansible_winrm_port: 5986
ansible_winrm_server_cert_validation: ignore
ansible_shell_type: powershell

ansible_user: my_admin_user
ansible_password: "{{ vault_windows_password }}"
```

### 4. Test connectivity

```bash
ansible windows -m ansible.windows.win_ping -i inventory/hosts.yml
```

## Connection Plugin Options

| Option | Variable | Required | Default | Description |
|--------|----------|----------|---------|-------------|
| `gcp_instance_name` | `ansible_gcp_instance_name` | No | `inventory_hostname` | GCP instance name |
| `gcp_project` | `ansible_gcp_project` / `gcp_project` | Yes | - | GCP project ID |
| `gcp_zone` | `ansible_gcp_zone` / `gcp_zone` | Yes | - | GCP zone |
| `gcp_iap_service_account` | `ansible_gcp_iap_service_account` | No | - | SA for impersonation |
| `iap_tunnel_timeout` | `ansible_iap_tunnel_timeout` | No | `30` | Tunnel ready timeout (seconds) |

All standard `ansible_winrm_*` options are also supported (inherited from the built-in `winrm` plugin).

## Included Content

### Connection Plugins

| Name | Description |
|------|-------------|
| `vennemp.windows_iap.winrm_iap` | WinRM connection through a GCP IAP tunnel |

### Modules

| Name | Description |
|------|-------------|
| `vennemp.windows_iap.gcp_reset_windows_password` | Reset a Windows VM password via `gcloud` and optionally vault-encrypt credentials |

## Dynamic Inventory

You can use the `google.cloud.gcp_compute` inventory plugin to discover Windows VMs automatically:

```yaml
# inventory/gcp.yml
plugin: gcp_compute
projects:
  - my-gcp-project
auth_kind: application
filters:
  - status = RUNNING
hostnames:
  - name
groups:
  windows: >-
    disks | map(attribute='licenses', default=[])
    | flatten | select('search', 'windows-cloud')
    | list | length > 0
compose:
  ansible_host: name
  gcp_instance_name: name
  gcp_project: project
  gcp_zone: zone
```

This requires the `google.cloud` collection:

```bash
ansible-galaxy collection install google.cloud
```

## Notes

- **Cert validation** is automatically disabled by the plugin because the Windows cert is issued for the VM hostname, not `localhost`
- **Transport** is NTLM over HTTPS (port 5986) by default
- **macOS**: the plugin sets `NO_PROXY=localhost` to prevent a fork-safety crash in macOS's system proxy detection (`_scproxy`)

## Development

The repo includes operational files (inventory, playbooks, vault configs) alongside the collection source. These are excluded from the published collection via `build_ignore` in `galaxy.yml`.

### Project Structure

```
galaxy.yml                              # Collection metadata
meta/runtime.yml                        # Ansible version requirements
plugins/
  connection/winrm_iap.py              # Connection plugin
  modules/gcp_reset_windows_password.py # Password reset module
ansible.cfg                             # Local development config
inventory/                              # Dynamic GCP inventory configs
group_vars/                             # Shared connection settings
host_vars/                              # Per-host vault-encrypted credentials
playbooks/                              # Operational playbooks
scripts/                                # Standalone helper scripts
```

### Building locally

```bash
ansible-galaxy collection build .
# produces vennemp-windows_iap-1.0.0.tar.gz
```

### Running playbooks from this repo

The `ansible.cfg` sets up local plugin paths so you can use short names without installing the collection:

```bash
# Test connectivity
ansible-playbook playbooks/win_ping.yml --limit <hostname>

# Reset a Windows password
ansible-playbook playbooks/reset_password.yml --limit <hostname>
```

## License

Apache-2.0
