# Monitoring

**Check agent status:**
```bash
launchctl list | grep elwin
```
PID present = running. Exit code 0 = last run clean, 1 = last run failed.

**Server health (process + memory + API status):**
```bash
./health.sh
```

**Watch server logs live:**
```bash
tail -f /tmp/com.elwin.server.log
```

**Watch bot logs live:**
```bash
tail -f /tmp/com.elwin.bot.log
```

**Detailed llama-server logs** (model loading, inference errors):
```bash
tail -f logs/llama-server-$(ls -t logs/ | head -1)
```
