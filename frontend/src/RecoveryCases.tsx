import { ArrowUpRight, Plus } from 'lucide-react';
import { incidentKindLabel, utcTimestamp } from './recoveryCase';
import type { FlightSelection, RecoveryIncident, World } from './types';
import { Tag } from './ui';
import { CaseAirportRestrictions } from './AirportRestrictionPanel';
import { activeCaseRestrictions } from './airportRestrictionData';
import './recoveryCase.css';

export function FlightFacts({ flight }: { flight: FlightSelection }) {
  const airport = (value: FlightSelection['origin']) =>
    value?.iata ?? value?.icao ?? value?.catalog_code ?? 'Unknown';
  return (
    <div className="case-flight-facts">
      <div className="case-flight-heading">
        <strong>{flight.flight_number ?? flight.callsign ?? flight.fr24_id}</strong>
        <Tag tone={flight.freshness === 'current' ? 'cyan' : 'amber'}>
          {flight.freshness.toUpperCase()}
        </Tag>
      </div>
      <p>
        {airport(flight.origin)} → {airport(flight.intended_destination)}{' '}
        <span>intended route</span>
      </p>
      <dl>
        <div>
          <dt>Actual destination reported</dt>
          <dd>{airport(flight.actual_destination)}</dd>
        </div>
        <div>
          <dt>Provider tracking state</dt>
          <dd>
            {flight.status === 'ended'
              ? 'Tracking ended'
              : flight.status === 'tracking'
                ? 'Tracking'
                : 'Unknown'}
          </dd>
        </div>
        <div>
          <dt>Callsign / registration</dt>
          <dd>
            {flight.callsign ?? 'Unknown'} / {flight.registration ?? 'Unknown'}
          </dd>
        </div>
        <div>
          <dt>First / last seen</dt>
          <dd>
            {utcTimestamp(flight.first_seen)} / {utcTimestamp(flight.last_seen)}
          </dd>
        </div>
        <div>
          <dt>FR24 reference</dt>
          <dd>{flight.fr24_id}</dd>
        </div>
      </dl>
    </div>
  );
}
export function CaseSummary({ incident, world }: { incident: RecoveryIncident; world: World }) {
  const people = world.groups
    .filter((group) => incident.group_ids.includes(group.id))
    .reduce((sum, group) => sum + group.size, 0);
  return (
    <section className="case-saved-summary" aria-label="Saved recovery case">
      <div className="case-flight-heading">
        <strong>
          {incident.requested_flight_number} · {incident.date_utc} UTC
        </strong>
        <Tag tone={incident.status === 'open' ? 'cyan' : 'amber'}>
          {incident.status === 'open' ? 'OPEN' : 'NEEDS PASSENGER DATA'}
        </Tag>
      </div>
      <p>
        <strong>Original incident · {incidentKindLabel(incident.kind)}</strong>
        {(incident.original_unavailable_airport_code ?? incident.unavailable_airport_code)
          ? ` · ${incident.original_unavailable_airport_code ?? incident.unavailable_airport_code}`
          : ''}
      </p>
      <p>{incident.description}</p>
      <p className="field-hint">
        Operator-reported constraint at case creation · {utcTimestamp(incident.created_at)}.{' '}
        {incident.group_ids.length} linked groups · {people} passengers. A case does not establish
        available seats, rooms or bookings.
      </p>
      <CaseAirportRestrictions incident={incident} />
      {incident.flight ? (
        <details className="case-flight-context">
          <summary>Flight context · Flightradar24</summary>
          <FlightFacts flight={incident.flight} />
        </details>
      ) : (
        <p className="case-source-warning">
          Flight context has expired. The operator’s case remains recorded; provider facts are
          currently unavailable.
        </p>
      )}
    </section>
  );
}
export default function RecoveryCases({
  world,
  onOpen,
}: {
  world: World;
  onOpen: (id: string | null) => void;
}) {
  const incidents = [...(world.incidents ?? [])].reverse();
  return (
    <section className="recovery-cases" aria-label="Recovery cases">
      <button className="primary-button full" onClick={() => onOpen(null)}>
        <Plus size={15} />
        Start recovery
      </button>
      {incidents.length === 0 ? (
        <p className="field-hint">
          Find the affected flight, record the incident, then link passenger requirements.
        </p>
      ) : (
        incidents.map((incident) => (
          <button className="case-rail-row" key={incident.id} onClick={() => onOpen(incident.id)}>
            <span>
              <strong>{incident.requested_flight_number}</strong>
              <small>
                Original: {incidentKindLabel(incident.kind)}
                {(incident.original_unavailable_airport_code ?? incident.unavailable_airport_code)
                  ? ` · ${incident.original_unavailable_airport_code ?? incident.unavailable_airport_code}`
                  : ''}
              </small>
              <small>
                {activeCaseRestrictions(incident).length} active airport directions restricted
              </small>
              <small>{incident.date_utc} UTC</small>
              <small>
                {incident.status === 'needs_passenger_data'
                  ? 'Needs passenger data'
                  : `${incident.group_ids.length} groups linked`}
              </small>
            </span>
            <ArrowUpRight size={14} />
          </button>
        ))
      )}
    </section>
  );
}
