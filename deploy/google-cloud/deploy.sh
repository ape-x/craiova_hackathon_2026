#!/usr/bin/env bash
# Run on the VM after restore and private env installation. No secret is echoed.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# == 3 || $# == 4 ]] || { echo 'Usage: deploy.sh RELEASE_DIRECTORY REVISION API_DOMAIN [HTTPS_FRONTEND_ORIGIN]' >&2; exit 1; }
release=$(cd -- "$1" && pwd)
revision=$2
domain=$3
frontend=${4:-https://craiova-hackathon-2026.vercel.app}
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected full reviewed git revision.' >&2; exit 1; }
[[ $domain =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && $domain == *.* && $domain != *..* ]] || { echo 'Expected DNS hostname only.' >&2; exit 1; }
[[ $frontend =~ ^https://[a-z0-9][a-z0-9.-]*[a-z0-9]$ && $frontend != *..* ]] || { echo 'Expected exact HTTPS frontend origin without port, path or wildcard.' >&2; exit 1; }
[[ -f $release/REVISION && $(cat "$release/REVISION") == "$revision" ]] || { echo 'Release REVISION marker mismatch.' >&2; exit 1; }
here=$release/deploy/google-cloud
python3 "$here/install_release.py" verify "$release" "$revision"
runtime=/opt/airport-recovery/runtime
private_env=/opt/airport-recovery/secrets/backend.env
mountpoint -q /mnt/airport-recovery || { echo 'Persistent data disk is not mounted.' >&2; exit 1; }
setpriv --reuid=10001 --regid=10001 --clear-groups python3 "$here/persistence_guard.py" quiescent /mnt/airport-recovery/data/recovery.sqlite3
python3 "$here/persistence_guard.py" environment "$private_env"
image=airport-recovery-api:$revision
if docker image inspect "$image" >/dev/null 2>&1; then
  [[ $(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image") == "$revision" ]] || { echo 'Existing image has an unexpected revision label.' >&2; exit 1; }
else
  python3 "$here/install_release.py" verify "$release" "$revision"
  docker build --label "org.opencontainers.image.revision=$revision" -t "$image" -f "$release/backend/Dockerfile" "$release"
fi
install -d -m 0755 "$runtime"
stage=$(mktemp -d /opt/airport-recovery/.runtime-check.XXXXXX)
trap 'rm -rf -- "$stage"' EXIT
install -m 0644 "$here/compose.yaml" "$here/Caddyfile" "$here/persistence_guard.py" "$stage/"
umask 077
cat >"$stage/deployment.env" <<EOF
API_IMAGE=$image
API_DOMAIN=$domain
FRONTEND_HOST=${frontend#https://}
FRONTEND_ORIGIN=$frontend
BACKEND_ENV_FILE=$private_env
EOF
# -q validates without printing expanded secret environment values.
docker compose --env-file "$stage/deployment.env" -f "$stage/compose.yaml" config --quiet
install -m 0644 "$stage/compose.yaml" "$stage/Caddyfile" "$stage/persistence_guard.py" "$runtime/"
install -m 0600 "$stage/deployment.env" "$runtime/deployment.env"
cat >/etc/systemd/system/airport-recovery.service <<'EOF'
[Unit]
Description=Airport Recovery API with persistent state
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=/mnt/airport-recovery

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/airport-recovery/runtime
ExecStartPre=/usr/bin/mountpoint -q /mnt/airport-recovery
ExecStartPre=/usr/bin/python3 /opt/airport-recovery/runtime/persistence_guard.py environment /opt/airport-recovery/secrets/backend.env
ExecStartPre=/usr/bin/setpriv --reuid=10001 --regid=10001 --clear-groups /usr/bin/python3 /opt/airport-recovery/runtime/persistence_guard.py database /mnt/airport-recovery/data/recovery.sqlite3
ExecStart=/usr/bin/docker compose --env-file /opt/airport-recovery/runtime/deployment.env -f /opt/airport-recovery/runtime/compose.yaml up -d --no-build --wait
ExecStop=/usr/bin/docker compose --env-file /opt/airport-recovery/runtime/deployment.env -f /opt/airport-recovery/runtime/compose.yaml stop
TimeoutStartSec=300
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable airport-recovery.service
setpriv --reuid=10001 --regid=10001 --clear-groups python3 "$here/persistence_guard.py" quiescent /mnt/airport-recovery/data/recovery.sqlite3
systemctl restart airport-recovery.service
echo 'API deployment started. Verify HTTPS readiness and unauthenticated world rejection before Vercel cutover.'
