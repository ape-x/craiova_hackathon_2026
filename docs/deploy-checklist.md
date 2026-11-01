# Deployment checklist

Run commands from the repository root. Keep `.env`, `.env.production`, databases and backups private. Commands below validate configuration without printing resolved secrets.

This repository includes no provider credentials. Bounded real checks cover exact FR24 lookup, flight/hotel quotes, both UK APL transfer directions, Google geography/dated routes, ECB estimates and a complete ten-call hotel proposal; see the [verification report](verification/report.md). No supplier booking occurred. A new deployment must verify its own account access and coverage. Complete spoken recovery, physical browser microphone/playback, FR24 observation coverage and detailed airport geography remain separate acceptance checks.

## Local build, this computer only

```sh
docker compose config --quiet
docker compose up -d --build
curl --fail --show-error http://127.0.0.1:8080/api/ready
```

Open `http://localhost:8080`. The web port is bound to loopback; the API has no published port. The development script similarly binds `127.0.0.1:5173`. A phone cannot reach these localhost addresses, and a QR pointing to localhost refers to the phone itself. Use the HTTPS deployment below for passenger access from another device.

## Phone-accessible HTTPS deployment

1. Use a server whose public DNS hostname points to it. Allow inbound TCP 80/TCP 443; UDP 443 is optional HTTP/3. Docker Compose must support `env_file.required` ([v2.24 or newer](https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/#additional-information-1)).
2. Create the private configuration without overwriting an existing one:

```sh
test -f .env.production || cp .env.production.example .env.production
chmod 600 .env.production
```

3. Edit `APP_DOMAIN` to the real hostname, without a scheme or path. Set a random `OPERATOR_TOKEN` of at least 32 characters. Add authorized provider keys privately. The public URL, allowed hosts and CORS origin derive from this hostname. Restrict the browser Maps key to the intended HTTPS referrers and required Maps JavaScript API; confirm API enablement and billing in the owning account. Blank provider keys leave the corresponding live functions unavailable; they do not populate supply or substitute AI. Configure account budgets and alerts as appropriate; application timeouts and request limits are not an automatic account spending cap.
4. Validate and start:

```sh
docker compose --env-file .env.production -f compose.production.yaml config --quiet
docker compose --env-file .env.production -f compose.production.yaml up -d --build
docker compose --env-file .env.production -f compose.production.yaml ps
```

5. Substitute the actual hostname below. Readiness should return `ready`; the unauthenticated operator-world request should return 401:

```sh
recovery_domain=your-real-hostname.example
curl --fail --show-error "https://${recovery_domain}/api/ready"
curl --silent --output /dev/null --write-out '%{http_code}\n' "https://${recovery_domain}/api/world"
```

6. Open HTTPS on the operator computer, log in and create a source-backed proposal for an explicitly selected group. Scan its QR on the phone while the proposal is current. Accept or decline it and check that both screens show the same preference state. Acceptance must not show an external booking confirmation. Only separately recorded supplier references for every segment can confirm bookings for an actual manifest group; generated planning assumptions cannot receive such confirmations. The operator microphone requires a secure origin and permission; localhost is the only local HTTP exception.
7. Check only authorized provider operations within explicit call allowances. Start FR24 observations only when ready to consume the persistent budget; stopping and restarting the application does not replenish it. The UK hotel package requires ten job calls and saved-profile voice reuse eleven total calls. The separate Romanian coach/hotel package requires eight job calls or nine total for explicit saved-profile command reuse; default eight stays unchanged. Verify final voice-session closure independently of successful recognition or planning. Confirm Google geography and source-labelled routes using the deployment's accounts; do not expect live quotes to appear as tracked vehicles or passengers. Check account warnings, quotas and coverage separately. Configured keys and mock tests do not prove access or worldwide coach/transfer availability.

Keep the API private: Uvicorn trusts forwarded headers from its internal proxy network. Publishing port 8000 would invalidate that trust assumption. Increase the proxy's 120-second response-header timeout if raising the default two sequential 45-second provider-call limits. On an uncertain request result, fetch current state and retry the same idempotency key.

## State, shutdown and backup

Stop the matching deployment without deleting its volumes:

```sh
docker compose --env-file .env.production -f compose.production.yaml down
```

Do not add `-v` unless intentionally discarding the database and TLS state. Keep a tested operational backup before updates. For the separate local Python instance:

```sh
backend/.venv/bin/python scripts/backup.py backend/data/recovery.sqlite3 backups/recovery-snapshot.sqlite3
```

The Docker database is in `/data/recovery.sqlite3` inside the API's `recovery_data` volume; it is not the local file in that command. Use SQLite's backup API against that mounted volume, or stop the service before taking a volume snapshot. The backup script verifies integrity, refuses overwrite, applies mode 0600 and omits the transient aircraft-observation cache. Restore with the API stopped and preserve UID/GID 10001 ownership for container files. Previously made backups need their own access and retention policy.
