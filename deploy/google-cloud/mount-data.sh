#!/usr/bin/env bash
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# == 0 || ( $# == 1 && $1 == --initialize-empty-disk ) ]] || { echo 'Usage: mount-data.sh [--initialize-empty-disk]' >&2; exit 1; }
device=/dev/disk/by-id/google-airport-recovery-data
target=/mnt/airport-recovery
[[ -b $device ]] || { echo 'Expected separately attached airport-recovery-data disk.' >&2; exit 1; }
if mountpoint -q "$target"; then
  [[ $(findmnt -n -o MAJ:MIN --target "$target") == "$(lsblk -dn -o MAJ:MIN "$device")" ]] || { echo 'Unexpected device mounted at data path.' >&2; exit 1; }
else
  filesystem=$(blkid -o value -s TYPE "$device" || true)
  if [[ -z $filesystem ]]; then
    [[ ${1:-} == --initialize-empty-disk ]] || { echo 'Unformatted disk: explicit --initialize-empty-disk required.' >&2; exit 1; }
    [[ $(lsblk -nr -o NAME "$device" | wc -l) -eq 1 ]] || { echo 'Disk has partitions; refusing to format.' >&2; exit 1; }
    [[ -z $(wipefs --no-act --noheadings --output TYPE "$device") ]] || { echo 'Disk contains signatures; refusing to format.' >&2; exit 1; }
    mkfs.ext4 -L airport-recovery-data "$device"
    filesystem=ext4
  fi
  [[ $filesystem == ext4 ]] || { echo 'Expected ext4; no conversion or reformat performed.' >&2; exit 1; }
  uuid=$(blkid -o value -s UUID "$device")
  [[ $uuid =~ ^[a-fA-F0-9-]+$ ]] || { echo 'Invalid filesystem UUID.' >&2; exit 1; }
  install -d -m 0755 "$target"
  if awk -v path="$target" '$2 == path {found=1} END {exit !found}' /etc/fstab; then
    awk -v path="$target" -v source="UUID=$uuid" '$2 == path && $1 != source {bad=1} END {exit bad}' /etc/fstab || { echo 'Conflicting fstab entry.' >&2; exit 1; }
  else
    printf 'UUID=%s %s ext4 defaults,nofail 0 2\n' "$uuid" "$target" >>/etc/fstab
  fi
  mount "$target"
fi
# Root can traverse; the API's non-root uid owns only its database directory.
install -d -o 10001 -g 10001 -m 0700 "$target/data"
install -d -m 0700 "$target/caddy-data" "$target/caddy-config"
echo 'Persistent disk mounted. No database created or overwritten.'
