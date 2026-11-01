import { postflightConnectionEvidence } from './postflightConnection';
export { postflightConnectionEvidence } from './postflightConnection';
import { sourceInstant, freshnessTime } from './resourceFreshness';

export default function PostflightConnectionDetails({
  value,
  incomingResourceId,
  pickupAt,
  departureSchedule,
  required = false,
}: {
  value: unknown;
  incomingResourceId?: string | null;
  pickupAt?: number | null;
  departureSchedule?: { epoch: unknown; departure: number };
  required?: boolean;
}) {
  if (value == null && !required) return null;
  const evidence = postflightConnectionEvidence(value);
  const scheduleEpoch = sourceInstant(departureSchedule?.epoch);
  if (
    !evidence ||
    (incomingResourceId !== undefined && incomingResourceId !== evidence.incoming_resource_id) ||
    (pickupAt !== undefined && pickupAt !== sourceInstant(evidence.derived_pickup_at)) ||
    (departureSchedule !== undefined &&
      (scheduleEpoch === null ||
        !Number.isSafeInteger(departureSchedule.departure) ||
        departureSchedule.departure !==
          Math.ceil((sourceInstant(evidence.derived_pickup_at)! - scheduleEpoch) / 60_000)))
  )
    return (
      <p className="case-source-warning">
        Flight-to-transfer connection evidence is incomplete or inconsistent. Review the itinerary
        before use.
      </p>
    );
  return (
    <section className="transfer-evidence" aria-label="Planned flight-to-transfer connection">
      <strong>Planned connection at OTP · flight {evidence.incoming_resource_id}</strong>
      <span>
        Supplier-scheduled arrival: {freshnessTime(sourceInstant(evidence.source_arrival_at)!)}.
        This is not an observed landing.
      </span>
      <span>
        Operator arrival processing: {evidence.airport_processing_minutes} minutes. Derived pickup:{' '}
        {freshnessTime(sourceInstant(evidence.derived_pickup_at)!)}.
      </span>
      <span>
        Approved pickup window: {freshnessTime(sourceInstant(evidence.pickup_window_start)!)} –{' '}
        {freshnessTime(sourceInstant(evidence.pickup_window_end)!)}.
      </span>
      <span>
        Pickup uses the later of window start and scheduled arrival plus processing, rounded up.
        Terminal, baggage collection and connection protection are unverified. Vehicle unreserved;
        road arrival estimated.
      </span>
    </section>
  );
}
