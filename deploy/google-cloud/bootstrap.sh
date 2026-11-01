#!/usr/bin/env bash
# VM startup only: installs prerequisites; never starts the application.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl python3 util-linux e2fsprogs
if ! docker compose version >/dev/null 2>&1; then
  . /etc/os-release
  [[ ${ID} == debian && ${VERSION_CODENAME} == bookworm ]] || { echo 'Expected Debian 12.' >&2; exit 1; }
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: bookworm
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker
install -d -m 0755 /opt/airport-recovery/releases /opt/airport-recovery/runtime
install -d -m 0700 /opt/airport-recovery/secrets /opt/airport-recovery/incoming
echo 'Prerequisites ready. No disk formatted, database created, or API started.'
