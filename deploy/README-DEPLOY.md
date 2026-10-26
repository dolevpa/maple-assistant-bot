# Deploying to a fresh Ubuntu 24.04 VPS

The bot runs as the non-root user `maplebot` under systemd (`maplestory-bot.service`, `Restart=always`, `RestartSec=5`),
code in `/opt/maplestory-bot`, secrets in `/etc/maplestory-bot.env` (root-owned, mode 600). The bot opens no
inbound ports (it connects out to Discord and xAI), so the firewall only allows SSH.

## 1. Build the package (on your workstation)

```bash
cd maplestory-bot && ./deploy/build-tarball.sh     # -> ../maplestory-bot-deploy.tar.gz
```
The tarball excludes `.venv`, `.env`, `cache/`, `bot.log`, `bot.pid`, `.git` — no secrets.

## 2. Copy and install (on the server)

```bash
scp maplestory-bot-deploy.tar.gz user@SERVER:/tmp/
ssh user@SERVER
tar -xzf /tmp/maplestory-bot-deploy.tar.gz -C /tmp
sudo /tmp/maplestory-bot/deploy/setup.sh
```
`setup.sh` is idempotent. It installs python3-venv/pip, creates the `maplebot` user, copies code to
`/opt/maplestory-bot`, builds the venv, installs `requirements.txt`, installs/enables the systemd unit, enables
`ufw` (deny incoming, allow SSH on the port sshd uses) and unattended-upgrades.

> Check you can SSH in on the standard port (or that sshd's configured port is right) before closing your session —
> ufw is enabled by the script.

## 3. Add secrets

On first run the script creates `/etc/maplestory-bot.env` with empty placeholders and does **not** start the bot.
```bash
sudo nano /etc/maplestory-bot.env      # set DISCORD_BOT_TOKEN= and XAI_API_KEY= (no quotes)
sudo systemctl start maplestory-bot
```
(Or re-run `sudo /tmp/maplestory-bot/deploy/setup.sh` — it starts the service once the token is present.)
Keep permissions: `sudo chmod 600 /etc/maplestory-bot.env && sudo chown root:root /etc/maplestory-bot.env`.
The bot reads variables from the process environment first (supplied by `EnvironmentFile`), then from a `.env` file
if one exists; on the server the env file is the source of truth. After changing it: `sudo systemctl restart maplestory-bot`.

## 4. Verify

```bash
systemctl status maplestory-bot
journalctl -u maplestory-bot -n 50 --no-pager     # look for "LOGIN OK: logged in as ..."
```

## Update the code

On the workstation rebuild the tarball, copy it over, then on the server:
```bash
rm -rf /tmp/maplestory-bot && tar -xzf /tmp/maplestory-bot-deploy.tar.gz -C /tmp
sudo /tmp/maplestory-bot/deploy/setup.sh      # rsyncs code, updates deps, restarts the service
```
The server's `cache/`, venv and `/etc/maplestory-bot.env` are preserved.

## Logs

```bash
journalctl -u maplestory-bot -f                 # follow
journalctl -u maplestory-bot --since "1 hour ago"
journalctl -u maplestory-bot -p err
```

## Restart / stop

```bash
sudo systemctl restart maplestory-bot
sudo systemctl stop maplestory-bot
sudo systemctl disable --now maplestory-bot     # stop and don't start on boot
```

## Troubleshooting

- Service flapping / "improper token": check `DISCORD_BOT_TOKEN` in `/etc/maplestory-bot.env`.
- `/ask` says the AI key isn't configured: set `XAI_API_KEY`, then restart (a `.env` file would be re-read live; the systemd env is read at start).
- Firewall: `sudo ufw status verbose`.
- Make sure the same Discord token isn't running elsewhere (e.g. your local machine) at the same time.
