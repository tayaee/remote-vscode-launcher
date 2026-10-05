"""Windows-side daemon (rvl-server: remote vscode launcher server): listens for HTTP triggers
and opens local VS Code.

Run on your Windows machine:

    rvl-server --port 8259 --token s3cret

Then from the remote Linux box (inside SSH):

    rvl --server <windows-ip>

Protocol (JSON over HTTP):
    POST /launch  {"path": "/home/user/proj", "host": "myserver", ...}
    -> launches ``code --folder-uri vscode-remote://ssh-remote+myserver/home/user/proj``

Auth: if the server was started with a token, the client must send
``Authorization: Bearer <token>`` or ``X-Rvl-Token: <token>``.
(``X-Vsl-Token`` / ``X-Vscode-Server-Token`` are still accepted for backward compat.)
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import secrets
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import click

from . import __version__
from .common import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    ENV_CODE_BIN,
    ENV_HOST,
    ENV_PORT,
    ENV_TOKEN,
    LAUNCH_PATHS,
    LEGACY2_ENV_CODE_BIN,
    LEGACY2_ENV_HOST,
    LEGACY2_ENV_PORT,
    LEGACY2_ENV_TOKEN,
    LEGACY_ENV_CODE_BIN,
    LEGACY_ENV_HOST,
    LEGACY_ENV_PORT,
    LEGACY_ENV_TOKEN,
    build_folder_uri,
    build_launch_command,
    ensure_ssh_user,
    find_code_binary,
    format_command,
    normalize_token,
)

log = logging.getLogger("rvl-server")

MAX_BODY_BYTES = 64 * 1024


class ServerConfig:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        token: str | None = None,
        code_binary: str | None = None,
        default_host: str | None = None,
        dry_run: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.code_binary = code_binary
        self.default_host = default_host
        self.dry_run = dry_run


def _check_auth(handler: BaseHTTPRequestHandler, token: str | None) -> bool:
    # Token auth is optional: no server token -> accept everything.
    token = normalize_token(token)
    if token is None:
        return True
    auth = handler.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        presented = auth[len("Bearer "):].strip()
        if secrets.compare_digest(presented, token):
            return True
    presented = handler.headers.get("X-Rvl-Token", "").strip()
    if presented and secrets.compare_digest(presented, token):
        return True
    presented = handler.headers.get("X-Vsl-Token", "").strip()
    if presented and secrets.compare_digest(presented, token):
        return True
    presented = handler.headers.get("X-Vscode-Server-Token", "").strip()
    if presented and secrets.compare_digest(presented, token):
        return True
    return False


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_handler(config: ServerConfig):
    class LaunchHandler(BaseHTTPRequestHandler):
        server_version = f"rvl-server/{__version__}"

        def log_message(self, fmt, *args):  # noqa: N802 - stdlib signature
            log.info("%s - %s", self.address_string(), fmt % args)

        def _route(self) -> str:
            return urlparse(self.path).path.rstrip("/") or "/"

        def do_GET(self):  # noqa: N802
            route = self._route()
            if route == "/health":
                _send_json(self, 200, {"status": "ok", "service": "rvl-server"})
            elif route in ("/", "/launch", "/open"):
                _send_json(
                    self,
                    200,
                    {
                        "service": "rvl-server",
                        "usage": "POST /launch with JSON {path, host}",
                        "example": {
                            "path": "/home/user/project",
                            "host": "myserver (Remote-SSH Host alias)",
                        },
                    },
                )
            else:
                _send_json(self, 404, {"error": f"unknown path: {route}"})

        def do_POST(self):  # noqa: N802
            route = self._route()
            if route not in LAUNCH_PATHS:
                _send_json(self, 404, {"error": f"unknown path: {route}"})
                return
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length > MAX_BODY_BYTES:
                _send_json(self, 413, {"error": "request body too large"})
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                _send_json(self, 400, {"error": "invalid JSON body"})
                return
            if not isinstance(data, dict):
                _send_json(self, 400, {"error": "JSON body must be an object"})
                return

            if not _check_auth(self, config.token):
                log.warning("Rejected unauthorized %s %s from %s:%d",
                            "POST", route, *self.client_address[:2])
                _send_json(self, 401, {"error": "unauthorized: bad or missing token"})
                return

            remote_path = str(data.get("path") or data.get("cwd") or "").strip()
            ssh_host = str(
                data.get("host") or data.get("ssh_host") or data.get("hostname") or ""
            ).strip() or (config.default_host or "")
            if not remote_path:
                _send_json(self, 400, {"error": "missing required field: 'path'"})
                return
            if not ssh_host:
                _send_json(
                    self,
                    400,
                    {"error": "missing SSH host alias: send {'host': '<Remote-SSH Host>'} "
                              "or start server with --default-host"},
                )
                return
            try:
                uri = build_folder_uri(ssh_host, remote_path)
            except ValueError as e:
                _send_json(self, 400, {"error": str(e)})
                return

            # Windows <-> Linux login ids may differ. The folder URI cannot
            # carry a username, so make the ssh config log in as the Linux id.
            login_user = str(data.get("user") or "").strip()
            ssh_result: dict = {}
            if login_user:
                try:
                    server_user = getpass.getuser()
                except Exception:  # noqa: BLE001
                    server_user = ""
                if server_user and login_user == server_user:
                    ssh_result = {"ssh_user": login_user, "ssh_config": "ok"}
                else:
                    status, message = ensure_ssh_user(ssh_host, login_user)
                    ssh_result = {"ssh_user": login_user, "ssh_config": status}
                    log.info("SSH user handling for %s: %s (%s)", ssh_host, status, message)
                    if status in ("missing", "error"):
                        ssh_result["warning"] = message

            log.info("Launch request from %s:%d: host=%s path=%s user=%s",
                       *self.client_address[:2], ssh_host, remote_path, login_user or "-")

            if config.dry_run:
                _send_json(self, 200, {
                    "status": "dry-run",
                    "uri": uri,
                    "cmd": build_launch_command(config.code_binary, uri),
                    **ssh_result,
                })
                return

            try:
                code_bin = config.code_binary or find_code_binary()
            except FileNotFoundError as e:
                _send_json(self, 500, {"error": str(e)})
                return

            try:
                # Detached so the HTTP response returns immediately and the
                # VS Code process outlives the handler thread.
                cmd = build_launch_command(code_bin, uri)
                log.info("Executing for %s:%d: %s", *self.client_address[:2], format_command(cmd))
                kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)  # type: ignore[attr-defined]
                else:
                    kwargs["start_new_session"] = True
                subprocess.Popen(cmd, **kwargs)  # noqa: S603
            except Exception as e:  # noqa: BLE001
                log.exception("Failed to launch VS Code")
                _send_json(self, 500, {"error": f"failed to launch VS Code: {e}"})
                return

            log.info("VS Code launched successfully for %s:%d: uri=%s",
                     *self.client_address[:2], uri)

            _send_json(self, 200, {"status": "launched", "uri": uri, "cmd": cmd, **ssh_result})

    return LaunchHandler


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--host", default=DEFAULT_HOST, envvar=[ENV_HOST, LEGACY_ENV_HOST, LEGACY2_ENV_HOST],
              show_default=True, help=f"Bind address (env {ENV_HOST}).")
@click.option("--port", type=int, default=DEFAULT_PORT, envvar=[ENV_PORT, LEGACY_ENV_PORT, LEGACY2_ENV_PORT],
              show_default=True, help=f"Listen port (env {ENV_PORT}).")
@click.option("--token", default=None, envvar=[ENV_TOKEN, LEGACY_ENV_TOKEN, LEGACY2_ENV_TOKEN],
              help=f"Optional shared secret (env {ENV_TOKEN}). "
                   "If set, client must present it; if omitted, all requests are accepted.")
@click.option("--generate-token", is_flag=True,
              help="Print a random token and exit (use it for --token).")
@click.option("--code-binary", default=None, envvar=[ENV_CODE_BIN, LEGACY_ENV_CODE_BIN, LEGACY2_ENV_CODE_BIN],
              help=f"Path to VS Code CLI (default: auto-detect; env {ENV_CODE_BIN}).")
@click.option("--default-host", default=None,
              help="Default Remote-SSH Host alias when the trigger omits 'host'.")
@click.option("--dry-run", is_flag=True,
              help="Accept requests but only log the URI instead of launching VS Code.")
@click.option("--verbose", is_flag=True, help="Verbose logging.")
@click.version_option(__version__, "-v", "--version", message="%(version)s")
def cli(host, port, token, generate_token, code_binary, default_host, dry_run, verbose) -> int:
    """Listen for HTTP triggers and open local VS Code on the remote directory."""
    return _run(host, port, token, generate_token, code_binary, default_host, dry_run, verbose)


def main(argv: list[str] | None = None) -> int:
    """Entry point (keeps ``main(argv) -> int`` for tests/embedders)."""
    try:
        rc = cli.main(args=list(argv) if argv is not None else None,
                      prog_name="rvl-server", standalone_mode=False)
        return rc if isinstance(rc, int) else 0
    except click.exceptions.Exit as e:
        return e.exit_code
    except (click.ClickException, click.Abort) as e:
        e.show()
        return getattr(e, "exit_code", 1)


def _run(host, port, token, generate_token, code_binary, default_host, dry_run, verbose) -> int:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if generate_token:
        print(secrets.token_urlsafe(32))
        return 0

    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "?"

    config = ServerConfig(
        host=host,
        port=port,
        token=normalize_token(token),
        code_binary=code_binary,
        default_host=default_host,
        dry_run=dry_run,
    )

    try:
        handler = make_handler(config)
        httpd = ThreadingHTTPServer((config.host, config.port), handler)
    except OSError as e:
        log.error("Cannot bind %s:%d: %s", config.host, config.port, e)
        return 1

    bind = f"{config.host}:{httpd.server_port}"
    log.info("rvl-server listening on %s (user=%s, dry_run=%s)", bind, user, config.dry_run)
    if config.token:
        log.info("Token auth enabled.")
    else:
        log.warning("No --token set: anyone on the network can open VS Code on this machine. "
                    "Use --generate-token + --token for LAN use.")
    if config.host in ("127.0.0.1", "localhost"):
        log.warning("Bound to loopback only; remote Linux hosts cannot reach it. "
                    "Use --host 0.0.0.0 (and a Windows firewall rule) or an SSH -R tunnel.")
    log.info("Health: GET http://%s/health | Trigger: POST http://%s/launch", bind, bind)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
