#!/usr/bin/python
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r"""
---
module: gcp_reset_windows_password
short_description: Reset a Windows VM password via gcloud and optionally vault-encrypt credentials
description:
    - Runs C(gcloud compute reset-windows-password) to set a new password for a
      Windows VM on GCP.
    - Optionally writes the credentials to an Ansible vault-encrypted file
      under C(host_vars/<instance>/vault.yml).
    - This module runs on C(localhost) (delegate_to: localhost).
options:
    instance_name:
        description: The GCP instance name.
        required: true
        type: str
    project:
        description: The GCP project ID.
        required: true
        type: str
    zone:
        description: The GCP zone of the instance.
        required: true
        type: str
    user:
        description: The Windows username to reset the password for.
        default: ansible_admin
        type: str
    vault_encrypt:
        description: Whether to write vault-encrypted credentials to host_vars.
        default: false
        type: bool
    vault_password_file:
        description:
            - Path to the vault password file.
            - Required when O(vault_encrypt) is V(true).
        type: str
    host_vars_dir:
        description: Base directory for host_vars output.
        default: host_vars
        type: str
author:
    - Matthew Venne
"""

EXAMPLES = r"""
- name: Reset Windows password and vault-encrypt
  gcp_reset_windows_password:
    instance_name: ripcord-staging-win-01
    project: armory-ripcord-staging
    zone: us-east4-a
    user: ansible_admin
    vault_encrypt: true
    vault_password_file: .vault_pass
  delegate_to: localhost
  register: reset_result
"""

RETURN = r"""
username:
    description: The Windows username.
    returned: success
    type: str
password:
    description: The new password (no_log).
    returned: success
    type: str
ip_address:
    description: The internal IP address of the instance.
    returned: success
    type: str
vault_file:
    description: Path to the vault-encrypted credentials file.
    returned: when vault_encrypt is true
    type: str
"""

import json
import os
import subprocess
import tempfile

from ansible.module_utils.basic import AnsibleModule


def run_module():
    module_args = dict(
        instance_name=dict(type='str', required=True),
        project=dict(type='str', required=True),
        zone=dict(type='str', required=True),
        user=dict(type='str', default='ansible_admin'),
        vault_encrypt=dict(type='bool', default=False),
        vault_password_file=dict(type='str', default=None),
        host_vars_dir=dict(type='str', default='host_vars'),
    )

    module = AnsibleModule(
        argument_spec=module_args,
        supports_check_mode=False,
    )

    instance = module.params['instance_name']
    project = module.params['project']
    zone = module.params['zone']
    user = module.params['user']
    vault_encrypt = module.params['vault_encrypt']
    vault_password_file = module.params['vault_password_file']
    host_vars_dir = module.params['host_vars_dir']

    if vault_encrypt and not vault_password_file:
        module.fail_json(msg="vault_password_file is required when vault_encrypt is true")

    if vault_password_file:
        vault_password_file = os.path.abspath(os.path.expanduser(vault_password_file))

    cmd = [
        'gcloud', 'compute', 'reset-windows-password',
        instance,
        '--user', user,
        '--zone', zone,
        '--project', project,
        '--format', 'json',
        '--quiet',
    ]

    rc, stdout, stderr = module.run_command(cmd)
    if rc != 0:
        module.fail_json(
            msg="gcloud reset-windows-password failed (rc=%d): %s" % (rc, stderr),
            cmd=' '.join(cmd),
        )

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        module.fail_json(msg="Failed to parse gcloud output: %s" % stdout)

    username = result.get('username', user)
    password = result.get('password', '')
    ip_address = result.get('ip_address', '')

    output = dict(
        changed=True,
        username=username,
        password=password,
        ip_address=ip_address,
    )

    if vault_encrypt:
        host_dir = os.path.join(host_vars_dir, instance)
        os.makedirs(host_dir, exist_ok=True)
        vault_file = os.path.join(host_dir, 'vault.yml')

        yaml_content = "ansible_user: %s\nansible_password: %s\n" % (username, password)

        # Write to a temp file then encrypt in place
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False, dir=host_dir) as tmp:
            tmp.write(yaml_content)
            tmp_path = tmp.name

        encrypt_cmd = [
            'ansible-vault', 'encrypt', tmp_path,
            '--vault-password-file', vault_password_file,
        ]
        rc, stdout, stderr = module.run_command(encrypt_cmd)
        if rc != 0:
            os.unlink(tmp_path)
            module.fail_json(msg="ansible-vault encrypt failed: %s" % stderr)

        # Move encrypted file to final location
        os.rename(tmp_path, vault_file)
        output['vault_file'] = vault_file

    module.exit_json(**output)


def main():
    run_module()


if __name__ == '__main__':
    main()
