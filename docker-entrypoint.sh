#!/bin/sh
set -eu

vnc_password="${JOBPARSER_VNC_PASSWORD:-}"
if [ "${#vnc_password}" -lt 8 ]; then
    echo "JOBPARSER_VNC_PASSWORD must contain at least 8 characters" >&2
    exit 1
fi

export DISPLAY="${DISPLAY:-:99}"
password_file="/tmp/jobparser-vnc.pass"
umask 077
x11vnc -storepasswd "$vnc_password" "$password_file" >/dev/null
vnc_password=""
unset JOBPARSER_VNC_PASSWORD

Xvfb "$DISPLAY" -screen 0 1440x1000x24 -nolisten tcp -ac &
xvfb_pid=$!

attempt=0
while [ ! -S "/tmp/.X11-unix/X${DISPLAY#:}" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 50 ] || ! kill -0 "$xvfb_pid" 2>/dev/null; then
        echo "Virtual display failed to start" >&2
        exit 1
    fi
    sleep 0.1
done

openbox >/tmp/openbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -localhost -rfbport 5900 \
    -rfbauth "$password_file" -noxdamage >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 \
    >/tmp/novnc.log 2>&1 &

exec python webapp.py
