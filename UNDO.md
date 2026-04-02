# Undoing the always-on setup

Stop and remove the launchd agents:

```bash
cd /Users/michael/Repo/Agent && python3 install.py --uninstall
```

Restore normal sleep behavior on AC power:

```bash
sudo pmset -c sleep 10 disksleep 10
```
