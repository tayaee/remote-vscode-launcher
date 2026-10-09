"""Shared constants and helpers for rvl-server (listener) and rvl (trigger)."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path

DEFAULT_PORT = 8259
DEFAULT_HOST = "0.0.0.0"
DEFAULT_TIMEOUT = 10

ENV_PORT = "RVL_PORT"
ENV_HOST = "RVL_HOST"  # client: Windows machine address; server: bind address override
ENV_TOKEN = "RVL_TOKEN"
ENV_SSH_HOST = "RVL_SSH_HOST"  # Remote-SSH Host alias as known on the Windows side
ENV_SSH_HOST_ALT = "VSL_SSH_HOST"  # legacy alias (kept for backward compat)
ENV_CODE_BIN = "RVL_BINARY"
ENV_SSH_CONFIG = "RVL_SSH_CONFIG"  # override path to ssh config (default: ~/.ssh/config)

# Legacy names from the vsl (VSL_*) and vscode-server (CODE_*) eras (still accepted as fallback).
LEGACY_ENV_PORT = "VSL_PORT"
LEGACY_ENV_HOST = "VSL_HOST"
LEGACY_ENV_TOKEN = "VSL_TOKEN"
LEGACY_ENV_SSH_HOST_ALT = "CODE_SSH_HOST"
LEGACY_ENV_CODE_BIN = "VSL_BINARY"
LEGACY_ENV_SSH_CONFIG = "VSL_SSH_CONFIG"  # override path to ssh config (default: ~/.ssh/config)

# Second-generation legacy names from the vscode-server era (still accepted as fallback).
LEGACY2_ENV_PORT = "CODE_SERVER_PORT"
LEGACY2_ENV_HOST = "CODE_SERVER_HOST"
LEGACY2_ENV_TOKEN = "CODE_SERVER_TOKEN"
LEGACY2_ENV_SSH_HOST_ALT = "CODE_SERVER_SSH_HOST"
LEGACY2_ENV_CODE_BIN = "CODE_SERVER_BINARY"
LEGACY2_ENV_SSH_CONFIG = "CODE_SSH_CONFIG"  # override path to ssh config (default: ~/.ssh/config)

LAUNCH_PATHS = ("/launch", "/open", "/")

# Self-update (``rvl --update`` / ``rvl-server --update``): force-reinstall the
# package via uv, same command as install.sh / README.
UPDATE_REPO = "git+https://github.com/tayaee/remote-vscode-launcher.git"
UPDATE_PACKAGE = "remote-vscode-launcher"
UPDATE_COMMAND = ["uv", "tool", "install", "--from", UPDATE_REPO, "--force", UPDATE_PACKAGE]
# Fallback when plain ``uv`` is not on PATH (or fails): run it through mise,
# which also covers mise-activated projects (uses the surrounding mise.toml).
MISE_UPDATE_COMMAND = ["mise", "exec", "--", *UPDATE_COMMAND]


def run_self_update() -> int:
    """Print and run the ``uv tool install --force`` self-update command.

    Displayed/logged (and suggested on failure) command is always the plain
    ``+ uv ...`` form so callers can see and copy-paste it. Internally, when
    the plain ``uv`` run is unavailable or fails, ``mise exec -- uv ...``
    is attempted as a fallback.
    Returns the process exit code (1 when both attempts fail).
    """
    print(f"+ {shlex.join(UPDATE_COMMAND)}", flush=True)
    try:
        completed = subprocess.run(UPDATE_COMMAND)  # noqa: S603
    except FileNotFoundError:
        print("[self-update] 'uv' not found on PATH; trying mise fallback...", file=sys.stderr)
        completed = None
    except OSError as e:
        print(f"[self-update] uv run failed ({e}); trying mise fallback...", file=sys.stderr)
        completed = None
    else:
        if completed.returncode == 0:
            return 0
        print(f"[self-update] uv exited with {completed.returncode}; "
              "trying mise fallback...", file=sys.stderr)

    print(f"+ {shlex.join(MISE_UPDATE_COMMAND)}", flush=True)
    try:
        mise_completed = subprocess.run(MISE_UPDATE_COMMAND)  # noqa: S603
    except FileNotFoundError:
        print("error: 'uv' not found on PATH and 'mise' fallback unavailable; "
              "install uv first (https://docs.astral.sh/uv/).", file=sys.stderr)
        print(f"manual update: {shlex.join(UPDATE_COMMAND)}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: failed to run self-update: {e}", file=sys.stderr)
        print(f"manual update: {shlex.join(UPDATE_COMMAND)}", file=sys.stderr)
        return 1
    if mise_completed.returncode != 0:
        print(f"error: self-update failed (exit {mise_completed.returncode}).", file=sys.stderr)
        print(f"manual update: {shlex.join(UPDATE_COMMAND)}", file=sys.stderr)
    return mise_completed.returncode


def _env_first(*names: str) -> str:
    """Return the first non-empty env value among *names*, else ""."""
    for name in names:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def normalize_token(value: str | None) -> str | None:
    """Normalize an auth token: empty/whitespace-only means 'no token'.

    Returns the stripped token, or None when auth is disabled (accept-all).
    Token auth is purely optional: if the server has no token it accepts
    every request; if it has one the client must present it.
    """
    if value is None:
        return None
    value = value.strip()
    return value or None


def build_folder_uri(ssh_host: str, remote_path: str) -> str:
    """Build a ``vscode-remote://ssh-remote+<host><path>`` folder URI.

    Example:
        >>> build_folder_uri("myserver", "/home/user/proj")
        'vscode-remote://ssh-remote+myserver/home/user/proj'
    """
    ssh_host = ssh_host.strip()
    if not ssh_host:
        raise ValueError("ssh_host must not be empty")
    p = remote_path.strip().replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p
    # Collapse duplicate slashes (but keep leading single slash).
    while "//" in p:
        p = p.replace("//", "/")
    return f"vscode-remote://ssh-remote+{ssh_host}{p}"


FOLDER_URI_FLAG = "--folder-uri"


def build_launch_command(code_bin: str | None, uri: str) -> list[str]:
    """Build the VS Code CLI command that opens *uri*.

    Example:
        >>> build_launch_command("code", "vscode-remote://ssh-remote+h/tmp")
        ['code', '--folder-uri', 'vscode-remote://ssh-remote+h/tmp']
    """
    return [code_bin or "code", FOLDER_URI_FLAG, uri]


def format_command(cmd: list[str]) -> str:
    """Render *cmd* as a single, copy-pasteable command line.

    Windows-style arguments (drive letter or backslashes) get double quotes so
    the line pastes into ``cmd.exe``; everything else uses POSIX quoting.
    """
    parts: list[str] = []
    for arg in cmd:
        if not arg:
            parts.append('""')
        elif _looks_like_windows_path(arg):
            parts.append(f'"{arg}"' if " " in arg else arg)
        else:
            parts.append(shlex.quote(arg))
    return " ".join(parts)


def _looks_like_windows_path(arg: str) -> bool:
    """True for drive-letter (``C:\\x``) or backslash-containing arguments."""
    return "\\" in arg or (len(arg) >= 2 and arg[0].isalpha() and arg[1] == ":")


# Editor executables we can shorten to a bare command name when printed.
_SHORTENABLE_EXES = {"code", "code-insiders", "codium"}


def friendly_command(cmd: list[str]) -> list[str]:
    """Shorten a known VS Code executable path to its bare command name.

    ``C:\\Program Files\\...\\code.CMD`` (or ``/usr/bin/code``) becomes ``code``
    so the printed command line stays short and copy-pasteable; unknown
    executables are left untouched.
    """
    if not cmd:
        return list(cmd)
    exe = str(cmd[0])
    base = os.path.basename(exe.replace("\\", "/")).lower()
    stem = base.rsplit(".", 1)[0] if "." in base else base
    if stem in _SHORTENABLE_EXES:
        return [stem, *[str(a) for a in cmd[1:]]]
    return [str(a) for a in cmd]


def resolve_ssh_host(explicit: str | None, fallback_hostname: str | None = None) -> str | None:
    """Resolve which SSH host alias to use, or None if unknown."""
    if explicit and explicit.strip():
        return explicit.strip()
    for env in (ENV_SSH_HOST, ENV_SSH_HOST_ALT, LEGACY_ENV_SSH_HOST_ALT, LEGACY2_ENV_SSH_HOST_ALT):
        v = os.environ.get(env, "").strip()
        if v:
            return v
    if fallback_hostname and fallback_hostname.strip():
        return fallback_hostname.strip()
    return None


def detect_client_ip() -> str | None:
    """Best-effort detection of the Windows/SSH-client IP from SSH env vars.

    Returns the IP of the machine you SSH'd *from* (i.e. your Windows box),
    or None when not in an SSH session.

    ``SSH_CONNECTION`` (``client_ip client_port server_ip server_port``) is
    preferred: it is the current OpenSSH standard variable. ``SSH_CLIENT``
    (``client_ip client_port server_port``) is its legacy predecessor and is
    only used as a fallback.
    """
    # SSH_CONNECTION="client_ip client_port server_ip server_port" (preferred)
    ssh_conn = os.environ.get("SSH_CONNECTION", "").strip()
    if ssh_conn:
        parts = ssh_conn.split()
        if parts and _looks_like_ip(parts[0]):
            return parts[0]
    # SSH_CLIENT="client_ip client_port server_port" (legacy fallback)
    ssh_client = os.environ.get("SSH_CLIENT", "").strip()
    if ssh_client:
        parts = ssh_client.split()
        if parts and _looks_like_ip(parts[0]):
            return parts[0]
    return None


def _looks_like_ip(s: str) -> bool:
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def find_code_binary(explicit: str | None = None) -> str:
    """Locate the VS Code CLI (``code``). Raises FileNotFoundError if missing."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env_bin = _env_first(ENV_CODE_BIN, LEGACY_ENV_CODE_BIN, LEGACY2_ENV_CODE_BIN)
    if env_bin:
        candidates.append(env_bin)
    candidates += ["code", "code.cmd", "code.exe"]

    for c in candidates:
        found = shutil.which(c)
        if found:
            return found
        if Path(c).exists():
            return c

    # Well-known Windows install locations.
    if os.name == "nt":
        extra = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Microsoft VS Code" / "bin" / "code.cmd",
            Path(os.environ.get("LocalAppData", "")) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Microsoft VS Code" / "bin" / "code.cmd",
        ]
        for p in extra:
            try:
                if p.exists():
                    return str(p)
            except OSError:
                continue

    raise FileNotFoundError(
        "VS Code CLI ('code') not found. Install VS Code and tick "
        f"'Add to PATH', or pass --code-binary / set {ENV_CODE_BIN}."
    )


def local_hostname() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def ssh_config_path() -> Path:
    """Path to the ssh client config (override via $RVL_SSH_CONFIG)."""
    override = _env_first(ENV_SSH_CONFIG, LEGACY_ENV_SSH_CONFIG, LEGACY2_ENV_SSH_CONFIG)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ssh" / "config"


def _is_plain_hostname(s: str) -> bool:
    """True if *s* looks like a directly connectable hostname (no patterns).

    e.g. ``spark1.local`` or ``192.168.1.10`` qualify; bare nicknames
    (``myserver``) and anything with wildcards do not.
    """
    if not s or any(c in s for c in (" ", "*", "?", "!", "/", "\\", "@", ":")):
        return False
    return "." in s or _looks_like_ip(s)


def _split_directive(line: str) -> tuple[str, str] | None:
    """Split an ssh-config line into (keyword, args); None for blank/comment."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if "=" in s:
        key, _, rest = s.partition("=")
        return key.strip(), rest.strip()
    parts = s.split(None, 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1].strip()


def ensure_ssh_user(host_alias: str, login_user: str,
                    config_path: Path | None = None) -> tuple[str, str]:
    """Ensure ssh config logs into *host_alias* as *login_user*.

    A ``vscode-remote://`` folder URI cannot carry a username, so the Linux
    login id must come from the Windows ssh config's ``User`` directive.
    This inserts ``User <login_user>`` into the first matching ``Host`` block
    (with a ``.bak`` backup) when no ``User`` (and no global ``Host *`` user)
    already covers the alias.

    Returns (status, message); status is one of:
      "ok"      - a User directive already covers the alias (untouched)
      "added"   - User directive was inserted (or a minimal Host block created)
      "missing" - no Host block for the alias and it is not itself a
                  connectable hostname; user must create one manually
      "error"   - config could not be read/written (see message)
    Only top-level ``Host`` blocks are examined (``Match``/``Include``d
    files are out of scope).

    When the alias is itself a hostname (e.g. ``spark1.local`` — the same
    name a working ``ssh user@host`` uses), a minimal block reproducing
    exactly that command is appended automatically, so no manual
    registration is needed.
    """
    host_alias = host_alias.strip()
    login_user = login_user.strip()
    if not host_alias or not login_user:
        return "error", "empty host alias or login user"
    path = config_path or ssh_config_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", f"ssh config not found: {path}"
    except OSError as e:
        return "error", f"cannot read ssh config {path}: {e}"

    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)

    # Locate blocks: list of (host_line_index, [patterns]).
    blocks: list[tuple[int, list[str]]] = []
    for i, raw in enumerate(lines):
        d = _split_directive(raw.split("#", 1)[0] if "#" in raw else raw)
        # NOTE: strip inline comments naively; '#' never appears in Host patterns.
        if d and d[0].lower() == "host":
            blocks.append((i, d[1].split()))

    def block_user(start: int) -> str | None:
        end = next((b[0] for b in blocks if b[0] > start), len(lines))
        for raw in lines[start + 1:end]:
            d = _split_directive(raw)
            if d and d[0].lower() == "user":
                return d[1].split()[0] if d[1].split() else ""
        return None

    alias_lc = host_alias.lower()
    exact = [b for b in blocks if any(p.lower() == alias_lc for p in b[1])]
    star_user = next((u for b in blocks if b[1] == ["*"] for u in [block_user(b[0])] if u), None)

    for b in exact:
        if block_user(b[0]):
            return "ok", f"Host {host_alias} already sets User"
    if star_user:
        return "ok", "global 'Host *' already sets User"

    if not exact:
        if _is_plain_hostname(host_alias):
            # The alias is itself a connectable hostname (the same name a
            # working `ssh user@host` uses). Append a minimal block that
            # reproduces exactly that command -- no manual registration needed.
            if not text.endswith(("\n", "\r")):
                text += newline
            block = (f"{newline}Host {host_alias}{newline}"
                     f"    HostName {host_alias}{newline}"
                     f"    User {login_user}{newline}")
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
                path.write_text(text + block, encoding="utf-8")
            except OSError as e:
                return "error", f"cannot update ssh config {path}: {e}"
            return "added", (f"Created 'Host {host_alias}' in {path} "
                             f"(HostName {host_alias}, User {login_user})")
        return ("missing",
                f"No 'Host {host_alias}' block in {path}; add one with "
                f"'HostName <real-host>' and 'User {login_user}' so VS Code "
                "logs in as the Linux id.")

    # Insert into the first matching block, mirroring its indent style.
    idx = exact[0][0]
    end = next((b[0] for b in blocks if b[0] > idx), len(lines))
    indent = "    "
    for raw in lines[idx + 1:end]:
        stripped = raw.lstrip()
        if stripped and not stripped.startswith("#") and raw[:len(raw) - len(stripped)]:
            indent = raw[:len(raw) - len(stripped)]
            break
    lines.insert(idx + 1, f"{indent}User {login_user}{newline}")
    try:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        path.write_text("".join(lines), encoding="utf-8")
    except OSError as e:
        return "error", f"cannot update ssh config {path}: {e}"
    return "added", f"Set 'User {login_user}' for Host {host_alias} in {path}"
