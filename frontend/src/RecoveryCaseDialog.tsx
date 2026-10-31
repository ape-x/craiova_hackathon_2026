import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, RefreshCw, Search } from 'lucide-react';
import { api, ApiError, TransportError } from './api';
import type {
  FlightLookup,
  FlightSelection,
  IncidentResult,
  MutationResult,
  RecoveryIncident,
  World,
} from './types';
import { Modal, Tag } from './ui';
import { CaseSummary, FlightFacts } from './RecoveryCases';
import {
  groupBindingIssue,
  flightLookupCurrent,
  normalizeFlightReference,
  selectedFlight,
  utcTimestamp,
  validateFlightQuery,
} from './recoveryCase';
import { passengerSourceLabel } from './liveData';
import './recoveryCase.css';
import RecoveryJobPanel from './RecoveryJobPanel';

type ActionKind = 'create' | 'bind' | 'refresh';
export default function RecoveryCaseDialog({
  world,
  incidentId,
  onApplied,
  onRefresh,
  onClose,
  onManageData,
}: {
  world: World;
  incidentId: string | null;
  onApplied: (result: MutationResult) => void;
  onRefresh: () => Promise<unknown>;
  onClose: () => void;
  onManageData: (incidentId: string | null) => void;
}) {
  const [activeId, setActiveId] = useState(incidentId);
  const incident = (world.incidents ?? []).find((item) => item.id === activeId);
  const [query, setQuery] = useState({
    flight_number: '',
    date_utc: new Date().toISOString().slice(0, 10),
  });
  const [lookup, setLookup] = useState<FlightLookup | null>(null);
  const [selectionId, setSelectionId] = useState('');
  const [kind, setKind] = useState<RecoveryIncident['kind']>('flight_disruption');
  const [description, setDescription] = useState('');
  const [airportCode, setAirportCode] = useState('');
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [refreshingFlight, setRefreshingFlight] = useState(false);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [jobSubmissionPending, setJobSubmissionPending] = useState(false);
  const [uncertain, setUncertain] = useState<{ error: TransportError; kind: ActionKind } | null>(
    null,
  );
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [now, setNow] = useState(Date.now());
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const running = useRef(false);
  const locked = busy || Boolean(uncertain) || jobSubmissionPending;
  const lockedRef = useRef(locked);
  lockedRef.current = locked;
  const close = useCallback(() => {
    if (running.current || lockedRef.current) {
      setError('Finish or recover the pending submission before closing this case.');
      return;
    }
    onClose();
  }, [onClose]);
  useEffect(() => {
    mounted.current = true;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      mounted.current = false;
      generation.current++;
      controller.current?.abort();
      clearInterval(timer);
    };
  }, []);
  const match = selectedFlight(lookup, selectionId, now);
  const rawSelection = lookup?.matches.find((candidate) => candidate.selection_id === selectionId);
  const lookupExpired = lookup?.state === 'ready' && !flightLookupCurrent(lookup, now);
  function changeQuery(key: keyof typeof query, value: string) {
    generation.current++;
    controller.current?.abort();
    setSearching(false);
    setLookup(null);
    setSelectionId('');
    setGroupIds([]);
    setError('');
    setSuccess('');
    setQuery((previous) => ({ ...previous, [key]: value }));
  }
  async function searchFlight(refreshCase?: RecoveryIncident) {
    const input = refreshCase
      ? { flight_number: refreshCase.requested_flight_number, date_utc: refreshCase.date_utc }
      : { ...query, flight_number: normalizeFlightReference(query.flight_number) };
    const invalid = validateFlightQuery(input.flight_number, input.date_utc);
    if (invalid) {
      setError(invalid);
      return;
    }
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const ownGeneration = ++generation.current;
    setLookup(null);
    setSelectionId('');
    setSearching(true);
    setError('');
    setSuccess('');
    try {
      const response = await api.lookupFlight(input, current.signal);
      if (mounted.current && !current.signal.aborted && ownGeneration === generation.current)
        setLookup(response);
    } catch (cause) {
      if (mounted.current && !current.signal.aborted && ownGeneration === generation.current)
        setError(
          cause instanceof TransportError
            ? 'Flight lookup did not return a usable response. Search again; no recovery case was created.'
            : cause instanceof Error
              ? cause.message
              : 'Flight lookup failed.',
        );
    } finally {
      if (mounted.current && ownGeneration === generation.current) setSearching(false);
    }
  }
  async function mutate(actionKind: ActionKind, action: () => Promise<IncidentResult>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setError('');
    setSuccess('');
    try {
      const result = await action();
      onApplied(result);
      if (mounted.current) {
        setActiveId(result.incident_id);
        setUncertain(null);
        setGroupIds([]);
        setLookup(null);
        setSelectionId('');
        setRefreshingFlight(false);
        setSuccess(
          actionKind === 'create'
            ? 'Recovery case recorded. Existing operations are preserved.'
            : actionKind === 'bind'
              ? 'Passenger groups linked to this case.'
              : 'Flight context refreshed for the same flight leg.',
        );
      }
    } catch (cause) {
      if (mounted.current) {
        setUncertain(cause instanceof TransportError ? { error: cause, kind: actionKind } : null);
        setError(cause instanceof Error ? cause.message : 'The case update failed.');
        if (cause instanceof ApiError && cause.status === 409) await onRefresh().catch(() => {});
      }
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function groupPicker(
    flight: FlightSelection | null,
    reference: string,
    currentCase?: RecoveryIncident,
  ) {
    return (
      <section className="case-groups" aria-label="Affected passenger groups">
        <h3>Affected passenger groups</h3>
        <p className="field-hint">
          Only explicitly selected groups are linked. Their original flight reference must match{' '}
          {reference}. Linking does not change their locations or confirm travel.
        </p>
        {!world.groups.length ? (
          <p className="case-source-warning">
            No passenger groups have been recorded. Save the case first, then import the manifest or
            enter planning assumptions.
          </p>
        ) : (
          <div className="case-group-options">
            {world.groups.map((group) => {
              const existing = currentCase?.group_ids.includes(group.id) ?? false;
              const issue = groupBindingIssue(group, flight, reference, currentCase?.id);
              return (
                <label className="case-group" key={group.id}>
                  <input
                    type="checkbox"
                    checked={existing || groupIds.includes(group.id)}
                    disabled={
                      locked ||
                      existing ||
                      Boolean(issue) ||
                      currentCase?.flight_context_state === 'expired'
                    }
                    onChange={(event) =>
                      setGroupIds((ids) =>
                        event.target.checked
                          ? [...ids, group.id]
                          : ids.filter((id) => id !== group.id),
                      )
                    }
                  />
                  <span>
                    <strong>
                      {group.name} · {group.size} passengers
                    </strong>
                    <small>
                      {group.original_flight_id} · {passengerSourceLabel(group.source)}
                    </small>
                    <small>{existing ? 'Already linked' : (issue ?? 'Available to link')}</small>
                  </span>
                </label>
              );
            })}
          </div>
        )}
        {groupIds.length > 0 && (
          <button
            type="button"
            className="secondary-button"
            disabled={locked}
            onClick={() => setGroupIds([])}
          >
            Clear selected groups
          </button>
        )}
      </section>
    );
  }
  const lookupResults = lookup && (
    <section className="case-lookup-results" aria-label="Flight lookup results">
      <p className="field-hint">
        {lookup.source} · {lookup.cached ? 'Cached response' : 'Retrieved'}{' '}
        {utcTimestamp(lookup.queried_at)} · lookup valid until {utcTimestamp(lookup.expires_at)}
      </p>
      {lookup.message && <p role="status">{lookup.message}</p>}
      {lookup.state === 'no_match' && (
        <p className="case-source-warning">
          No matching flight was returned for this UTC date. This does not establish cancellation or
          a disruption.
        </p>
      )}
      {lookup.state !== 'ready' && lookup.state !== 'no_match' && (
        <p className="case-source-warning">
          Flight context is unavailable from the provider. No flight or recovery case has been
          invented.
        </p>
      )}
      {lookup.state === 'ready' && (
        <>
          <p className="field-hint">
            Choose the exact flight leg. A tracking state does not establish seat availability or
            prove the reported incident.
          </p>
          {lookup.matches.map((flight) => {
            const differentLeg = Boolean(
              incident && flight.selection_id !== incident.flight_ref.selection_id,
            );
            return (
              <label
                className={`case-match ${selectionId === flight.selection_id ? 'selected' : ''}`}
                key={flight.selection_id}
              >
                <input
                  type="radio"
                  name="flight-match"
                  value={flight.selection_id}
                  checked={selectionId === flight.selection_id}
                  disabled={locked || differentLeg || Boolean(lookupExpired)}
                  onChange={() => {
                    setSelectionId(flight.selection_id);
                    setGroupIds([]);
                  }}
                />
                <div>
                  <FlightFacts flight={flight} />
                  {differentLeg && (
                    <p className="field-hint">
                      Different flight leg; cannot replace this case’s source context.
                    </p>
                  )}
                </div>
              </label>
            );
          })}
          {lookupExpired && (
            <p className="form-error" role="alert">
              This lookup has expired. Search again before saving.
            </p>
          )}
        </>
      )}
    </section>
  );
  return (
    <Modal title={activeId ? 'Recovery case' : 'Start recovery'} onClose={close} wide>
      {activeId ? (
        incident ? (
          <>
            <CaseSummary incident={incident} world={world} />
            <RecoveryJobPanel
              key={incident.id}
              world={world}
              incident={incident}
              disabled={busy || Boolean(uncertain)}
              onRefresh={onRefresh}
              onMutationPending={setJobSubmissionPending}
            />
            <div className="case-actions">
              <button
                className="secondary-button"
                disabled={locked || searching}
                onClick={() => {
                  setRefreshingFlight(true);
                  void searchFlight(incident);
                }}
              >
                <RefreshCw size={14} />
                Refresh flight context
              </button>
              <button
                className="secondary-button"
                disabled={locked}
                onClick={() => onManageData(incident.id)}
              >
                Manage passenger requirements &amp; services
              </button>
            </div>
            {refreshingFlight ? (
              <>
                {lookupResults}
                <div className="case-actions">
                  <button
                    className="secondary-button"
                    disabled={locked}
                    onClick={() => {
                      generation.current++;
                      controller.current?.abort();
                      setSearching(false);
                      setRefreshingFlight(false);
                      setLookup(null);
                      setSelectionId('');
                    }}
                  >
                    Back to case
                  </button>
                  <button
                    className="primary-button"
                    disabled={
                      locked || !match || match.selection_id !== incident.flight_ref.selection_id
                    }
                    onClick={() => {
                      if (match && lookup?.lookup_id)
                        void mutate('refresh', () =>
                          api.refreshIncidentFlight(
                            world,
                            incident.id,
                            lookup.lookup_id!,
                            match.selection_id,
                          ),
                        );
                    }}
                  >
                    Update source context
                  </button>
                </div>
              </>
            ) : (
              <>
                {groupPicker(incident.flight, incident.requested_flight_number, incident)}
                {incident.flight_context_state === 'expired' && (
                  <p className="field-hint">
                    Refresh this flight context before linking additional groups.
                  </p>
                )}
                <button
                  className="primary-button"
                  disabled={
                    locked ||
                    incident.flight_context_state === 'expired' ||
                    !incident.flight ||
                    !groupIds.length
                  }
                  onClick={() =>
                    void mutate('bind', () => api.bindIncidentGroups(world, incident.id, groupIds))
                  }
                >
                  Link selected groups
                </button>
              </>
            )}
          </>
        ) : (
          <p className="form-error" role="alert">
            This case is no longer present in the current operation.
          </p>
        )
      ) : (
        <>
          <p className="modal-copy">
            Find the affected flight and record the operator’s incident report. Existing cases,
            inventory and passenger plans remain in place.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void searchFlight();
            }}
          >
            <fieldset className="case-fieldset" disabled={locked}>
              <div className="case-form-grid">
                <label className="field">
                  Flight number
                  <input
                    autoComplete="off"
                    data-autofocus
                    value={query.flight_number}
                    maxLength={16}
                    onChange={(event) =>
                      changeQuery('flight_number', event.target.value.toUpperCase())
                    }
                    required
                  />
                </label>
                <label className="field">
                  First tracked date (UTC)
                  <input
                    type="date"
                    min="2022-06-01"
                    max={new Date().toISOString().slice(0, 10)}
                    value={query.date_utc}
                    onChange={(event) => changeQuery('date_utc', event.target.value)}
                    required
                  />
                </label>
              </div>
              <p className="field-hint">
                Choose the UTC day when Flightradar24 first tracked the flight. For flights around
                midnight, this can differ from the local departure date.
              </p>
              <button className="secondary-button" disabled={searching}>
                <Search size={14} />
                Find flight
              </button>
            </fieldset>
          </form>
          {lookupResults}
          {rawSelection && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                if (!match || !lookup?.lookup_id) {
                  setError('Choose a current flight lookup before saving.');
                  return;
                }
                void mutate('create', () =>
                  api.createIncident(world, {
                    lookup_id: lookup.lookup_id!,
                    selection_id: match.selection_id,
                    kind,
                    description: description.trim(),
                    ...(kind === 'destination_unavailable'
                      ? { unavailable_airport_code: airportCode.trim().toUpperCase() }
                      : {}),
                    affected_group_ids: groupIds,
                  }),
                );
              }}
            >
              <fieldset className="case-fieldset" disabled={locked}>
                <div className="case-section-title">
                  <Tag tone="amber">OPERATOR REPORT</Tag>
                  <h3>Describe the incident</h3>
                </div>
                <p className="field-hint">
                  These are operator-entered constraints, not an incident status confirmed by
                  Flightradar24.
                </p>
                <div className="case-form-grid">
                  <label className="field">
                    Incident type
                    <select
                      value={kind}
                      onChange={(event) => setKind(event.target.value as RecoveryIncident['kind'])}
                    >
                      <option value="flight_disruption">Flight disruption</option>
                      <option value="destination_unavailable">Destination unavailable</option>
                    </select>
                  </label>
                  {kind === 'destination_unavailable' && (
                    <label className="field">
                      Unavailable airport (IATA / ICAO)
                      <input
                        value={airportCode}
                        maxLength={4}
                        pattern="[A-Z0-9]{3,4}"
                        onChange={(event) => setAirportCode(event.target.value.toUpperCase())}
                        required
                      />
                    </label>
                  )}
                </div>
                <label className="field">
                  Incident description
                  <textarea
                    rows={3}
                    minLength={3}
                    maxLength={2000}
                    required
                    value={description}
                    onChange={(event) => setDescription(event.target.value)}
                  />
                </label>
                {groupPicker(match, query.flight_number)}
                <button className="primary-button" disabled={!match || searching}>
                  Save recovery case
                </button>
              </fieldset>
            </form>
          )}
        </>
      )}
      {(searching || busy) && (
        <p className="case-feedback" role="status">
          <Loader2 className="spin" size={15} />
          {busy ? 'Saving the operation…' : 'Looking up the flight…'}
        </p>
      )}
      {success && (
        <p className="case-feedback" role="status">
          {success}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {uncertain && (
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => void mutate(uncertain.kind, () => uncertain.error.retry<IncidentResult>())}
        >
          <RefreshCw size={14} />
          Recover original submission
        </button>
      )}
    </Modal>
  );
}
