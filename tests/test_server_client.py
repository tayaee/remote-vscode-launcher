import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import click

from remote_vscode_launcher.client import cli as client_cli
from remote_vscode_launcher.client import main as client_main
from remote_vscode_launcher.common import (
    build_folder_uri,
    build_launch_command,
    detect_client_ip,
    ensure_ssh_user,
    format_command,
    friendly_command,
    normalize_token,
)
from remote_vscode_launcher.server import ServerConfig, make_handler
from remote_vscode_launcher.server import main as server_main


def _post(port, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/launch",
        data=json.dumps(body).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_build_folder_uri():
    assert build_folder_uri("myserver", "/home/u/proj") == \
        "vscode-remote://ssh-remote+myserver/home/u/proj"
    assert build_folder_uri("h", "a/b") == "vscode-remote://ssh-remote+h/a/b"


def test_build_launch_command_and_format():
    uri = "vscode-remote://ssh-remote+h/tmp"
    assert build_launch_command("code", uri) == ["code", "--folder-uri", uri]
    assert build_launch_command(None, uri) == ["code", "--folder-uri", uri]
    # POSIX rendering: no quoting needed for the URI itself.
    assert format_command(build_launch_command("code", uri)) == f"code --folder-uri {uri}"
    # Paths with spaces get quoted in the flavour their shell understands.
    assert format_command(["/usr/local/bin/code", "--folder-uri", "/a b"]) == \
        "/usr/local/bin/code --folder-uri '/a b'"
    assert format_command([r"C:\Program Files\VS Code\bin\code.cmd", uri]) == \
        rf'"C:\Program Files\VS Code\bin\code.cmd" {uri}'


def test_friendly_command_shortens_code_cli():
    uri = "vscode-remote://ssh-remote+h/tmp"
    assert friendly_command([r"C:\Program Files\Microsoft VS Code\bin\code.CMD", "--folder-uri", uri]) == \
        ["code", "--folder-uri", uri]
    assert friendly_command(["/usr/bin/code-insiders", "--folder-uri", uri]) == \
        ["code-insiders", "--folder-uri", uri]
    # Unknown editors keep their full path.
    assert friendly_command(["/opt/other/bin/other", "--folder-uri", uri]) == \
        ["/opt/other/bin/other", "--folder-uri", uri]


def test_client_parser_defaults():
    defaults = {p.name: p.default for p in client_cli.params if isinstance(p, click.Option)}
    assert defaults["port"] == 8259
    assert defaults["server"] is None


def test_client_version(capsys):
    from remote_vscode_launcher import __version__
    assert __version__ == "0.2.5"
    assert client_main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__
    assert server_main(["-v"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_normalize_token_optional():
    assert normalize_token(None) is None
    assert normalize_token("") is None
    assert normalize_token("   ") is None
    assert normalize_token("  s3cret  ") == "s3cret"


def test_server_no_token_accepts_all():
    # Token omitted -> accept everything (no token, or any spurious token).
    config = ServerConfig(host="127.0.0.1", port=0, token=None, dry_run=True)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, data = _post(port, {"path": "/tmp", "host": "h"})
        assert status == 200
        assert data["uri"] == "vscode-remote://ssh-remote+h/tmp"
        # Spurious token is also accepted when server has none.
        status, _ = _post(port, {"path": "/tmp", "host": "h"}, token="whatever")
        assert status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_empty_string_token_is_no_token():
    config = ServerConfig(host="127.0.0.1", port=0, token="  ", dry_run=True)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, _ = _post(port, {"path": "/tmp", "host": "h"})
        assert status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_launch_dry_run():
    config = ServerConfig(host="127.0.0.1", port=0, token="s3cret", dry_run=True)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        # No token -> 401
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/launch",
            data=json.dumps({"path": "/tmp", "host": "h"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401

        # With token -> 200 dry-run
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/launch",
            data=json.dumps({"path": "/tmp", "host": "h"}).encode(),
            headers={"Content-Type": "application/json",
                      "Authorization": "Bearer s3cret"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        assert data["status"] == "dry-run"
        assert data["uri"] == "vscode-remote://ssh-remote+h/tmp"

        # Health
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            assert json.loads(r.read())["status"] == "ok"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_detect_client_ip_no_env(monkeypatch):
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    assert detect_client_ip() is None


def test_detect_client_ip_ssh_client(monkeypatch):
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.setenv("SSH_CLIENT", "192.168.1.5 52341 22")
    assert detect_client_ip() == "192.168.1.5"


def test_detect_client_ip_prefers_ssh_connection(monkeypatch):
    # SSH_CONNECTION wins when both are present but disagree.
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.9 52341 10.0.0.1 22")
    monkeypatch.setenv("SSH_CLIENT", "192.168.1.5 52341 22")
    assert detect_client_ip() == "10.0.0.9"


def test_detect_client_ip_ssh_connection_only(monkeypatch):
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.9 52341 10.0.0.1 22")
    assert detect_client_ip() == "10.0.0.9"


def test_detect_client_ip_ipv6(monkeypatch):
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "fd00::1 52341 fd00::2 22")
    assert detect_client_ip() == "fd00::1"


def test_rvl_host_env_is_used(monkeypatch, capsys):
    # Nested SSH scenario: explicit origin propagated via SendEnv/AcceptEnv.
    for var in ("RVL_HOST", "RVL_SSH_HOST", "VSL_HOST", "VSL_SSH_HOST",
                "CODE_SERVER_HOST", "CODE_SSH_HOST", "CODE_SERVER_SSH_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RVL_HOST", "10.9.9.9")
    monkeypatch.setenv("RVL_SSH_HOST", "h")
    assert client_main(["--dry-run"]) == 0
    assert "http://10.9.9.9:8259/launch" in capsys.readouterr().err


def test_vsl_legacy_env_still_accepted(monkeypatch, capsys):
    # Backward compat with the vsl era names.
    for var in ("RVL_HOST", "RVL_SSH_HOST", "VSL_HOST", "VSL_SSH_HOST",
                "CODE_SERVER_HOST", "CODE_SSH_HOST", "CODE_SERVER_SSH_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VSL_HOST", "10.9.9.9")
    monkeypatch.setenv("VSL_SSH_HOST", "h")
    assert client_main(["--dry-run"]) == 0
    assert "http://10.9.9.9:8259/launch" in capsys.readouterr().err


def test_legacy_env_still_accepted(monkeypatch, capsys):
    # Backward compat with the vscode-server era names.
    for var in ("RVL_HOST", "RVL_SSH_HOST", "VSL_HOST", "VSL_SSH_HOST",
                "CODE_SERVER_HOST", "CODE_SSH_HOST", "CODE_SERVER_SSH_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CODE_SERVER_HOST", "10.9.9.9")
    monkeypatch.setenv("CODE_SSH_HOST", "h")
    assert client_main(["--dry-run"]) == 0
    assert "http://10.9.9.9:8259/launch" in capsys.readouterr().err


def test_explicit_server_beats_env(monkeypatch, capsys):
    for var in ("RVL_HOST", "RVL_SSH_HOST", "VSL_HOST", "VSL_SSH_HOST",
                "CODE_SERVER_HOST", "CODE_SSH_HOST", "CODE_SERVER_SSH_HOST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RVL_HOST", "10.9.9.9")
    monkeypatch.setenv("RVL_SSH_HOST", "h")
    assert client_main(["--dry-run", "--server", "192.168.0.1"]) == 0
    err = capsys.readouterr().err
    assert "http://192.168.0.1:8259/launch" in err
    assert "10.9.9.9" not in err


def test_ensure_ssh_user_adds_directive(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("Host myserver\n    HostName 10.0.0.1\n", encoding="utf-8")
    status, _ = ensure_ssh_user("myserver", "linuxid", cfg)
    assert status == "added"
    text = cfg.read_text(encoding="utf-8")
    assert "User linuxid" in text
    assert "HostName 10.0.0.1" in text  # rest untouched
    assert (tmp_path / "config.bak").exists()  # backup kept


def test_ensure_ssh_user_leaves_existing(tmp_path):
    cfg = tmp_path / "config"
    original = "Host myserver\n    HostName 10.0.0.1\n    User other\n"
    cfg.write_text(original, encoding="utf-8")
    status, _ = ensure_ssh_user("myserver", "linuxid", cfg)
    assert status == "ok"
    assert cfg.read_text(encoding="utf-8") == original


def test_ensure_ssh_user_missing_block(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text("Host other\n    HostName 10.0.0.2\n", encoding="utf-8")
    status, msg = ensure_ssh_user("myserver", "linuxid", cfg)
    assert status == "missing"  # bare nickname: HostName unknowable
    assert "linuxid" in msg


def test_ensure_ssh_user_creates_block_for_hostname(tmp_path):
    # `ssh user1@spark1.local` works with no config -> reproduce it as a block.
    cfg = tmp_path / "config"
    cfg.write_text("Host other\n    HostName 10.0.0.2\n", encoding="utf-8")
    status, _ = ensure_ssh_user("spark1.local", "user1", cfg)
    assert status == "added"
    text = cfg.read_text(encoding="utf-8")
    assert "Host spark1.local" in text
    assert "HostName spark1.local" in text
    assert "User user1" in text
    # Second call is a no-op now.
    status, _ = ensure_ssh_user("spark1.local", "user1", cfg)
    assert status == "ok"


def test_ensure_ssh_user_missing_file(tmp_path):
    status, _ = ensure_ssh_user("myserver", "linuxid", tmp_path / "nope")
    assert status == "missing"


def test_server_reports_ssh_user_handling(monkeypatch, tmp_path):
    # Linux id differs -> server inserts User into Windows ssh config.
    cfg = tmp_path / "config"
    cfg.write_text("Host h\n    HostName 10.0.0.1\n", encoding="utf-8")
    monkeypatch.setenv("RVL_SSH_CONFIG", str(cfg))
    config = ServerConfig(host="127.0.0.1", port=0, token=None, dry_run=True)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, data = _post(port, {"path": "/tmp", "host": "h", "user": "linuxid"})
        assert status == 200
        assert data["ssh_user"] == "linuxid"
        assert data["ssh_config"] == "added"
        assert "User linuxid" in cfg.read_text(encoding="utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_server_launch_returns_no_pid(monkeypatch):
    from unittest import mock

    import remote_vscode_launcher.server as srv_mod

    fake_proc = mock.Mock()
    fake_proc.pid = 1111
    monkeypatch.setattr(srv_mod.subprocess, "Popen", lambda *a, **k: fake_proc)

    config = ServerConfig(host="127.0.0.1", port=0, token=None, code_binary=sys.executable)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        status, data = _post(port, {"path": "/tmp", "host": "h"})
        assert status == 200
        assert data["status"] == "launched"
        assert data["uri"] == "vscode-remote://ssh-remote+h/tmp"
        # The command line actually executed is reported back to the client.
        assert data["cmd"] == [sys.executable, "--folder-uri", "vscode-remote://ssh-remote+h/tmp"]
        for key in ("launcher_pid", "vscode_pids", "pid", "pid_confidence"):
            assert key not in data
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_client_prints_success_confirm(monkeypatch, capsys):
    from unittest import mock

    import remote_vscode_launcher.server as srv_mod

    fake_proc = mock.Mock()
    fake_proc.pid = 1111
    monkeypatch.setattr(srv_mod.subprocess, "Popen", lambda *a, **k: fake_proc)

    config = ServerConfig(host="127.0.0.1", port=0, token=None, code_binary=sys.executable)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        rc = client_main(["--server", "127.0.0.1", "--port", str(port), "--ssh-host", "h",
                          "--path", "/tmp"])
        assert rc == 0
        out = capsys.readouterr().out
        assert out == (f"Remote VS Code launched successfully via 127.0.0.1:{port}. "
                      f"Command line: {sys.executable} --folder-uri "
                      f"vscode-remote://ssh-remote+h/tmp\n")
        assert "taskkill" not in out
        assert "pid" not in out.lower()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_client_prints_code_command_line(monkeypatch, capsys):
    """Success line shows the editor command line, shortened to `code`."""
    from unittest import mock

    import remote_vscode_launcher.server as srv_mod

    fake_proc = mock.Mock()
    monkeypatch.setattr(srv_mod.subprocess, "Popen", lambda *a, **k: fake_proc)
    monkeypatch.setattr(srv_mod, "find_code_binary",
                        lambda explicit=None: r"C:\Program Files\Microsoft VS Code\bin\code.CMD")

    config = ServerConfig(host="127.0.0.1", port=0, token=None)
    httpd = ThreadingHTTPServer((config.host, config.port), make_handler(config))
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        rc = client_main(["--server", "127.0.0.1", "--port", str(port), "--ssh-host", "dash3",
                          "--path", "/home/user1/git/remote-vscode-launcher"])
        assert rc == 0
        out = capsys.readouterr().out
        assert out == (f"Remote VS Code launched successfully via 127.0.0.1:{port}. "
                      "Command line: code --folder-uri "
                      "vscode-remote://ssh-remote+dash3/home/user1/git/"
                      "remote-vscode-launcher\n")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_self_update_success(monkeypatch):
    import subprocess

    from remote_vscode_launcher import common

    monkeypatch.setattr(common, "_prepare_windows_self_update", list)
    monkeypatch.setattr(common, "_finish_windows_self_update", lambda moved, ok: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0))

    assert common.run_self_update() == 0


def test_self_update_mise_fallback(monkeypatch):
    import subprocess

    from remote_vscode_launcher import common

    monkeypatch.setattr(common, "_prepare_windows_self_update", list)
    monkeypatch.setattr(common, "_finish_windows_self_update", lambda moved, ok: None)

    calls = []

    def fake_run(cmd, **k):
        calls.append(cmd)
        if cmd == common.UPDATE_COMMAND:
            return subprocess.CompletedProcess(cmd, 2)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert common.run_self_update() == 0
    assert len(calls) == 2


def test_windows_self_update_rename_and_rollback(tmp_path, monkeypatch):
    from remote_vscode_launcher import common

    monkeypatch.setattr(common.os, "name", "nt")
    monkeypatch.setattr(common, "_find_windows_bin_dirs", lambda: [tmp_path])

    rvl_exe = tmp_path / "rvl.exe"
    rvl_exe.write_text("old_content")

    # 1. Prepare: target should be moved aside to backup
    moved = common._prepare_windows_self_update()
    assert len(moved) == 1
    target, backup = moved[0]
    assert not target.exists()
    assert backup.exists()

    # 2. Finish with failure (rollback): target should be restored
    common._finish_windows_self_update(moved, success=False)
    assert target.exists()
    assert target.read_text() == "old_content"

