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

export function SupplierMeetingInstructions({ value }: { value: unknown }) {
  const meeting = supplierMeetingEvidence(value);
  if (!meeting) return null;
  return (
    <section className="transfer-evidence" aria-label="Supplier meeting instructions">
      <strong>APL supplier meeting instructions</strong>
      {meeting.name && <span>{meeting.name}</span>}
      {meeting.instructions && (
        <span className="supplier-instructions-text">{meeting.instructions}</span>
      )}
      <span>
        Supplier pickup instructions; these do not establish the flight’s departure terminal.
      </span>
    </section>
  );
}
