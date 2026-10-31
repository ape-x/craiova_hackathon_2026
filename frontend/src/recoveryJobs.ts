import type { RecoveryJob, RecoveryJobStage } from './types';

export const recoveryJobActive = (job: RecoveryJob): boolean =>
  job.status === 'queued' || job.status === 'running';

const groundReferenceLimitations = new Set([
  'ground_availability_unverified',
  'ground_fare_allowance_uncovered',
  'ground_fare_date_unsupported',
  'ground_fare_fx_allowance_uncovered',
  'ground_fare_fx_unavailable',
  'ground_fare_not_found',
  'ground_fare_party_unsupported',
  'ground_fare_provider_unavailable',
  'ground_fare_route_unsupported',
  'ground_fare_source_failed',
  'ground_fare_window_closed',
]);

/** Search outcomes describe alternatives; they do not invalidate a selected plan. */
export function classifyRecoveryJobGaps(gaps: RecoveryJob['gaps'], allPassengersProposed = false) {
  const requirements: RecoveryJob['gaps'] = [];
  const sourceExclusions: RecoveryJob['gaps'] = [];
  const waitingNotes: RecoveryJob['gaps'] = [];
  for (const gap of gaps) {
    if (gap.code === 'overnight_wait_without_accommodation') waitingNotes.push(gap);
    else if (
      gap.code === 'quote_not_usable' ||
      gap.code === 'no_flight_offers' ||
      (allPassengersProposed && groundReferenceLimitations.has(gap.code))
    )
      sourceExclusions.push(gap);
    else requirements.push(gap); // Unknown requirements remain visible, never silently downgraded.
  }
  return { requirements, sourceExclusions, waitingNotes };
}

export const recoveryStageLabel: Record<RecoveryJobStage, string> = {
  queued: 'Queued',
  validating: 'Checking case and passenger requirements',
  planning_queries: 'Preparing provider searches',
  searching_flights: 'Searching flight suppliers',
  searching_hotels: 'Searching hotel rates',
  searching_ground: 'Searching dated coach fares',
  locating_hotel: 'Checking hotel location and published policy',
  quoting_initial_airport_to_hotel: 'Getting airport-to-hotel transfer quotes',
  routing_initial_airport_to_hotel: 'Checking the airport-to-hotel driving route',
  reference_fx: 'Retrieving the ECB reference exchange rate',
  quoting_hotel_to_airport: 'Getting hotel-to-airport transfer quotes',
  routing_hotel_to_airport: 'Checking the hotel-to-airport driving route',
  evaluating: 'Checking returned alternatives',
  deciding: 'Evaluating recovery decisions',
  applying: 'Recording proposals',
  finished: 'Finished',
};

/** A poll started before a mutation must not erase a new job or regress its status. */
export function mergeRecoveryJobs(current: RecoveryJob[], incoming: RecoveryJob[]): RecoveryJob[] {
  const jobs = new Map(current.map((job) => [job.id, job]));
  for (const job of incoming) {
    const previous = jobs.get(job.id);
    const difference = previous ? Date.parse(job.updated_at) - Date.parse(previous.updated_at) : 1;
    if (
      !previous ||
      difference > 0 ||
      (difference === 0 && (!recoveryJobActive(job) || recoveryJobActive(previous)))
    )
      jobs.set(job.id, job);
  }
  return [...jobs.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}
