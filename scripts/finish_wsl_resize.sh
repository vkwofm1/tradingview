#!/usr/bin/env bash
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo 'Requires root inside the target WSL distribution.' >&2; exit 1; }
target_bytes=$((406 * 1024 * 1024 * 1024))
root_device=$(findmnt -n -o SOURCE /)
[[ $(findmnt -n -o FSTYPE /) == ext4 && -b "$root_device" ]] || {
    echo 'Expected an ext4 root block device.' >&2; exit 1;
}
[[ $(blockdev --getsize64 "$root_device") -eq $target_bytes ]] || {
    echo 'Root disk is not the approved 406GiB target.' >&2; exit 1;
}
[[ ,$(findmnt -n -o OPTIONS /), == *,rw,* ]] || {
    echo 'Root filesystem is not read-write; no automatic repair.' >&2; exit 1;
}

resize2fs "$root_device"
filesystem_bytes=$(df -B1 --output=size / | tail -n 1 | tr -d ' ')
[[ $filesystem_bytes -gt $((target_bytes * 95 / 100)) ]] || {
    echo 'Filesystem size verification failed.' >&2; exit 1;
}
df -h /
df -B1 /
sync

# These host services were enabled before maintenance. Do not change trading permissions.
services=(docker.service containerd.service k3d-start.service port-forward.service)
systemctl start --no-block "${services[@]}"
deadline=$((SECONDS + 180))
until systemctl is-active --quiet "${services[0]}" &&
      systemctl is-active --quiet "${services[1]}" &&
      systemctl is-active --quiet "${services[2]}" &&
      systemctl is-active --quiet "${services[3]}"; do
    if (( SECONDS >= deadline )); then
        systemctl --no-pager --full status "${services[@]}" || true
        echo 'Disk expanded, but host service readiness could not be verified.' >&2
        exit 1
    fi
    sleep 3
done
systemctl is-active "${services[@]}"
