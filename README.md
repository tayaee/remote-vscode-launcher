# remote-vscode-launcher (rvl)

Want to run VS Code for a repository right from your terminal SSH session? Yes, you can do it with `rvl-server` and `rvl`.

## Install

**Install rvl-server on Windows:**
```
c:\> powershell -ExecutionPolicy Bypass -c "iwr https://astral.sh/uv/install.ps1 -useb | iex"
c:\> uv --version
c:\> uv tool install --from git+https://github.com/tayaee/remote-vscode-launcher.git --force remote-vscode-launcher
c:\> rvl-server --version
```

**Install rvl client on Linux:**
```
$ curl -LsSf https://astral.sh/uv/install.sh | sh
$ uv --version
$ uv tool install --from git+https://github.com/tayaee/remote-vscode-launcher.git --force remote-vscode-launcher
$ rvl --version
```

## SSH config (Windows side)

Register the Linux hostname (the outcome of `hostname` command) in `%USERPROFILE%\.ssh\config`. See the [OpenBSD ssh_config manual](https://man.openbsd.org/ssh_config) and [VS Code Remote-SSH documentation](https://code.visualstudio.com/docs/remote/ssh) for syntax.

**For LAN connection:**
```
Host <linux-hostname>
    HostName <linux-ip>
    User <linux-login-id>    
    RemoteForward 8259 127.0.0.1:8259
```
Check the SSH connection with `ssh <linux-hostname>`.

**For Cloud (AWS, Oracle Cloud) connection:**
```
Host <linux-hostname>
    HostName <linux-ip>
    User <linux-login-id>
    IdentityFile "<path\to\private-key-file>"
    RemoteForward 8259 127.0.0.1:8259
```
Check the SSH connection with `ssh -i "<path\to\private-key-file>" <linux-hostname>`.

## Use case 1: Local LAN (port 8259 directly reachable)

Same office / home LAN, where the Linux box can reach `http://<Windows IP>:8259` directly. No reverse tunnel needed.

**Windows:**
```
c:\> rvl-server
```

**Then, from the Linux SSH session:**
```
$ rvl
```

Not working? See [1], [2], [3], [5] below.

## Use case 2: Home -> Cloud (port 8259 unreachable, reverse tunnel required)

Cloud VM (OCI / AWS / etc.) cannot dial back to your home Windows PC (NAT / firewall). Open a reverse tunnel from Windows so the cloud box can reach `rvl-server` via localhost.

**Windows:**
```
c:\> rvl-server
c:\> ssh <linux-hostname>
```

**Linux (cloud, inside the tunneled SSH session):**
```
$ rvl
```

Not working? See [2], [3], [4], [5] below.
`rvl` tries `<Windows-IP>:8259` first and falls back to `127.0.0.1:8259` when it is listening.

## Troubleshooting

### [1] Linux -> Windows: is rvl-server reachable?

```bash
Linux $ nc -zv -w 3 <Windows-IP> 8259
# expect: Connection to <Windows-IP> 8259 port [tcp/*] succeeded!
# fail -> rvl-server not running / Windows firewall / wrong IP
```

### [2] Windows -> Linux: does SSH work without a password?

```powershell
c:\> ssh <linux-hostname> "echo ssh-ok"
# expect: ssh-ok (no password prompt)
# fail -> HostName / User / IdentityFile / key registration wrong
```

For scripting / BatchMode check (fails instead of prompting):

```powershell
c:\> ssh -o BatchMode=yes <linux-hostname> "echo ssh-ok"
```

### [3] Windows -> Linux: does VS Code Remote-SSH open?

```powershell
c:\> code --folder-uri "vscode-remote://ssh-remote+<linux-hostname>/home/user1/src/demo"
# expect: a VS Code window opens on that folder (close it after the test)
# fail -> Host alias / User / Remote-SSH extension wrong
```

### [4] Linux (cloud): is the reverse tunnel bound?

```bash
$ ss -tnlp4 | grep 8259
# expect: LISTEN 0 ... 127.0.0.1:8259 ...
# empty -> tunnel not up (check the `ssh` session on Windows for RemoteForward)
```

### [5] SSH host alias mismatch?

`rvl` picks the alias in this order: `--ssh-host` > `$RVL_SSH_HOST` > local hostname. If the Linux hostname is not the alias, pass it explicitly:

```
Linux $ rvl --ssh-host <linux-hostname>
```
