# Ansible Windows IAP

Custom Ansible connection plugin for managing Windows VMs on GCP behind Identity-Aware Proxy (IAP).

## Project Structure

- `plugins/connection/winrm_iap.py` - Custom connection plugin subclassing the built-in `winrm` plugin
- `library/gcp_reset_windows_password.py` - Custom module for resetting Windows passwords via `gcloud`
- `scripts/reset-windows-password.sh` - Standalone credential bootstrap script
- `inventory/` - Dynamic GCP compute inventory files per environment (`*.gcp.yml`)
- `group_vars/windows/connection.yml` - Shared WinRM/IAP connection settings
- `host_vars/<instance>/vault.yml` - Per-host vault-encrypted credentials
- `playbooks/` - Operational playbooks

## Key Commands

```bash
# Always cd into this directory before running commands
cd ~/github/ansible-windows-iap

# Verify plugin discovery
ansible-doc -t connection winrm_iap

# Check inventory (requires GCP Application Default Credentials)
ansible-inventory -i inventory/staging.gcp.yml --list

# Bootstrap credentials for a host
./scripts/reset-windows-password.sh \
  --instance <vm> --zone <zone> --project <project> \
  --vault-password-file .vault_pass

# Test connectivity (always staging first)
ansible-playbook playbooks/win_ping.yml -i inventory/staging.gcp.yml --limit <host>
```

## Environment

- **Staging project:** `armory-ripcord-staging`
- **Prod project:** `armory-ripcord-prod`
- Vault password file: `.vault_pass` (git-ignored)

## Connection Plugin Notes

- Uses `gcloud compute start-iap-tunnel` to create a local port forward per host
- Port `0` lets the OS pick a free port, safe for parallel forks
- WinRM cert validation must be disabled (cert is for the Windows hostname, not `localhost`)
- Transport: NTLM over HTTPS (port 5986)

## GITLAB_PAT_CMD

```bash
export GITLAB_PAT=$(gcloud secrets versions access latest --secret=gitlab-pat --project=armory-gss-prod --impersonate-service-account=gitlab-iap-api-access@armory-gss-prod.iam.gserviceaccount.com)
```
