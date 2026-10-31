#!/bin/sh
set -e
# Virtueel scherm in gsm-formaat voor de zichtbare browser (Turnstile ziet een echte browser).
rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 480x1000x24 -nolisten tcp &
sleep 1
# VNC luistert alleen intern; je bereikt het via de app, achter je Home Assistant-login.
x11vnc -display :99 -rfbport 5900 -localhost -nopw -forever -shared -quiet -bg -o /tmp/x11vnc.log
export INGRESS_ONLY=1
exec python3 /app/server.py
