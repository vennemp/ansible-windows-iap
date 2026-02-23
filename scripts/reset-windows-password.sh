#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Reset a Windows VM password via gcloud and store vault-encrypted credentials.

Required:
  --instance NAME          GCP instance name
  --zone ZONE              GCP zone (e.g., us-east4-a)
  --project PROJECT        GCP project ID

Optional:
  --user USERNAME          Windows username (default: ansible_admin)
  --vault-password-file F  Path to vault password file (default: .vault_pass)
  --host-vars-dir DIR      Base directory for host_vars (default: host_vars)
  -h, --help               Show this help message
EOF
    exit "${1:-0}"
}

INSTANCE=""
ZONE=""
PROJECT=""
USER="ansible_admin"
VAULT_PASSWORD_FILE=".vault_pass"
HOST_VARS_DIR="host_vars"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance)     INSTANCE="$2"; shift 2 ;;
        --zone)         ZONE="$2"; shift 2 ;;
        --project)      PROJECT="$2"; shift 2 ;;
        --user)         USER="$2"; shift 2 ;;
        --vault-password-file) VAULT_PASSWORD_FILE="$2"; shift 2 ;;
        --host-vars-dir) HOST_VARS_DIR="$2"; shift 2 ;;
        -h|--help)      usage 0 ;;
        *)              echo "Unknown option: $1" >&2; usage 1 ;;
    esac
done

if [[ -z "$INSTANCE" || -z "$ZONE" || -z "$PROJECT" ]]; then
    echo "Error: --instance, --zone, and --project are required." >&2
    usage 1
fi

if [[ ! -f "$VAULT_PASSWORD_FILE" ]]; then
    echo "Error: Vault password file not found: $VAULT_PASSWORD_FILE" >&2
    exit 1
fi

echo "Resetting password for $USER on $INSTANCE ($PROJECT / $ZONE)..."

RESULT=$(gcloud compute reset-windows-password "$INSTANCE" \
    --user "$USER" \
    --zone "$ZONE" \
    --project "$PROJECT" \
    --format json \
    --quiet)

USERNAME=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['username'])")
PASSWORD=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
IP=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ip_address',''))")

echo "Username: $USERNAME"
echo "IP Address: $IP"

HOST_DIR="$HOST_VARS_DIR/$INSTANCE"
mkdir -p "$HOST_DIR"

VAULT_FILE="$HOST_DIR/vault.yml"

cat > "$VAULT_FILE" <<YAML
ansible_user: $USERNAME
ansible_password: $PASSWORD
YAML

ansible-vault encrypt "$VAULT_FILE" --vault-password-file "$VAULT_PASSWORD_FILE"

echo "Credentials written and encrypted: $VAULT_FILE"
echo "Verify with: ansible-vault view $VAULT_FILE --vault-password-file $VAULT_PASSWORD_FILE"
