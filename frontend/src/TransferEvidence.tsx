import { airportArrivalEvidence, supplierMeetingEvidence } from './airportArrival';
import { freshnessTime, sourceInstant } from './resourceFreshness';
import './transferEvidence.css';

export function AirportArrivalDetails({ assumption }: { assumption: unknown }) {
  if (assumption == null) return null;
  const evidence = airportArrivalEvidence(assumption);
  if (!evidence)
    return (
      <p className="field-hint">
        Airport arrival evidence is incomplete; confirm these inputs with operations.
      </p>
    );
  return (
    <section className="transfer-evidence" aria-label="Airport arrival planning inputs">
      <strong>Airport-level arrival · {evidence.airport_id}</strong>
      <span>Reported drop-off: {evidence.dropoff.label}.</span>
      <span>
        Operator processing allowance: {evidence.processing_minutes} minutes before the selected
        onward flight, departing {freshnessTime(sourceInstant(evidence.onward_departure_at)!)}.
      </span>
      <span>
        Departure terminal unknown. This allowance is an operator planning input, not airline
        guidance or guaranteed boarding.
      </span>
    </section>
  );
}

export function SupplierMeetingInstructions({
  value,
  context,
}: {
  value: unknown;
  context?: 'hotel_to_airport';
}) {
  const meeting = supplierMeetingEvidence(value);
  if (!meeting) return null;
  return (
    <section className="transfer-evidence" aria-label="APL meeting guidance">
      <strong>APL meeting guidance</strong>
      {meeting.name && <span>{meeting.name}</span>}
      {meeting.instructions && (
        <span className="supplier-instructions-text">{meeting.instructions}</span>
      )}
      <span>
        {context === 'hotel_to_airport'
          ? 'Guidance returned by APL for this hotel-to-airport quote. Airport meeting guidance does not verify hotel pickup instructions or the flight’s departure terminal.'
          : 'Source guidance only. Its applicability to the requested pickup or flight departure terminal has not been verified.'}
      </span>
    </section>
  );
}
