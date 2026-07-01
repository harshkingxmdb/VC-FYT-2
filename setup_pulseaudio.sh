#!/usr/bin/env bash
#
# One-time host setup for the VC-to-VC Audio Forwarding Userbot.
# Run this once on the machine (or in the Docker image) before starting
# main.py. It installs and boots the system audio bridge the bot relies
# on to move audio between the two Voice Chats.
#
set -euo pipefail

echo "[setup] Installing system dependencies (ffmpeg, pulseaudio, xvfb)..."
apt-get update -y
apt-get install -y --no-install-recommends \
    ffmpeg \
    pulseaudio \
    pulseaudio-utils \
    xvfb \
    x11-apps

echo "[setup] Starting PulseAudio in system-wide daemon mode..."
if ! pgrep -x pulseaudio >/dev/null 2>&1; then
    pulseaudio -D --exit-idle-time=-1 --system=false --disallow-exit
fi

echo "[setup] Starting a virtual X display for screen share (:1.0)..."
if ! pgrep -x Xvfb >/dev/null 2>&1; then
    Xvfb :1 -screen 0 1280x720x24 &
    export DISPLAY=:1
fi

echo "[setup] Done. PulseAudio and Xvfb are ready."
echo "[setup] Remember to export DISPLAY=:1 in the environment that runs main.py"
echo "        if you plan to use /screenshare."
