#!/usr/bin/env bash
# Explicit infrastructure creation; this script is never called by deploy.sh.
set -euo pipefail
[[ ${1:-} == --apply && $# == 1 ]] || { echo 'Usage: provision.sh --apply (creates billed Google Cloud resources)' >&2; exit 1; }
command -v gcloud >/dev/null || { echo 'Install/authenticate the official gcloud CLI first.' >&2; exit 1; }
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project=hale-tracer-286910
region=europe-west3
zone=europe-west3-a
name=airport-recovery
gc() { gcloud --quiet --project="$project" "$@"; }
# Never changes the caller's default project, billing account or IAM grants.
gc services enable compute.googleapis.com iap.googleapis.com oslogin.googleapis.com
[[ -n $(gc compute networks list --filter="name=$name" --format='value(name)') ]] || gc compute networks create "$name" --subnet-mode=custom
[[ -n $(gc compute networks subnets list --regions="$region" --filter="name=$name" --format='value(name)') ]] || gc compute networks subnets create "$name" --network="$name" --region="$region" --range=10.78.0.0/24
[[ -n $(gc compute firewall-rules list --filter="name=$name-web" --format='value(name)') ]] || gc compute firewall-rules create "$name-web" --network="$name" --allow=tcp:80,tcp:443 --source-ranges=0.0.0.0/0 --target-tags="$name"
[[ -n $(gc compute firewall-rules list --filter="name=$name-iap-ssh" --format='value(name)') ]] || gc compute firewall-rules create "$name-iap-ssh" --network="$name" --allow=tcp:22 --source-ranges=35.235.240.0/20 --target-tags="$name"
[[ -n $(gc compute addresses list --regions="$region" --filter="name=$name" --format='value(name)') ]] || gc compute addresses create "$name" --region="$region" --network-tier=PREMIUM
[[ -n $(gc compute disks list --zones="$zone" --filter="name=$name-data" --format='value(name)') ]] || gc compute disks create "$name-data" --zone="$zone" --size=20GB --type=pd-balanced
if [[ -z $(gc compute instances list --zones="$zone" --filter="name=$name" --format='value(name)') ]]; then
  gc compute instances create "$name" --zone="$zone" --machine-type=e2-small \
    --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=20GB --boot-disk-type=pd-balanced \
    --subnet="$name" --address="$name" --network-tier=PREMIUM --tags="$name" \
    --disk="name=$name-data,device-name=$name-data,mode=rw,boot=no,auto-delete=no" \
    --no-service-account --no-scopes --deletion-protection \
    --metadata=enable-oslogin=TRUE,block-project-ssh-keys=TRUE \
    --metadata-from-file="startup-script=$here/bootstrap.sh"
else
  echo 'Existing VM retained unchanged; inspect its configuration before continuing.'
fi
gc compute addresses describe "$name" --region="$region" --format='value(address)'
echo 'SSH: gcloud compute ssh airport-recovery --project=hale-tracer-286910 --zone=europe-west3-a --tunnel-through-iap'
