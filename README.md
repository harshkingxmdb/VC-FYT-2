# Telegram VC-to-VC Audio Forwarding Userbot

Forwards live audio from one Telegram Voice Chat (the **Logger Group**)
into another Telegram Voice Chat (any **target group**), in real time,
with configurable volume and bass, controlled entirely through a Bot API
bot that only the owner can command.

## How it works

Telegram Voice Chats don't expose a public API for "raw decoded audio
of what's currently playing" — so the bridge is built the same way
every production tgcalls-based relay does it: through real system audio
devices.

```
Logger Group VC  --(assistant listens)-->  PulseAudio null-sink "vcrelay"
                                                  |
                                     ffmpeg reads vcrelay.monitor
                                     applies volume + bass filters
                                                  |
                                            named pipe (FIFO)
                                                  |
                                     assistant streams FIFO  -->  Target Group VC
```

1. The assistant account (`STRING_SESSION`) joins the **Logger Group**'s
   Voice Chat using the `vcrelay` PulseAudio sink as its audio output.
   Whatever the owner speaks there is physically rendered into that
   sink.
2. `ffmpeg` reads the sink's monitor source, applies the `/level` and
   `/bass` filters (plus a limiter so nothing clips), and writes raw
   PCM into a named pipe.
3. The assistant joins the **target group**'s Voice Chat, streaming
   that named pipe live — so the target hears the Logger Group in real
   time, at the configured volume/bass.
4. A watchdog task polls every session's health and automatically
   rejoins both Voice Chats if a disconnect is detected (network drop,
   kick, or the voice chat itself being closed and restarted).

Multiple target groups can be joined simultaneously (`/join <chat_id>`
multiple times); every session gets its own capture process, FIFOs, and
watchdog, all tracked in MongoDB so state survives restarts.

## Requirements

- Linux host (PulseAudio + `x11grab`/Xvfb for `/screenshare`)
- Python 3.10+
- FFmpeg
- PulseAudio (`pulseaudio`, `pulseaudio-utils`)
- Xvfb + x11-apps (only needed for `/screenshare`)
- MongoDB instance

## Setup

```bash
git clone <this repo>
cd vcfight
pip install -r requirements.txt
cp .env.example .env      # fill in your values

# Don't have a STRING_SESSION yet? Generate one:
python3 gen_string.py

# One-time host setup (installs/starts ffmpeg, pulseaudio, xvfb)
sudo ./setup_pulseaudio.sh

python3 start.py
```

`start.py` prints a startup banner, runs `validate_startup.py`
automatically, then boots the bot — a missing env var or a missing
system binary (ffmpeg/pulseaudio) fails fast with a clear message
instead of crashing three layers deep.

Or with Docker:

```bash
docker build -t vcfight .
docker run --env-file .env vcfight
```

Or deploy directly to Heroku using `app.json` / `Procfile` / `packages.txt`
/ `runtime.txt` (the apt buildpack installs ffmpeg/pulseaudio/xvfb from
`packages.txt` automatically).

## Environment variables

| Variable          | Description                                              |
|--------------------|-----------------------------------------------------------|
| `API_ID`           | my.telegram.org API ID                                    |
| `API_HASH`         | my.telegram.org API hash                                  |
| `BOT_TOKEN`        | BotFather token for the command-and-control bot            |
| `OWNER_ID`         | Telegram user ID allowed to issue commands                |
| `STRING_SESSION`   | Pyrogram string session for the assistant account          |
| `RECORD_GROUP`     | Group used to archive/upload recordings                   |
| `LOGGER_GROUP`     | Group whose Voice Chat is the live audio source            |
| `MONGO_URI`        | MongoDB connection string                                  |
| `DB_NAME`          | MongoDB database name                                     |
| `PULSE_SINK_NAME`  | Optional, defaults to `vcrelay`                            |
| `WORK_DIR`         | Optional, defaults to `/tmp/vc_forward_bot`                |
| `SCREEN_SHARE_DEVICE` | Optional, defaults to `:1.0` (Xvfb display)             |

## Commands (owner only)

**Setup**
- `/join <chat_id>` — join that chat's Voice Chat and start forwarding audio from the Logger Group.
- `/leave [chat_id]` — leave both Voice Chats for a session.
- `/leaveall` — leave every active session.
- `/leaveplay [chat_id]` — leave only the playback (target) Voice Chat.
- `/leaverecord` — leave only the Logger Group Voice Chat.

**Audio**
- `/level <1-25>` — set playback volume.
- `/bass <0-15>` — set bass boost.
- `/mute` / `/unmute` — mute/unmute without leaving.

**Screen share**
- `/screenshare` — start sharing the host's virtual display into the active Voice Chat.
- `/screenshareoff` — stop screen sharing, revert to audio-only.

**Recording**
- `/startrecord` — start recording the forwarded (post-filter) audio.
- `/stoprecord` — stop and upload the recording to the owner.

**Utilities**
- `/speedtest` — show current ping, download, and upload speed of the host.

## Project layout

```
vcfight/
├── start.py                  # <- run this. Banner + pre-flight checks + boot
├── main.py                   # core startup/shutdown logic, used by start.py
├── gen_string.py              # interactive STRING_SESSION generator
├── validate_startup.py        # pre-flight env var + dependency checks
├── config/
│   └── __init__.py            # environment variable loading/validation
├── core/
│   ├── db.py                  # MongoDB sessions & settings
│   ├── logger.py              # shared logging setup
│   ├── permissions.py         # owner-only filter
│   ├── pulse_audio.py         # virtual sink bridge management
│   ├── ffmpeg_utils.py        # filter graph + ffmpeg process helpers
│   ├── pipe_manager.py        # named pipe (FIFO) lifecycle
│   └── call_manager.py        # join/leave/reconnect/screenshare/record
├── plugins/
│   ├── join_leave.py
│   ├── audio_controls.py
│   ├── screenshare.py
│   ├── recording.py
│   └── utility.py
├── deploy/
│   └── vcfight.service        # systemd unit for 24/7 uptime (see below)
├── .github/workflows/ci.yml   # syntax/lint check on push
├── Dockerfile / setup_pulseaudio.sh
├── Procfile / app.json / packages.txt / runtime.txt   # Heroku-style deploy
└── requirements.txt
```

## VPS Deployment Guide (A to Z)

This walks through taking a brand-new Ubuntu 22.04 VPS to a bot running
24/7. Any provider works, but **pick one that doesn't block/throttle
UDP traffic** — Telegram Voice Chats run over WebRTC (UDP); providers
that filter UDP heavily will give you connect-then-silence behaviour
that looks like a code bug but isn't.

### 1. Get a VPS and log in

```bash
ssh root@your.server.ip
```

A 1-2 vCPU / 2 GB RAM box is enough for a handful of concurrent
sessions (each session runs 2-3 ffmpeg processes).

### 2. Create a dedicated user (don't run this as root)

```bash
adduser vcfight
usermod -aG sudo vcfight
su - vcfight
```

### 3. Update the system and install dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3-pip git \
    ffmpeg pulseaudio pulseaudio-utils xvfb x11-apps
```

If `python3.11` isn't available on your distro's default repos, use the
`deadsnakes` PPA:

```bash
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update && sudo apt install -y python3.11 python3.11-venv
```

### 4. Start PulseAudio for this user

```bash
pulseaudio --start
pactl info    # should print server info, not an error
```

### 5. Clone the repo and set up a virtualenv

```bash
cd ~
git clone <your-repo-url> vcfight
cd vcfight
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 6. Configure environment variables

```bash
cp .env.example .env
nano .env       # fill in API_ID, API_HASH, BOT_TOKEN, OWNER_ID, RECORD_GROUP,
                 # LOGGER_GROUP, MONGO_URI, DB_NAME
```

Generate `STRING_SESSION` (still inside the venv):

```bash
python3 gen_string.py
```

Copy the printed `STRING_SESSION=...` line into `.env`.

### 7. Set up MongoDB

Either install it locally on the VPS:

```bash
curl -fsSL https://pgp.mongodb.com/server-7.0.asc | sudo gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
echo "deb [signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
sudo apt update && sudo apt install -y mongodb-org
sudo systemctl enable --now mongod
```
then set `MONGO_URI=mongodb://localhost:27017` in `.env`,

or use a free MongoDB Atlas cluster and put its connection string in
`MONGO_URI` instead (simpler, no maintenance, works from any VPS).

### 8. Test it manually first

```bash
python3 start.py
```

You should see the startup banner, then "Startup complete. Listening
for owner commands." in the log, and a startup DM from the bot to
`OWNER_ID` on Telegram. Start a Voice Chat in both the Logger Group and
a target group, then from Telegram send `/join <target_chat_id>` to the
bot and confirm audio actually forwards. Once this works, `Ctrl+C` it
and move on to running it as a service.

### 9. Open the firewall for Telegram's WebRTC traffic

```bash
sudo ufw allow OpenSSH
sudo ufw allow out to any port 443,80 proto tcp
sudo ufw allow out 1024:65535/udp
sudo ufw enable
```

(Outbound UDP is what actually carries Voice Chat audio; if your
provider or firewall blocks it, joining will succeed but no audio will
flow either direction.)

## Running 24/7 (systemd, auto-restart on crash/reboot)

A ready-made unit file is in `deploy/vcfight.service`. It's a **user**
systemd service so it can talk to the `vcfight` user's own PulseAudio
session — a system-wide service would have no audio session to attach
to.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/vcfight.service ~/.config/systemd/user/vcfight.service
nano ~/.config/systemd/user/vcfight.service   # double-check the %h paths match your setup

systemctl --user daemon-reload
systemctl --user enable vcfight
systemctl --user start vcfight
```

Let it keep running after you log out / across reboots:

```bash
sudo loginctl enable-linger vcfight
```

Manage it:

```bash
systemctl --user status vcfight     # is it running?
systemctl --user restart vcfight    # after a git pull / config change
systemctl --user stop vcfight
journalctl --user -u vcfight -f     # live logs (Ctrl+C to exit)
```

`Restart=always` in the unit means systemd relaunches the bot
automatically if it crashes; the bot's own watchdog (see "How it
works" above) separately handles Voice Chat disconnects without a full
process restart.

### Updating the bot

```bash
cd ~/vcfight
source venv/bin/activate
git pull
pip install -r requirements.txt --upgrade
systemctl --user restart vcfight
```

### Alternative: quick testing without systemd

For a quick manual test (not recommended for real 24/7 use, since it
dies when your SSH session ends unless you detach it):

```bash
tmux new -s vcfight
source venv/bin/activate
python3 start.py
# Ctrl+B then D to detach; `tmux attach -t vcfight` to reattach
```

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/join` succeeds but no audio flows either way | Outbound UDP blocked by the VPS provider/firewall (see step 9) |
| `pactl: command not found` / bridge errors on startup | PulseAudio not installed, or not started for this user (`pulseaudio --start`) |
| "No active voice chat" error on `/join` | Start the Voice Chat inside that Telegram group first — the bot can't create one |
| Bot doesn't survive reboot | `loginctl enable-linger` wasn't run for the `vcfight` user |
| Choppy/robotic audio | VPS is CPU-starved; check `top` while a session is active, upgrade the plan if ffmpeg is maxing a core |
| `/screenshare` fails | Xvfb isn't running / `SCREEN_SHARE_DEVICE` doesn't match its display number |

## Notes

- Only `OWNER_ID` can issue commands; every other user's messages are
  silently ignored.
- All joins, leaves, reconnects, errors, startup, shutdown, and
  recording events are logged to `WORK_DIR/logs/bot.log` and stdout.
- If a target or the Logger Group has no active Voice Chat yet, start
  the Voice Chat in Telegram first — `/join` will report the failure
  clearly instead of silently doing nothing.
- **Honesty note on "working":** this code follows the documented
  pytgcalls API and the standard PulseAudio-bridge technique real
  VC-relay bots use, and every file compiles cleanly, but it has not
  been run against live Telegram servers in the environment that built
  it (no network access there). Test `/join` against a real Voice Chat
  after deploying, and watch `journalctl --user -u vcfight -f` — if a
  `pytgcalls` method name has shifted between versions, that log is
  where it'll show up, and it's usually a one-line fix in
  `core/call_manager.py`.

