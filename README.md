# remote-vscode-launcher (rvl)

Want to run VS Code for a repository right from your terminal SSH session? Yes, you can do it with `rvl-server` and `rvl`.

## Install

Requires [uv](https://docs.astral.sh/uv/) on both Windows and Linux.

**Install rvl-server on Windows:**
```
c:\> uv tool install --from git+https://github.com/tayaee/remote-vscode-launcher.git --force remote-vscode-launcher
c:\> rvl-server --version
```

**Install rvl client on Linux:**
```
$ uv tool install --from git+https://github.com/tayaee/remote-vscode-launcher.git --force remote-vscode-launcher
$ rvl --version
```

## SSH config (Windows side)

Register the Linux hostname (the outcome of `hostname` command) in `%USERPROFILE%\.ssh\config`. See the [OpenBSD ssh_config manual](https://man.openbsd.org/ssh_config) and [VS Code Remote-SSH documentation](https://code.visualstudio.com/docs/remote/ssh) for syntax.

## Case 1: Local LAN (port 8259 directly reachable)

Same office / home LAN, where the Linux box can reach `http://<Windows-IP>:8259` directly. No reverse tunnel needed.

**SSH config:**
```
Host <linux-hostname>
    HostName <linux-hostname-or-ip>
    User <linux-login-id>
```
Check the SSH connection with `ssh <linux-hostname>`.

**Windows:**
```
c:\> rvl-server
c:\> ssh <linux-hostname>
```

**Then, from the Linux SSH session:**
```
$ rvl
```
Expect: VS Code launches on Windows with the Linux project loaded.

Not working? See [Troubleshooting](Troubleshooting.md).

## Case 2: Home -> Cloud (port 8259 unreachable, reverse tunnel required)

Cloud VM (OCI / AWS / etc.) cannot dial back to your home Windows PC (NAT / firewall). Open a reverse tunnel from Windows so the cloud box can reach `rvl-server` via localhost.

**SSH config:**
```
Host <linux-hostname>
    HostName <linux-hostname-or-ip>
    User <linux-login-id>
    IdentityFile "<path\to\private-key-file>"
    RemoteForward 8259 127.0.0.1:8259
```
Check the SSH connection with `ssh <linux-hostname>`.

**Windows:**
```
c:\> rvl-server
c:\> ssh <linux-hostname>
```

**Linux (cloud, inside the tunneled SSH session):**
```
$ rvl
```
Expect: VS Code launches on Windows with the Linux project loaded.

Not working? See [Troubleshooting](Troubleshooting.md).
`rvl` tries `<Windows-IP>:8259` first (auto-detected from the SSH connection) and falls back to `127.0.0.1:8259` via the reverse tunnel when the direct route fails.

## Troubleshooting

See [Troubleshooting.md](Troubleshooting.md).
