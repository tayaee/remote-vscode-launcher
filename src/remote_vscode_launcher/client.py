"""Linux-side trigger (rvl: remote vscode launcher client): tell your Windows
box to open VS Code here.

Run inside an active SSH session on the remote Linux machine (pairs with
rvl-server, the remote vscode launcher server, on Windows):

    rvl
    rvl --server 192.168.1.10 --ssh-host myserver
    rvl --path /home/user/project --token s3cret

The server address defaults (in order) to:
  1. ``--server``
  2. ``$RVL_HOST`` (legacy ``$VSL_HOST`` / ``$CODE_SERVER_HOST`` still accepted)
  3. client IP from ``$SSH_CLIENT`` / ``$SSH_CONNECTION``
  4. ``127.0.0.1`` (works with ``ssh -R 8259:localhost:8259`` reverse tunnel)

Payload sent: ``POST http://<server>:<port>/launch``
``{"path": "<cwd>", "host": "<ssh-alias>", "hostname": ..., "user": ...}``
"""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import click

from . import __version__
from .common import (
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    ENV_HOST,
    ENV_PORT,
    ENV_SSH_HOST,
    ENV_TOKEN,
    LEGACY2_ENV_HOST,
    LEGACY2_ENV_PORT,
    LEGACY2_ENV_TOKEN,
    LEGACY_ENV_HOST,
    LEGACY_ENV_PORT,
    LEGACY_ENV_TOKEN,
    build_folder_uri,
    build_launch_command,
    detect_client_ip,
    format_command,
    friendly_command,
    local_hostname,
    normalize_token,
    resolve_ssh_host,
    run_self_update,
)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("path", required=False, default=None)
@click.option("--path", "path_opt", default=None,
              help="Same as positional PATH (explicit flag).")
@click.option("--server", "--host", "server", default=None,
              envvar=[ENV_HOST, LEGACY_ENV_HOST, LEGACY2_ENV_HOST],
              help="Windows machine address (default: auto-detect from $SSH_CONNECTION / "
                   f"$SSH_CLIENT, else 127.0.0.1 for SSH -R tunnels; env {ENV_HOST}).")
@click.option("--port", type=int, default=DEFAULT_PORT, envvar=[ENV_PORT, LEGACY_ENV_PORT, LEGACY2_ENV_PORT],
              show_default=True, help=f"rvl-server port (env {ENV_PORT}).")
@click.option("--token", default=None, envvar=[ENV_TOKEN, LEGACY_ENV_TOKEN, LEGACY2_ENV_TOKEN],
              help=f"Optional shared secret, only needed when the server was started "
                   f"with one (env {ENV_TOKEN}). Omit to send no token.")
@click.option("--ssh-host", default=None,
              help="Remote-SSH Host alias as configured on Windows "
                   f"(default: ${ENV_SSH_HOST} or local hostname).")
@click.option("--timeout", type=float, default=DEFAULT_TIMEOUT, show_default=True,
              help="HTTP timeout in seconds.")
@click.option("--dry-run", is_flag=True,
              help="Print the request without sending it.")
@click.option("--update", "--upgrade", "do_update", is_flag=True,
              help="Self-update via 'uv tool install --force' and exit.")
@click.option("--verbose", is_flag=True, help="Verbose output.")
@click.version_option(__version__, "-v", "--version", message="%(version)s")
def cli(path, path_opt, server, port, token, ssh_host, timeout, dry_run, do_update, verbose) -> int:
    """Trigger the Windows rvl-server listener to open VS Code here."""
    return _run(path, path_opt, server, port, token, ssh_host, timeout, dry_run, do_update,
                verbose)


def resolve_target_dir(path_arg: str | None, path_opt: str | None) -> str:
    raw = path_opt or path_arg or os.getcwd()
    # Keep it absolute; do not require existence (dir may be transient).
    p = Path(raw)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    return os.path.normpath(str(p))


def _is_loopback(host: str) -> bool:
    """True when *host* is a loopback address (no fallback needed)."""
    h = (host or "").strip().lower().strip("[]")
    return h in ("127.0.0.1", "localhost", "::1")


def _is_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    """Best-effort check whether *host*:*port* accepts TCP connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _post_launch(url: str, body: bytes, token: str | None, timeout: float) -> tuple[str, int]:
    """POST the launch payload. Returns (raw_body, http_status).

    Raises urllib.error.HTTPError (server reached, rejected) or
    urllib.error.URLError / OSError / timeout (unreachable) to the caller.
    """
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    token = normalize_token(token)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), resp.status


def _print_result(server: str, port: int, uri: str, ok: bool, reason: str = "") -> None:
    """Final two-line result log (stdout).

    Success:
        rvl (remote vscode launcher) is connecting to <server>:<port>
        to run [code --folder-uri <uri>].
        succeeded.
    Failure:
        ... same first line ...
        failed. (<reason>)
    """
    print(f"rvl (remote vscode launcher) is connecting to {server}:{port} "
          f"to run [code --folder-uri {uri}].")
    if ok:
        print("succeeded.")
    else:
        print(f"failed. ({reason})")


def main(argv: list[str] | None = None) -> int:
    """Entry point (keeps ``main(argv) -> int`` for tests/embedders)."""
    try:
        rc = cli.main(args=list(argv) if argv is not None else None,
                      prog_name="rvl", standalone_mode=False)
        return rc if isinstance(rc, int) else 0
    except click.exceptions.Exit as e:
        return e.exit_code
    except (click.ClickException, click.Abort) as e:
        e.show()
        return getattr(e, "exit_code", 1)


def _run(path, path_opt, server, port, token, ssh_host, timeout, dry_run, do_update,
           verbose) -> int:
    if do_update:
        return run_self_update()

    target_dir = resolve_target_dir(path, path_opt)

    server = (server or "").strip()
    auto_ip = detect_client_ip()
    if not server:
        if auto_ip:
            server = auto_ip
            if verbose:
                print(f"[rvl] auto-detected Windows host {server} from SSH env", file=sys.stderr)
        else:
            server = "127.0.0.1"
            print("[rvl] warning: not in an SSH session and --server not given; "
                   "trying 127.0.0.1 (works with `ssh -R 8259:localhost:8259`).",
                  file=sys.stderr)

    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = ""
    hostname = local_hostname()
    ssh_host = resolve_ssh_host(ssh_host, hostname)
    if not ssh_host:
        print("[rvl] error: cannot determine SSH host alias. "
              f"Pass --ssh-host <Remote-SSH Host> or set ${ENV_SSH_HOST}.", file=sys.stderr)
        return 2

    try:
        preview_uri = build_folder_uri(ssh_host, target_dir)
    except ValueError as e:
        print(f"[rvl] error: {e}", file=sys.stderr)
        return 2

    payload = {"path": target_dir, "host": ssh_host, "hostname": hostname, "user": user}

    if verbose or dry_run:
        print(f"[rvl] payload: {json.dumps(payload)}", file=sys.stderr)
        print(f"[rvl] will open: {preview_uri}", file=sys.stderr)
    if dry_run:
        print(f"[rvl] POST http://{server}:{port}/launch", file=sys.stderr)
        return 0

    body = json.dumps(payload).encode("utf-8")

    # Primary + loopback fallback (e.g. cloud host unreachable, ssh -R tunnel up).
    candidates = [server]
    if not _is_loopback(server):
        candidates.append("127.0.0.1")

    raw = ""
    status = 0
    last_error: Exception | None = None

    for i, candidate in enumerate(candidates):
        url = f"http://{candidate}:{port}/launch"
        if i > 0:
            probe_timeout = min(1.0, timeout) if timeout > 0 else 0.5
            if not _is_port_listening(candidate, port, timeout=probe_timeout):
                break  # nothing listening locally; report the primary failure.
            print(f"[rvl] info: {candidates[0]}:{port} not directly reachable, "
                  f"trying {candidate}:{port} via ssh -R tunnel",
                  file=sys.stderr)
        if verbose:
            print(f"[rvl] POST {url}", file=sys.stderr)
        try:
            raw, status = _post_launch(url, body, token, timeout)
            server = candidate  # actual endpoint used, shown in final log.
            last_error = None
            break
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                detail = ""
            print(f"[rvl] server rejected request at {url}: "
                  f"HTTP {e.code} {detail}", file=sys.stderr)
            if e.code == 401:
                print("[rvl] hint: token mismatch. Match --token with the server's --token.",
                      file=sys.stderr)
            elif e.code == 400:
                print("[rvl] hint: try --ssh-host <Remote-SSH Host alias from Windows ssh config>.",
                      file=sys.stderr)
            reason = f"HTTP {e.code} {detail}".strip()
            _print_result(server, port, preview_uri, False, reason)
            return 1
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as e:
            last_error = e
            reason = getattr(e, "reason", e) if isinstance(e, urllib.error.URLError) else e
            print(f"[rvl] cannot reach rvl-server at {url}: {reason}", file=sys.stderr)
            continue

    if last_error is not None:
        url = f"http://{server}:{port}/launch"
        print("[rvl] hints:", file=sys.stderr)
        print("  1. Is `rvl-server` running on Windows? (rvl-server --host 0.0.0.0)", file=sys.stderr)
        print("  2. Windows firewall allowing inbound TCP on the port?", file=sys.stderr)
        print(f"     PowerShell (admin): New-NetFirewallRule -DisplayName rvl-server "
              f"-Direction Inbound -LocalPort {port} -Protocol TCP -Action Allow", file=sys.stderr)
        print("  3. No direct LAN route? Use a reverse tunnel from Windows:", file=sys.stderr)
        print(f"     ssh -R {port}:localhost:{port} user@remote", file=sys.stderr)
        print("     then: rvl --server 127.0.0.1", file=sys.stderr)
        if not auto_ip:
            print("  4. $SSH_CLIENT/$SSH_CONNECTION empty: you may be in tmux/screen; "
                  "pass --server explicitly.", file=sys.stderr)
        reason_obj = getattr(last_error, "reason", last_error) \
            if isinstance(last_error, urllib.error.URLError) else last_error
        _print_result(server, port, preview_uri, False, str(reason_obj))
        return 1

    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {"raw": raw}
    uri = data.get("uri", preview_uri) if isinstance(data, dict) else preview_uri
    if status == 200:
        if isinstance(data, dict):
            if data.get("ssh_config") == "added":
                print(f"[rvl] ssh config updated: log in as '{data.get('ssh_user')}'.",
                      file=sys.stderr)
            elif data.get("warning"):
                print(f"[rvl] warning: {data['warning']}", file=sys.stderr)
        reported = data.get("cmd") if isinstance(data, dict) else None
        cmd = ([str(a) for a in reported] if isinstance(reported, list) and reported
               else build_launch_command("code", str(uri)))
        print(f"Remote VS Code launched successfully via {server}:{port}. "
              f"Command line: {format_command(friendly_command(cmd))}")
        return 0
    print(f"[rvl] unexpected status HTTP {status}: {raw}", file=sys.stderr)
    _print_result(server, port, uri, False, f"HTTP {status} {raw}".strip())
    return 1


if __name__ == "__main__":
    sys.exit(main())
