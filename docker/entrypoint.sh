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

mkdir -p /config/logs /config/cookies /downloads
chown -R abc:abc /config
# Never chown the media tree recursively: it may be huge and belong to other apps.
chown abc:abc /downloads 2>/dev/null || true

echo "patreonarr: running as uid=$(id -u abc) gid=$(id -g abc), tz=${TZ:-UTC}"
exec gosu abc "$@"
