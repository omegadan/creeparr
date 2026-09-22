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

DOWNLOAD_ROOT=${CREEPARR_DOWNLOAD_DIR:-/downloads}
mkdir -p /config/logs /config/cookies "$DOWNLOAD_ROOT" || true

# Per-provider archive roots are optional. If one is configured but its
# directory does not exist and is not inside any existing directory, nothing was
# mounted there (compose / Unraid create the mount point when a host path is
# given). Writing into the container's own filesystem would lose the archive on
# the next recreate, so unset it and let that provider use the default root.
DOWNLOAD_DIRS="$DOWNLOAD_ROOT"
for provider in PATREON ONLYFANS YOUTUBE INSTAGRAM REDDIT; do
    var="CREEPARR_${provider}_DOWNLOAD_DIR"
    dir="${!var:-}"
    [ -z "$dir" ] && continue
    if [ ! -d "$dir" ]; then
        ancestor="$dir"
        while [ ! -d "$ancestor" ]; do ancestor=$(dirname "$ancestor"); done
        if [ "$ancestor" = "/" ]; then
            echo "creeparr: $var=$dir is not mounted; ${provider,,} downloads will use $DOWNLOAD_ROOT"
            unset "$var"
            continue
        fi
        mkdir -p "$dir" || true
    fi
    DOWNLOAD_DIRS="$DOWNLOAD_DIRS $dir"
done

# Best effort: on some shares (Unraid /mnt/user, NFS) chown can fail for individual
# files; the app reports clearly if it cannot write, so do not abort startup here.
if ! chown -R abc:abc /config 2>/dev/null; then
    echo "creeparr: warning: could not change ownership of everything under /config"
fi
# Never chown the media trees recursively: they may be huge and belong to other apps.
for d in $DOWNLOAD_DIRS; do
    chown abc:abc "$d" 2>/dev/null || true
done
if ! gosu abc test -w /config; then
    echo "creeparr: ERROR: /config is not writable by uid $PUID gid $PGID. Fix the host folder's permissions or PUID/PGID." >&2
    exit 1
fi
for d in $DOWNLOAD_DIRS; do
    if ! gosu abc test -w "$d"; then
        echo "creeparr: warning: $d is not writable by uid $PUID gid $PGID; downloads will pause until fixed" >&2
    fi
done

echo "creeparr: running as uid=$(id -u abc) gid=$(id -g abc), tz=${TZ:-UTC}"
exec gosu abc "$@"
