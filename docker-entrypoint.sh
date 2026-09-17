#!/bin/sh
# Container entrypoint. Starts as root only long enough to make the data directory writable for the
# unprivileged "mtr" user, then drops privileges. mtr-packet and ping carry the cap_net_raw file
# capability (set in the Dockerfile), so raw-socket probing keeps working without root.
#
# MTR_TRACKER_RUN_AS_ROOT=1 keeps the process as root, which is only needed for mtr probe intervals
# below one second (mtr refuses them for non-root users).
set -e

DATA_DIR="${MTR_TRACKER_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ] && [ "${MTR_TRACKER_RUN_AS_ROOT:-0}" != "1" ]; then
    if getcap /usr/bin/mtr-packet 2>/dev/null | grep -q cap_net_raw; then
        mkdir -p "$DATA_DIR"
        chown -R mtr:mtr "$DATA_DIR"
        exec setpriv --reuid=mtr --regid=mtr --init-groups "$@"
    fi
    echo "mtr-packet has no cap_net_raw file capability; staying root so probes keep working" >&2
fi

exec "$@"
