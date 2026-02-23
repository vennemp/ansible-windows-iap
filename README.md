# Ansible Windows IAP

Custom Ansible connection plugin for managing Windows VMs on GCP that sit behind [Identity-Aware Proxy (IAP)](https://cloud.google.com/iap/docs/using-tcp-forwarding) with no public IP.

## Problem

Windows VMs behind IAP cannot use the SSH wrapper approach that works for Linux (there is no WinRM equivalent of `ssh_executable`). This project provides a `winrm_iap` connection plugin that manages an IAP TCP tunnel per host internally, so Ansible's WinRM transport works transparently.

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

## Requirements

- Python 3.10+
- `gcloud` CLI with IAP tunnel permissions
- GCP Application Default Credentials (`gcloud auth application-default login`)
- `google.cloud` and `ansible.windows` Ansible collections

## Project Structure

```
plugins/connection/winrm_iap.py     # Connection plugin (core)
library/gcp_reset_windows_password.py  # Module for password resets
scripts/reset-windows-password.sh    # Standalone credential bootstrap
inventory/inventory.gcp.yml         # Dynamic GCP compute inventory
group_vars/windows/connection.yml   # Shared WinRM/IAP settings
host_vars/<instance>/vault.yml      # Per-host vault-encrypted credentials
playbooks/win_ping.yml              # Connectivity test
playbooks/reset_password.yml        # Reset + vault-store credentials
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
ansible-galaxy collection install google.cloud ansible.windows
```

### 2. Configure inventory

Edit `inventory/inventory.gcp.yml` and replace the placeholder project ID with your GCP project(s):

```yaml
projects:
  - my-gcp-project-id
```

You can list multiple projects. The inventory automatically discovers all running
Windows VMs by checking the boot disk license for `windows-cloud`. These hosts are
placed in the `windows` group, which applies the connection settings from
`group_vars/windows/connection.yml`.

If you need to narrow the results further, add filters:

```yaml
filters:
  - status = RUNNING
  - 'name = my-specific-vm'
```

Verify the inventory works:

```bash
ansible-inventory --list
```

You should see your Windows VMs listed under the `windows` group with `gcp_project`,
`gcp_zone`, and `gcp_instance_name` vars populated.

The inventory also extracts the Windows Server version year from the boot disk license
(e.g. `2022`, `2025`) into a `windows_version` host var and creates keyed groups like
`windows_2025`. You can use these to target specific OS versions:

```bash
ansible-playbook playbooks/win_ping.yml --limit windows_2025
```

### 3. Create a vault password file

The vault password is used to encrypt and decrypt per-host credentials stored under
`host_vars/`. Create a `.vault_pass` file in the project root (it is git-ignored):

```bash
# Use a strong, random password
openssl rand -base64 32 > .vault_pass
chmod 600 .vault_pass
```

### 4. Configure Windows credentials

Before Ansible can connect to a Windows VM, it needs a username and password.
Choose the approach that matches your environment:

#### Option A: Domain-joined hosts (shared credential)

If your Windows hosts are domain-joined, you can use a single domain admin
credential for all hosts. Create a vault-encrypted file in `group_vars/windows/`:

```bash
ansible-vault create group_vars/windows/vault.yml
```

Add the domain credentials:

```yaml
ansible_user: DOMAIN\admin_username
ansible_password: your_domain_password
```

Save and close. The credentials will apply to all hosts in the `windows` group.
To edit later:

```bash
ansible-vault edit group_vars/windows/vault.yml
```

#### Option B: Standalone hosts (per-host credentials)

For hosts that are not domain-joined, use `gcloud compute reset-windows-password`
to generate a local account. The `reset_password.yml` playbook automates this and
stores the credentials in a vault-encrypted file at `host_vars/<instance>/vault.yml`.

Run it against the hosts you want to manage:

```bash
ansible-playbook playbooks/reset_password.yml --limit <hostname>
```

This will:

1. Call `gcloud compute reset-windows-password` to generate a new password
2. Write `ansible_user` and `ansible_password` to `host_vars/<hostname>/vault.yml`
3. Encrypt the file with `ansible-vault` using `.vault_pass`

You can also use the standalone script for a single host:

```bash
./scripts/reset-windows-password.sh \
  --instance <vm-name> \
  --zone <zone> \
  --project <project-id> \
  --vault-password-file .vault_pass
```

### 5. Test connectivity

```bash
# Verify the connection plugin is discovered
ansible-doc -t connection winrm_iap

# Test WinRM connectivity through IAP
ansible-playbook playbooks/win_ping.yml --limit <hostname>
```

A successful run looks like:

```
TASK [Ping Windows host] ****************************************************
ok: [my-win-vm]

TASK [Show result] ***********************************************************
ok: [my-win-vm] => {
    "ping_result": {
        "changed": false,
        "ping": "pong"
    }
}
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

## Notes

- **Cert validation** is automatically disabled by the plugin because the Windows cert is issued for the VM hostname, not `localhost`
- **Transport** is NTLM over HTTPS (port 5986) by default
- **macOS**: the plugin sets `NO_PROXY=localhost` to prevent a fork-safety crash in macOS's system proxy detection (`_scproxy`)
