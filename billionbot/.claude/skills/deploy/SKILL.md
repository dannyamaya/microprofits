# /deploy — Deploy billionbot to a remote server

Syncs the billionbot project to a target server, installs dependencies, runs a smoke test, and ensures the cron job is registered.

## Targets

| Alias | Host | User | Notes |
|---|---|---|---|
| `gapper` | `100.101.111.35` | `ubuntu` | AWS Lightsail, Tailscale SSH — requires `tailscale status` up |
| `legion` | `192.168.86.34` | `ivan` | Local Linux box, LAN only |

Arguments passed: `<target>`

If no target given, default to `gapper`.

---

## Steps

### 1. Resolve target

Parse `<target>` against the table above. If unrecognized, treat it as a raw `user@host` string.

For `gapper`: verify Tailscale is connected first:
```bash
tailscale status
```
If logged out, tell the user to run `! tailscale up` and stop.

### 2. Rsync project

```bash
rsync -av --exclude='.git' --exclude='data/' --exclude='__pycache__' --exclude='.venv' \
  /Users/ivan/billionbot/ <user>@<host>:~/billionbot/
```

### 3. Install / update dependencies

```bash
ssh <user>@<host> "cd ~/billionbot && ~/.local/bin/uv sync"
```

If `uv` is not found, install it first:
```bash
ssh <user>@<host> "curl -LsSf https://astral.sh/uv/install.sh | sh"
```
Then retry `uv sync`.

### 4. Smoke test

```bash
ssh <user>@<host> "cd ~/billionbot && ~/.local/bin/uv run python x_scraper.py 2>&1 | tail -3"
```

Check that output contains tweet content (not an error/traceback). If it fails, report the error and stop.

### 5. Ensure cron job

Check if the scraper cron is registered:
```bash
ssh <user>@<host> "crontab -l 2>/dev/null | grep x_scraper"
```

If missing, add it:
```bash
ssh <user>@<host> '(crontab -l 2>/dev/null; echo "*/30 * * * * cd /home/<user>/billionbot && /home/<user>/.local/bin/uv run python x_scraper.py >> /home/<user>/billionbot/scraper.log 2>&1") | crontab -'
```

### 6. Report

Print a summary:
- Target host
- Files synced (rsync output summary)
- Dep install result
- Smoke test: pass/fail + last line of output
- Cron: already registered / newly added

---

## Notes

- Never commit credentials or PRIVATE.md files — the rsync excludes nothing sensitive by default but the project `.gitignore` covers it.
- If the user says "deploy" from Discord, treat it as `/deploy gapper` unless they specify otherwise.
- For the `gapper` server: billionbot lives at `~/billionbot/` (separate from `/opt/thegapper/` which is a different project — do not touch it).
