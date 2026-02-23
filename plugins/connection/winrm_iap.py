from __future__ import (annotations, absolute_import, division, print_function)
__metaclass__ = type

DOCUMENTATION = r"""
    author: Matthew Venne
    name: winrm_iap
    short_description: Run tasks over WinRM through a GCP IAP tunnel
    description:
        - Extends the built-in C(winrm) connection plugin to automatically
          manage a GCP Identity-Aware Proxy (IAP) TCP tunnel.
        - Before establishing WinRM, the plugin launches
          C(gcloud compute start-iap-tunnel) to forward a local port to the
          remote Windows host's WinRM HTTPS port (5986).
        - The parent WinRM connection then talks to C(localhost:<local_port>)
          instead of the real host, which has no public IP.
        - Each connection instance gets its own tunnel with an OS-assigned
          port, so parallel forks work safely.
    version_added: "1.0.0"
    extends_documentation_fragment:
        - connection_pipelining
    requirements:
        - pywinrm (python library)
        - gcloud CLI with IAP permissions
    options:
      gcp_instance_name:
        description:
            - The GCP instance name to tunnel to.
            - Defaults to C(inventory_hostname) if not set.
        vars:
            - name: ansible_gcp_instance_name
            - name: gcp_instance_name
        type: str
      gcp_project:
        description: The GCP project containing the instance.
        required: true
        vars:
            - name: ansible_gcp_project
            - name: gcp_project
        type: str
      gcp_zone:
        description: The GCP zone of the instance.
        required: true
        vars:
            - name: ansible_gcp_zone
            - name: gcp_zone
        type: str
      gcp_iap_service_account:
        description:
            - Optional service account email for C(--impersonate-service-account)
              when creating the IAP tunnel.
        vars:
            - name: ansible_gcp_iap_service_account
            - name: gcp_iap_service_account
        type: str
      iap_tunnel_timeout:
        description:
            - Timeout in seconds to wait for the IAP tunnel to become ready.
        default: 30
        vars:
            - name: ansible_iap_tunnel_timeout
        type: int
      remote_addr:
        description:
            - Address of the windows machine.
            - Overridden by the plugin to C(localhost) once the tunnel is up.
        default: inventory_hostname
        vars:
            - name: inventory_hostname
            - name: ansible_host
            - name: ansible_winrm_host
        type: str
      remote_user:
        description:
            - The user to log in as to the Windows machine.
        vars:
            - name: ansible_user
            - name: ansible_winrm_user
        keyword:
            - name: remote_user
        type: str
      remote_password:
        description: Authentication password for the O(remote_user).
        vars:
            - name: ansible_password
            - name: ansible_winrm_pass
            - name: ansible_winrm_password
        type: str
        aliases:
        - password
      port:
        description:
            - Port for WinRM on the remote target.
            - The default is the HTTPS port (5986).
        vars:
          - name: ansible_port
          - name: ansible_winrm_port
        default: 5986
        keyword:
            - name: port
        type: integer
      scheme:
        description:
            - URI scheme to use.
            - Defaults to V(https) or V(http) if O(port) is V(5985).
        choices: [http, https]
        vars:
          - name: ansible_winrm_scheme
        type: str
      path:
        description: URI path to connect to.
        default: '/wsman'
        vars:
          - name: ansible_winrm_path
        type: str
      transport:
        description:
           - List of winrm transports to attempt to use (ssl, plaintext, kerberos, etc).
        type: list
        elements: string
        vars:
          - name: ansible_winrm_transport
      kerberos_command:
        description: kerberos command to use to request an authentication ticket.
        default: kinit
        vars:
          - name: ansible_winrm_kinit_cmd
        type: str
      kinit_args:
        description: Extra arguments to pass to C(kinit).
        type: str
        vars:
          - name: ansible_winrm_kinit_args
      kinit_env_vars:
        description: Environment variables to pass through to C(kinit).
        type: list
        elements: str
        default: []
        vars:
          - name: ansible_winrm_kinit_env_vars
      kerberos_mode:
        description: kerberos usage mode (managed or manual).
        choices: [managed, manual]
        vars:
          - name: ansible_winrm_kinit_mode
        type: str
      connection_timeout:
        description:
            - Sets both the operation and read timeout for the WinRM connection.
        vars:
          - name: ansible_winrm_connection_timeout
        type: int
"""

EXAMPLES = r"""
# group_vars/windows/connection.yml
ansible_connection: winrm_iap
ansible_winrm_transport: [ntlm]
ansible_winrm_scheme: https
ansible_winrm_port: 5986
ansible_winrm_server_cert_validation: ignore
ansible_shell_type: powershell

# inventory/staging.yml
all:
  children:
    windows:
      hosts:
        ripcord-staging-win-01:
          gcp_instance_name: ripcord-staging-win-01
          gcp_project: armory-ripcord-staging
          gcp_zone: us-east4-a
"""

import os
import re
import select
import signal
import socket
import subprocess
import typing as t

from ansible.errors import AnsibleConnectionFailure, AnsibleError
from ansible.plugins.connection.winrm import Connection as WinRMConnection
from ansible.utils.display import Display

display = Display()


class Connection(WinRMConnection):
    """WinRM connection over a GCP IAP tunnel."""

    transport = 'winrm_iap'
    module_implementation_preferences = ('.ps1', '.exe', '')

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        self._iap_tunnel_proc: subprocess.Popen | None = None
        self._iap_local_port: int | None = None

    def _get_iap_instance_name(self) -> str:
        name = self.get_option('gcp_instance_name')
        if name:
            return name
        return self.get_option('remote_addr') or self._play_context.remote_addr

    def _start_iap_tunnel(self) -> int:
        """Start a gcloud IAP tunnel and return the local port."""
        if self._iap_tunnel_proc and self._iap_tunnel_proc.poll() is None:
            return self._iap_local_port

        instance = self._get_iap_instance_name()
        project = self.get_option('gcp_project')
        zone = self.get_option('gcp_zone')
        remote_port = self.get_option('port') or 5986
        timeout = self.get_option('iap_tunnel_timeout') or 30

        if not project:
            raise AnsibleError("gcp_project is required for winrm_iap connection")
        if not zone:
            raise AnsibleError("gcp_zone is required for winrm_iap connection")

        cmd = [
            'gcloud', 'compute', 'start-iap-tunnel',
            instance, str(remote_port),
            '--local-host-port=localhost:0',
            '--zone', zone,
            '--project', project,
        ]

        sa = self.get_option('gcp_iap_service_account')
        if sa:
            cmd.extend(['--impersonate-service-account', sa])

        display.vvv(
            "WINRM_IAP: starting tunnel: %s" % ' '.join(cmd),
            host=instance,
        )

        self._iap_tunnel_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Wait for the tunnel to report the listening port on stderr, then
        # poll the port until it accepts connections.
        port_re = re.compile(r'(?:Listening on port|Picking local unused port) \[(\d+)\]')
        collected_stderr = []
        deadline = __import__('time').time() + timeout

        # Phase 1: read stderr until we see the port number.
        while __import__('time').time() < deadline:
            if self._iap_tunnel_proc.poll() is not None:
                remaining = self._iap_tunnel_proc.stderr.read().decode('utf-8', errors='replace')
                collected_stderr.append(remaining)
                raise AnsibleConnectionFailure(
                    "IAP tunnel process exited unexpectedly (rc=%d): %s"
                    % (self._iap_tunnel_proc.returncode, ''.join(collected_stderr))
                )

            ready, _, _ = select.select(
                [self._iap_tunnel_proc.stderr], [], [], 1.0
            )
            if ready:
                line = self._iap_tunnel_proc.stderr.readline().decode(
                    'utf-8', errors='replace'
                )
                if not line:
                    continue
                collected_stderr.append(line)
                display.vvvv("WINRM_IAP tunnel stderr: %s" % line.strip(), host=instance)
                m = port_re.search(line)
                if m:
                    self._iap_local_port = int(m.group(1))
                    break

        if self._iap_local_port is None:
            self._stop_iap_tunnel()
            raise AnsibleConnectionFailure(
                "Timed out waiting for IAP tunnel port after %ds. Stderr: %s"
                % (timeout, ''.join(collected_stderr))
            )

        # Phase 2: poll until the local port accepts TCP connections.
        while __import__('time').time() < deadline:
            if self._iap_tunnel_proc.poll() is not None:
                raise AnsibleConnectionFailure(
                    "IAP tunnel died while waiting for port to become ready (rc=%d)"
                    % self._iap_tunnel_proc.returncode
                )
            try:
                sock = socket.create_connection(('localhost', self._iap_local_port), timeout=1)
                sock.close()
                display.vvv(
                    "WINRM_IAP: tunnel ready on localhost:%d -> %s:%s"
                    % (self._iap_local_port, instance, remote_port),
                    host=instance,
                )
                return self._iap_local_port
            except OSError:
                __import__('time').sleep(0.5)

        # Timeout reached
        self._stop_iap_tunnel()
        raise AnsibleConnectionFailure(
            "Timed out waiting for IAP tunnel after %ds. Stderr: %s"
            % (timeout, ''.join(collected_stderr))
        )

    def _stop_iap_tunnel(self) -> None:
        """Terminate the IAP tunnel subprocess."""
        if self._iap_tunnel_proc:
            display.vvv("WINRM_IAP: stopping tunnel (pid=%d)" % self._iap_tunnel_proc.pid)
            try:
                os.killpg(os.getpgid(self._iap_tunnel_proc.pid), signal.SIGTERM)
            except OSError:
                pass
            try:
                self._iap_tunnel_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._iap_tunnel_proc.pid), signal.SIGKILL)
                except OSError:
                    pass
                self._iap_tunnel_proc.wait(timeout=5)
            self._iap_tunnel_proc = None
            self._iap_local_port = None

    def _connect(self) -> Connection:
        if self._connected:
            return self

        # Prevent macOS _scproxy crash: the system proxy lookup via
        # SCDynamicStoreCopyProxiesWithOptions is not fork-safe and
        # segfaults in Ansible worker processes.  Setting NO_PROXY
        # for localhost makes requests skip the native proxy check.
        os.environ.setdefault('NO_PROXY', 'localhost')

        # Start the IAP tunnel before the parent WinRM connection
        local_port = self._start_iap_tunnel()

        # Override the address and port so the parent connects through the tunnel
        self.set_option('remote_addr', 'localhost')
        self.set_option('port', local_port)

        # Ensure cert validation is disabled — the WinRM certificate is
        # issued for the Windows hostname, not localhost.
        extras = self.get_option('_extras')
        extras.setdefault('ansible_winrm_server_cert_validation', 'ignore')

        # Now let the parent handle WinRM connection setup
        return super()._connect()

    def reset(self) -> None:
        self._stop_iap_tunnel()
        self.protocol = None
        self.shell_id = None
        self._connected = False

        # Reconnect (will start a new tunnel)
        self._connect()

    def close(self) -> None:
        super().close()
        self._stop_iap_tunnel()
