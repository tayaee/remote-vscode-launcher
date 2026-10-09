# Troubleshooting

If `rvl` / `rvl-server` doesn't work, check in this order.
Items per case: Case 1 (LAN): [1], [2], [3], [5] / Case 2 (Cloud): [2], [3], [4], [5].

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
c:\> code --folder-uri "vscode-remote://ssh-remote+<linux-hostname>/home/<linux-login-id>/src/demo"
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
