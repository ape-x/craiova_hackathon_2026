import type { Resource } from './types';
import { isTransfero, transferoEvidence } from './transferoData';
import { freshnessTime } from './resourceFreshness';

export default function TransferoScheduleDetails({ resource }: { resource: Resource }) {
  if (!isTransfero(resource)) return null;
  const evidence = transferoEvidence(resource);
  if (!evidence)
    return (
      <p className="field-hint">
        Scheduled transport source details are incomplete. Recheck the source before a new plan; no
        reservation is established.
      </p>
    );
  return (
    <div className="price-evidence" aria-label="Recorded scheduled transport evidence">
      <strong>Direct Aeroport · {evidence.vehicleType} · scheduled service</strong>
      <p>
        {evidence.origin.name}, {evidence.origin.city} → {evidence.destination.name},{' '}
        {evidence.destination.city}
      </p>
      <p>
        Departure {freshnessTime(evidence.departure)} · scheduled arrival{' '}
        {freshnessTime(evidence.arrival)} (timetable estimate).
      </p>
      <p>
        {evidence.reportedSeats} seats reported at receipt ({freshnessTime(evidence.received)}),
        unreserved. No seat selection or guaranteed availability.
      </p>
      <details>
        <summary>Source timing and boarding limits</summary>
        <p>
          The source timetable uses local clocks interpreted in Europe/Bucharest. Arrival is an
          estimate; this is not a tracked vehicle or a traffic-aware road route.
        </p>
        <p>
          Conservative application rules: 12 hours’ notice, 20 minutes for boarding and at least 3
          hours before the onward flight. Named facilities do not establish a boarding bay or hotel
          door; exact instructions require supplier confirmation.
        </p>
        <a
          href="https://direct-aeroport.ro/termeni-conditii-calatorie/"
          target="_blank"
          rel="noreferrer"
        >
          Operator terms
        </a>
      </details>
    </div>
  );
}
