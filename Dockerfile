FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        pulseaudio \
        pulseaudio-utils \
        xvfb \
        x11-apps \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DISPLAY=:1
ENV PULSE_SERVER=unix:/tmp/pulse-socket

CMD ["bash", "-c", "pulseaudio -D --exit-idle-time=-1 --disallow-exit --system=false && Xvfb :1 -screen 0 1280x720x24 & sleep 2 && python3 start.py"]
