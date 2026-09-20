#!/bin/bash
# linuxserver-style entrypoint: run as PUID:PGID, own /config, set TZ.
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ -n "$TZ" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

if ! getent group abc >/dev/null; then
    groupadd -o -g "$PGID" abc
else
    groupmod -o -g "$PGID" abc
fi
if ! id abc >/dev/null 2>&1; then
    useradd -o -u "$PUID" -g abc -d /config -s /usr/sbin/nologin abc
else
    usermod -o -u "$PUID" abc
fi

mkdir -p /config/logs /config/cookies /downloads /downloads-onlyfans || true
# Best effort: on some shares (Unraid /mnt/user, NFS) chown can fail for individual
# files; the app reports clearly if it cannot write, so do not abort startup here.
if ! chown -R abc:abc /config 2>/dev/null; then
    echo "patrearr: warning: could not change ownership of everything under /config"
fi
# Never chown the media trees recursively: they may be huge and belong to other apps.
chown abc:abc /downloads 2>/dev/null || true
chown abc:abc /downloads-onlyfans 2>/dev/null || true
if ! gosu abc test -w /config; then
    echo "patrearr: ERROR: /config is not writable by uid $PUID gid $PGID. Fix the host folder's permissions or PUID/PGID." >&2
    exit 1
fi
for d in /downloads /downloads-onlyfans; do
    if ! gosu abc test -w "$d"; then
        echo "patrearr: warning: $d is not writable by uid $PUID gid $PGID; downloads will pause until fixed" >&2
    fi
done

echo "patrearr: running as uid=$(id -u abc) gid=$(id -g abc), tz=${TZ:-UTC}"
exec gosu abc "$@"
