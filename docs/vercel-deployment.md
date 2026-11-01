# Vercel frontend and Google Cloud backend

This deployment keeps the existing single FastAPI process, its recovery worker,
voice watchdogs and transactional SQLite database together. Vercel serves the
frontend and forwards `/api/*` to the backend over HTTPS. It does not run a second
copy of the API or initialize a new operation.

The scripts below are prepared infrastructure, not evidence that cloud resources
or a deployment already exist. Running `provision.sh --apply` creates billed
resources in the explicitly authorized project `hale-tracer-286910`:

| Resource | Configuration |
| --- | --- |
| VM | `airport-recovery`, `e2-small`, Debian 12, `europe-west3-a` |
| Boot disk | 20 GB `pd-balanced` |
| Separate state disk | `airport-recovery-data`, 20 GB `pd-balanced`, no automatic deletion |
| Address | Regional reserved public IPv4 in `europe-west3` |
| Network | Dedicated `airport-recovery` VPC/subnet, no default-network rules |
| Ingress | TCP 80/443; SSH only through Google's IAP range |
| Backend | One API container plus HTTPS Caddy; API port 8000 is not published |

VM deletion protection is enabled. VM, disks, IPv4 and network egress may incur
charges; there is no automatic spending cap in these scripts. OS Login and IAP
access must already be granted to the deploying operator. No runtime service
account is attached, and the scripts do not modify IAM or billing.

## 1. Provision without starting an application

From an authenticated official Google Cloud CLI on the operator's computer:

```sh
bash deploy/google-cloud/provision.sh --apply
gcloud compute ssh airport-recovery --project=hale-tracer-286910 \
  --zone=europe-west3-a --tunnel-through-iap
```

The script always supplies the project explicitly. Existing named resources are
retained, not deleted or repurposed. Before reusing pre-existing resources,
inspect their machine/disk/network/firewall settings; the script does not silently
reconcile drift. A partial provisioning failure can be retried after its cause is
resolved. Bootstrap installs Docker from its official Debian repository and
Python. It never formats a disk, seeds a database, or starts the API.

Point the chosen API hostname at the printed reserved IP. A dedicated DNS name is
preferred. If needed for the temporary deployment, an IP-derived hostname such as
`203.0.113.10.sslip.io` can resolve without a separately owned zone; replace the
example IP with the actual address and verify DNS/TLS availability. Shared-domain
certificate limits and that DNS service remain external dependencies. Do not use
an IP-literal HTTP endpoint for production.

## 2. Transfer an exact reviewed release and private inputs

Do not archive the unreviewed dirty workspace. Commit the reviewed infrastructure
files on top of application revision `69b252a`; keep unrelated uncommitted work
excluded. Use that resulting full commit for both frontend and backend. Make an
exact source archive outside the repository:

```sh
REVISION=FULL_REVIEWED_INFRA_COMMIT
PRIVATE_DIR=$(mktemp -d)
chmod 700 "$PRIVATE_DIR"
git archive --format=tar "$REVISION" >"$PRIVATE_DIR/release.tar"
git show "$REVISION:deploy/google-cloud/install_release.py" >"$PRIVATE_DIR/install_release.py"
printf '%s\n' "$REVISION" >"$PRIVATE_DIR/REVISION"
chmod 600 "$PRIVATE_DIR/release.tar" "$PRIVATE_DIR/REVISION" "$PRIVATE_DIR/install_release.py"
shasum -a 256 "$PRIVATE_DIR/release.tar"
gcloud compute scp "$PRIVATE_DIR/release.tar" "$PRIVATE_DIR/REVISION" "$PRIVATE_DIR/install_release.py" \
  airport-recovery: --project=hale-tracer-286910 --zone=europe-west3-a \
  --tunnel-through-iap
```

On the VM, use the archive checksum printed on the trusted operator's computer.
The installer requires a new directory, checks the archive, rejects links and
path traversal, and records exact file checksums and permissions. It never
extracts over an existing release:

```sh
REVISION=$(cat ~/REVISION)
sudo python3 ~/install_release.py install ~/release.tar "$REVISION" ACTUAL_ARCHIVE_SHA256
sudo bash "/opt/airport-recovery/releases/$REVISION/deploy/google-cloud/mount-data.sh" \
  --initialize-empty-disk
```

`--initialize-empty-disk` formats only the dedicated, unpartitioned disk when it
has no detected filesystem/signature. Existing ext4 data is mounted unchanged.
Later calls omit that flag. The UUID is retained in `/etc/fstab`.

Prepare the private runtime file from the current backend settings, with
[`backend.env.example`](../deploy/google-cloud/backend.env.example) as a checklist.
Keep all current provider environment selections, models and request/credit
limits. The operator token must be a literal URL-safe value of at least 32
characters, without quotes or variable expansion. Do not put secrets in source
archives, shell command arguments, startup metadata, Vercel frontend variables or
chat. Upload the file through SSH/SCP and install it as:

```sh
sudo install -m 600 -o root -g root ~/backend.env \
  /opt/airport-recovery/secrets/backend.env
rm ~/backend.env
```

The deployment forces production mode, live operation, the mounted database and
the exact public hosts/origin. Provider keys remain backend-only. The Google
browser key is intentionally public through application config; add the final
Vercel origin to its authorized referrers without removing its API restrictions.

## 3. Preserve the current operation, then restore once

Pause FR24 in the existing operator interface, stop new submissions, and wait for
all recovery jobs and voice sessions to finish. Keep the old instance quiescent
through the migration. Record world version, provider counters, job history and
profile counts privately. Use the existing backup tool: it captures SQLite WAL
consistently, omits transient source caches, and preserves the request/usage
ledgers. It marks historical unfinished work for review rather than retrying it.

For the existing local Docker Compose installation, from the repository root:

```sh
docker compose exec -T api python - /data/recovery.sqlite3 \
  /tmp/vercel-cutover.sqlite3 <scripts/backup.py
docker compose cp api:/tmp/vercel-cutover.sqlite3 "$PRIVATE_DIR/recovery.sqlite3"
chmod 600 "$PRIVATE_DIR/recovery.sqlite3"
# After the successful final snapshot, prevent two independently writable copies.
docker compose stop api
shasum -a 256 "$PRIVATE_DIR/recovery.sqlite3"
gcloud compute scp "$PRIVATE_DIR/recovery.sqlite3" airport-recovery: \
  --project=hale-tracer-286910 --zone=europe-west3-a --tunnel-through-iap
```

Use a new snapshot filename if repeating backup; the tool refuses to overwrite.
The backup preserves operational state but intentionally expires transient
flight context/quotes. It does not turn old offers into fresh supply. Do not copy
only the live main SQLite file while ignoring its WAL.

On the VM, restore with the exact printed checksum:

```sh
sudo python3 "/opt/airport-recovery/releases/$REVISION/deploy/google-cloud/restore.py" \
  ~/recovery.sqlite3 ACTUAL_SHA256_FROM_THE_FINAL_SNAPSHOT
```

Restore verifies SQLite integrity, a live authoritative world, all 18 durable
tables in the committed application schema (including command intents, quote
receipts, cancellation keys, saved profiles and revision archives),
paused traffic and no active jobs. It retains the file byte-for-byte, including
usage, unknown outcomes, idempotency keys and saved profiles; it installs it with
UID/GID 10001 and mode 0600. Existing target databases or WAL sidecars are never
overwritten. Missing usage counters are an error, not permission to reset them.
The uploaded snapshot and source archive remain private migration artifacts;
remove staged home-directory copies once the verified backups are retained.

## 4. Start the restored backend

With DNS pointing at the reserved address and ports 80/443 reachable:

```sh
sudo bash "/opt/airport-recovery/releases/$REVISION/deploy/google-cloud/deploy.sh" \
  "/opt/airport-recovery/releases/$REVISION" "$REVISION" API_HOSTNAME \
  https://craiova-hackathon-2026.vercel.app
```

The optional fourth argument is the exact frontend origin, if the final hostname
differs. The script verifies the installed source manifest before building or
reusing an image labelled with that revision, validates
the private token and restored database, and installs a systemd unit. Caddy obtains
and renews TLS certificates. The database and Caddy state are on the separate
persistent disk. No application container starts against a missing database.
SQLite checks run as the API user to avoid root-owned WAL sidecars.

No new provider work is triggered by deploy: traffic must be paused and jobs
drained. Normal backend startup preserves persisted counters and request keys.
Check `/api/ready` over HTTPS, then verify an unauthenticated `/api/world` returns
401. Login through the final frontend and compare the restored state/counters.
Do not use paid searches as deployment health checks.

For updates, repeat the reviewed release transfer and `deploy.sh`; **do not
restore again**. Before replacing a running revision, drain work and take a fresh
backup. Previous images/releases remain available. A code rollback uses a previous
compatible image against the current database. Never restore an older database
over newer usage just to roll back code; that can revive consumed allowances.
If returning to the old host after accepting new writes, first stop the new host
and migrate a fresh current snapshot under the same no-overwrite procedure.

## 5. Vercel routing and production verification

Deploy the reviewed `frontend` Vite app (`npm ci`, `npm run build`, output `dist`).
Route `/api/:path*` to `https://API_HOSTNAME/api/:path*` before the SPA catch-all.
Keep the `/api` prefix. The browser must see the same Vercel origin for API calls:
existing auth cookies are HttpOnly, Secure in production, `SameSite=Strict`, and
scoped to `/api`. `PUBLIC_BASE_URL` is the frontend origin so passenger links open
the frontend, not the API-only hostname.

Explicitly disable API caching (`Cache-Control: no-store`, optionally Vercel's
`x-vercel-enable-rewrite-caching: 0`). Do not introduce caching for authenticated
world/profile/receipt reads. Port the existing frontend security headers from
`deploy/Caddyfile`, including the Google Maps and OpenAI connect policies. The
backend proxy's Caddyfile deliberately serves only `/api/*`.

Verify login/logout cookies, world reads, SSE reconnect and the 15-second polling
fallback, final Maps referrer, and ordinary request timeout behavior through the
Vercel rewrite. Commands can await up to two bounded model calls; the existing
browser POST timeout is 105 seconds and Caddy response-header timeout is 120
seconds. Test those boundaries offline or with harmless local test endpoints,
not extra paid model calls. Preview hosts are not implicitly authorized: use the
specific production origin unless a separately isolated preview is configured.

## Local infrastructure validation

```sh
for script in deploy/google-cloud/*.sh; do bash -n "$script"; done
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s deploy/google-cloud -p 'test_*.py' -v
```

`docker compose config --quiet` can validate the template with dummy values and a
temporary private env file. Do not print a fully expanded production Compose
configuration: it contains secrets. These checks neither provision resources nor
run the application.

Primary references: [Compute Engine instance creation](https://cloud.google.com/compute/docs/instances/create-start-instance),
[persistent disks](https://cloud.google.com/compute/docs/disks/add-persistent-disk),
[official Docker installation on Debian](https://docs.docker.com/engine/install/debian/),
and [Vercel external rewrites](https://vercel.com/docs/routing/rewrites).
