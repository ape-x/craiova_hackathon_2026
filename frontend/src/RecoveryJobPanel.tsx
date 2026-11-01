import { useEffect, useRef, useState } from 'react';
import AirportTransferRecoveryFields, {
  AirportTransferRequestDetails,
} from './AirportTransferRecoveryFields';
import { airportTransferInputs } from './airportTransferRecovery';
import AirportTransferQuoteObservations from './AirportTransferQuoteObservations';
import SavedAirportTransferInputs from './SavedAirportTransferInputs';
import SavedAfterFlightInputs from './SavedAfterFlightInputs';
import SavedRomanianHotelInputs from './SavedRomanianHotelInputs';
import AfterFlightTransferFields, {
  AfterFlightTransferRequestDetails,
} from './AfterFlightTransferFields';
import { afterFlightTransferInput } from './afterFlightTransfer';
import {
  cloneRecoveryFormDraft,
  restoreRecoveryFormDraft,
  type RecoveryFormDraft,
} from './recoveryFormDraft';
import { initialRecoverySelection, recoveryGroupSelectionIssue } from './recoveryNavigation';
import { recoveryItineraryTarget, type RecoveryItineraryIntent } from './recoveryItineraryReview';
import type { RecoveryDataContext } from './recoveryDataContext';
import { Loader2, RefreshCw, Square } from 'lucide-react';
import { api, ApiError, TransportError } from './api';
import type {
  RecoveryIncident,
  RecoveryJob,
  RecoveryJobInput,
  RecoveryJobResponse,
  World,
} from './types';
import { passengerSourceLabel } from './liveData';
import { utcTimestamp } from './recoveryCase';
import {
  classifyRecoveryJobGaps,
  mergeRecoveryJobs,
  recoveryJobActive,
  recoveryJobAnchor,
  recoveryJobWaiting,
  recoveryStageLabel,
} from './recoveryJobs';
import { Tag } from './ui';
import HotelRecoveryFields from './HotelRecoveryFields';
import RomanianHotelRecoveryFields, {
  RomanianHotelRequestDetails,
} from './RomanianHotelRecoveryFields';
import {
  emptyRomanianHotelRecoveryForm,
  romanianHotelGroupIssue,
  romanianHotelRecoveryInput,
} from './romanianHotelRecovery';
import HistoricalSource from './HistoricalSource';
import { CoachFareResults } from './CoachFares';
import {
  emptyHotelRecoveryForm,
  hotelRecoveryGroupIssue,
  hotelRecoveryInput,
} from './hotelRecovery';

function waitDuration(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes < 0) return 'Duration unavailable';
  const exact = `${minutes.toLocaleString('en-GB', { maximumFractionDigits: 1 })} min`;
  if (minutes < 60) return exact;
  const hours = Math.floor(minutes / 60);
  const remaining = Math.round((minutes - hours * 60) * 10) / 10;
  return `${exact} (${hours} h${remaining ? ` ${remaining} min` : ''})`;
}

function waitClock(from: string, to: string, timezone: string | null) {
  const options: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZoneName: 'shortOffset',
  };
  let zone = timezone;
  let formatter: Intl.DateTimeFormat;
  try {
    if (!zone) throw new Error('Local timezone unavailable');
    formatter = new Intl.DateTimeFormat('en-GB', { ...options, timeZone: zone });
  } catch {
    zone = null;
    formatter = new Intl.DateTimeFormat('en-GB', { ...options, timeZone: 'UTC' });
  }
  const format = (value: string) =>
    Number.isFinite(Date.parse(value)) ? formatter.format(new Date(value)) : 'Time unavailable';
  return { from: format(from), to: format(to), zone: zone ?? 'UTC · local timezone unavailable' };
}

function RecoveryJobReference({ id, recorded }: { id: string; recorded?: RecoveryJob }) {
  if (!recorded) return <span>{id} · not in loaded case history</span>;
  const anchor = recoveryJobAnchor(id);
  return (
    <a
      href={`#${encodeURIComponent(anchor)}`}
      onClick={() => {
        const target = document.getElementById(anchor);
        const history = target?.closest('details');
        if (history) history.open = true;
        target?.focus({ preventScroll: true });
      }}
    >
      {id} · {recorded.status.replaceAll('_', ' ')}
    </a>
  );
}

export function ReviewRecoveryItinerary({
  world,
  incidentId,
  job,
  offerId,
  disabled = false,
  onReview,
}: {
  world: World;
  incidentId: string;
  job: RecoveryJob;
  offerId: string;
  disabled?: boolean;
  onReview: (intent: RecoveryItineraryIntent) => void;
}) {
  const target = recoveryItineraryTarget(world, incidentId, job, offerId);
  if (target.kind === 'unavailable')
    return (
      <p className="field-hint">
        {offerId} · {target.reason}
      </p>
    );
  return (
    <button
      type="button"
      className="secondary-button"
      disabled={disabled}
      onClick={() => {
        if (disabled) return;
        const current = recoveryItineraryTarget(world, incidentId, job, offerId);
        if (current.kind === 'ready') onReview(current.intent);
      }}
    >
      Review itinerary · {target.group.name} · {offerId}
    </button>
  );
}

export function RecoveryJobDetails({
  job,
  world,
  relatedJobs,
  incidentId,
  reviewDisabled,
  onReviewItinerary,
}: {
  job: RecoveryJob;
  world: World;
  relatedJobs?: readonly RecoveryJob[];
  incidentId?: string;
  reviewDisabled?: boolean;
  onReviewItinerary?: (intent: RecoveryItineraryIntent) => void;
}) {
  const result = job.result;
  const waitingForPrevious = recoveryJobWaiting(job);
  const {
    requirements: unresolved,
    sourceExclusions,
    waitingNotes: waitNotes,
  } = classifyRecoveryJobGaps(
    job.gaps,
    job.status === 'completed' &&
      result !== null &&
      result.recovered_passengers > 0 &&
      result.exception_passengers === 0,
  );
  const currentOfferIds = new Set(
    world.offers
      .filter(
        (offer) =>
          ['proposed', 'accepted', 'held', 'confirmed'].includes(offer.status) &&
          (offer.status === 'confirmed' || Date.parse(offer.expires_at) > Date.now()),
      )
      .map((offer) => offer.id),
  );
  const stillCurrent = result?.offer_ids.filter((id) => currentOfferIds.has(id)).length ?? 0;
  return (
    <article
      className="case-job"
      id={recoveryJobAnchor(job.id)}
      tabIndex={-1}
      aria-label={`Recovery attempt ${job.id}`}
    >
      <div className="case-flight-heading">
        <strong>
          {recoveryJobActive(job) ? recoveryStageLabel[job.stage] : job.status.replaceAll('_', ' ')}
        </strong>
        <Tag tone={job.status === 'completed' ? 'cyan' : 'amber'}>
          {job.status.replaceAll('_', ' ').toUpperCase()}
        </Tag>
      </div>
      <p role="status">{job.message}</p>
      <p className="field-hint">
        {job.id} · Started {utcTimestamp(job.created_at)} · Updated {utcTimestamp(job.updated_at)}
      </p>
      <p className="field-hint">
        {job.calls_dispatched} provider calls dispatched · limit {job.max_provider_calls} ·{' '}
        {job.group_ids.length} selected groups. Calls are usage, not a completion percentage.
      </p>
      {(waitingForPrevious || Boolean(job.supersedes_job_ids?.length)) && (
        <section className="case-job-result" aria-label="Recovery attempt dependencies">
          <h4>
            {waitingForPrevious ? 'Waiting before the new search' : 'Earlier recovery attempts'}
          </h4>
          {waitingForPrevious && (
            <p>
              Earlier attempts must finish before this search can start. An in-flight call may still
              finish and incur a charge; an unknown outcome requires review.
            </p>
          )}
          {job.status === 'needs_review' && job.calls_dispatched === 0 && (
            <p>
              No new provider calls were dispatched by this attempt. Review the recorded
              dependencies.
            </p>
          )}
          {Boolean(job.supersedes_job_ids?.length) && (
            <>
              <p className="field-hint">
                This request supersedes the following attempts. Their own recorded statuses remain
                authoritative.
              </p>
              <ul>
                {job.supersedes_job_ids?.map((id) => (
                  <li key={id}>
                    <RecoveryJobReference
                      id={id}
                      recorded={relatedJobs?.find((item) => item.id === id)}
                    />
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
      <AirportTransferRequestDetails inputs={job.airport_transfers} world={world} />
      <AfterFlightTransferRequestDetails input={job.after_flight_transfer} world={world} />
      {job.result?.after_flight_transfer && (
        <section className="case-job-result" aria-label="Recorded chosen flight and transfer">
          {job.status === 'completed' &&
          job.result.offer_ids.length > 0 &&
          job.result.resource_ids.includes(job.result.after_flight_transfer.incoming_resource_id) &&
          job.result.resource_ids.includes(
            job.result.after_flight_transfer.transfer_resource_id,
          ) ? (
            <>
              <h4>Connection chosen in this completed attempt</h4>
              <p>
                Flight {job.result.after_flight_transfer.incoming_resource_id} → transfer{' '}
                {job.result.after_flight_transfer.transfer_resource_id}. This is the recorded
                proposal; review its current validity and connection evidence in the itinerary. It
                does not confirm landing or supplier bookings.
              </p>
            </>
          ) : (
            <p>
              Chosen connection is not confirmed by this attempt’s result. Review the case before
              use.
            </p>
          )}
        </section>
      )}
      <AirportTransferQuoteObservations quotes={job.airport_transfer_quotes} world={world} />
      {Boolean(job.blocking_job_ids?.length) && (
        <section className="case-job-result" aria-label="Attempts requiring review">
          <h4>Attempts requiring review</h4>
          <p className="field-hint">
            Review the recorded outcomes for these attempts before requesting further recovery.
          </p>
          <ul>
            {job.blocking_job_ids?.map((id) => (
              <li key={id}>
                <RecoveryJobReference
                  id={id}
                  recorded={relatedJobs?.find((item) => item.id === id)}
                />
              </li>
            ))}
          </ul>
        </section>
      )}
      {job.superseded_by_job_id && (
        <p className="case-source-warning">
          Superseded by request{' '}
          <RecoveryJobReference
            id={job.superseded_by_job_id}
            recorded={relatedJobs?.find((item) => item.id === job.superseded_by_job_id)}
          />
          . The newer request has its own status; this link does not confirm that earlier work has
          stopped.
        </p>
      )}
      {job.cancel_requested && job.status !== 'cancelled' && (
        <p className="case-source-warning">
          Cancellation requested. An in-flight provider call may still finish and incur a charge; no
          cancellation is complete until the recorded status confirms it.
        </p>
      )}
      {['unknown_outcome', 'needs_review'].includes(job.status) && (
        <p className="case-source-warning">
          Review this attempt’s recorded calls before starting another. A new attempt can incur
          additional provider charges; it does not resolve the previous call’s unknown outcome.
        </p>
      )}
      <RomanianHotelRequestDetails input={job.romanian_hotel_recovery} />
      {result && (
        <section className="case-job-result" aria-label="Recorded recovery result">
          <h4>Recorded proposal result</h4>
          <p>
            <strong>{result.recovered_passengers}</strong> passengers proposed ·{' '}
            <strong>{result.exception_passengers}</strong> with unresolved requirements
          </p>
          <p className="field-hint">
            This is the result at operation version {job.applied_version ?? 'not recorded'}.
            Proposals are not supplier bookings. {stillCurrent} of {result.offer_ids.length}{' '}
            recorded offers are currently active in this operation.
          </p>
          <dl>
            <div>
              <dt>Sources</dt>
              <dd>
                {Object.entries(result.source_counts)
                  .map(([source, count]) => `${source}: ${count}`)
                  .join(' · ') || 'No sourced result recorded'}
              </dd>
            </div>
            <div>
              <dt>Offer references</dt>
              <dd>{result.offer_ids.join(', ') || 'None'}</dd>
            </div>
            <div>
              <dt>Service references</dt>
              <dd>
                {world.mode === 'live' && result.resource_ids.length
                  ? result.resource_ids.map((resourceId) => (
                      <HistoricalSource
                        key={resourceId}
                        scenarioId={world.scenario_id}
                        resourceId={resourceId}
                        epoch={world.epoch}
                      />
                    ))
                  : result.resource_ids.join(', ') || 'None'}
              </dd>
            </div>
          </dl>
          {incidentId &&
            onReviewItinerary &&
            result.offer_ids.map((offerId) => (
              <ReviewRecoveryItinerary
                key={offerId}
                world={world}
                incidentId={incidentId}
                job={job}
                offerId={offerId}
                disabled={reviewDisabled}
                onReview={onReviewItinerary}
              />
            ))}
          {result.hotel_recovery && (
            <section className="hotel-recovery-result" aria-label="Recorded hotel recovery scope">
              <h4>Hotel stay and both transfer directions</h4>
              <p>
                {world.airports.find((node) => node.id === result.hotel_recovery?.hotel_node_id)
                  ?.name ?? 'Sourced hotel property'}
                {' · '}one property shortlist · flight query date{' '}
                {result.hotel_recovery.flight_query_date} (Europe/London)
              </p>
              <p>
                Return pickup: {utcTimestamp(result.hotel_recovery.return_pickup_at)}. Checkout + 10
                minutes is an application connection allowance, not a supplier pickup promise.
              </p>
              <p className="field-hint">
                The recorded proposal includes the requested hotel interval. This is a bounded
                sourced search, not a complete-market comparison or supplier booking.
              </p>
            </section>
          )}
          {result.romanian_hotel_recovery && job.status === 'completed' && (
            <section className="hotel-recovery-result" aria-label="Recorded Romania hotel journey">
              <h4>Hotel Aro Palace and both scheduled transport directions</h4>
              <p>
                Outbound departure:{' '}
                {utcTimestamp(result.romanian_hotel_recovery.outbound_departure_at)}. Return
                departure: {utcTimestamp(result.romanian_hotel_recovery.return_departure_at)}.
              </p>
              <p>
                Onward flight search date: {result.romanian_hotel_recovery.flight_query_date}{' '}
                (Europe/Bucharest).
              </p>
              <p className="field-hint">
                The recorded complete proposal includes the requested hotel stay, both Transfero /
                Direct Aeroport services and the onward flight. RON fares use an explicitly marked
                ECB estimate for the EUR planning budget. Nothing is reserved; source timetable
                arrival and named boarding facilities do not establish a guaranteed arrival, door or
                bay.
              </p>
            </section>
          )}
          {Boolean(result.waits?.length) && (
            <section className="case-job-waits" aria-label="Recorded journey waiting times">
              <h4>Waiting in the recorded itinerary</h4>
              <p className="field-hint">
                Planned waiting time is shown separately from travel and hotel stays.{' '}
                {result.hotel_recovery || result.romanian_hotel_recovery
                  ? 'These waits are outside the requested hotel interval; they do not replace the required stay.'
                  : 'An overnight wait is informational; no mandatory rest requirement was assumed.'}
              </p>
              <ul>
                {result.waits?.map((wait, index) => {
                  const clock = waitClock(wait.from_at, wait.to_at, wait.timezone);
                  const group = world.groups.find((item) => item.id === wait.group_id);
                  const location = world.airports.find((item) => item.id === wait.location_id);
                  return (
                    <li key={`${wait.group_id}-${wait.location_id}-${wait.from_at}-${index}`}>
                      <strong>
                        {waitDuration(wait.duration_minutes)} ·{' '}
                        {location ? `${location.name} (${wait.location_id})` : wait.location_id}
                      </strong>
                      <p>{group ? `${group.name} · ${wait.group_id}` : wait.group_id}</p>
                      <p>
                        <time dateTime={wait.from_at}>{clock.from}</time> →{' '}
                        <time dateTime={wait.to_at}>{clock.to}</time>
                      </p>
                      <p className="field-hint">
                        {clock.zone}
                        {wait.overnight === true
                          ? ' · Wait crosses local midnight'
                          : wait.overnight === null
                            ? ' · Local overnight status unknown'
                            : ''}
                      </p>
                      {wait.accommodation_included === false && (
                        <p>Accommodation is not included during this wait.</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </section>
      )}
      {Boolean(job.ground_references?.length) && (
        <section className="coach-job-references" aria-label="Recorded ground fare references">
          <h4>Ground fares requiring seat confirmation</h4>
          {job.ground_references?.map((reference, index) => (
            <div className="coach-job-reference" key={`${reference.group_ids.join('-')}-${index}`}>
              <p className="field-hint">
                Groups:{' '}
                {reference.group_ids
                  .map((id) => world.groups.find((group) => group.id === id)?.name ?? id)
                  .join(', ')}
              </p>
              <CoachFareResults result={reference.result} historical />
            </div>
          ))}
        </section>
      )}
      {waitNotes.length > 0 && (
        <section className="case-job-wait-notes" aria-label="Informational waiting considerations">
          <h4>Waiting considerations</h4>
          {waitNotes.map((note, index) => (
            <p key={`${note.code}-${index}`}>
              {note.message}
              {note.group_ids.length ? ` Groups: ${note.group_ids.join(', ')}.` : ''}
            </p>
          ))}
        </section>
      )}
      {unresolved.length > 0 && (
        <section className="case-job-gaps" aria-label="Recovery gaps">
          <h4>Requirements still unresolved</h4>
          <ul>
            {unresolved.map((gap, index) => (
              <li key={`${gap.code}-${index}`}>
                <strong>{gap.message}</strong>
                {gap.group_ids.length > 0 && <small>Groups: {gap.group_ids.join(', ')}</small>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {sourceExclusions.length > 0 && (
        <section className="case-job-gaps" aria-label="Source exclusions and search outcomes">
          <h4>Source exclusions and search outcomes</h4>
          <p className="field-hint">
            These reasons describe excluded alternatives, searches with no offers or limits on
            discovering new routes. They do not indicate an unresolved requirement in a selected
            proposal or establish that every alternative was searched.
          </p>
          <ul>
            {sourceExclusions.map((gap, index) => (
              <li key={`${gap.code}-${index}`}>
                <span>{gap.message}</span>
                {gap.group_ids.length > 0 && <small>Groups: {gap.group_ids.join(', ')}</small>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {job.steps.length > 0 && (
        <details className="case-job-ledger">
          <summary>Provider and decision record · {job.steps.length} steps</summary>
          <ol>
            {job.steps.map((step) => (
              <li key={step.id}>
                <div className="case-flight-heading">
                  <strong>{recoveryStageLabel[step.stage]}</strong>
                  <Tag>{step.status.toUpperCase()}</Tag>
                </div>
                {step.provider && <small>{step.provider}</small>}
                <p>{step.message}</p>
                <small>
                  {utcTimestamp(step.started_at)}
                  {step.finished_at ? ` → ${utcTimestamp(step.finished_at)}` : ''} ·{' '}
                  {step.quote_count} quotes returned
                  {step.group_ids.length ? ` · Groups: ${step.group_ids.join(', ')}` : ''}
                </small>
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}

type RecoveryJobFollow = { job: RecoveryJob; request: number };

/** Follow the recorded attempt once its article exists, without changing search drafts. */
export function revealRecoveryJob(
  follow: RecoveryJobFollow | undefined,
  incidentId: string,
  previous: string | null,
  panel: HTMLElement | null,
): string | null {
  if (!follow || follow.job.incident_id !== incidentId || !panel) return previous;
  const token = JSON.stringify([follow.request, follow.job.id]);
  if (token === previous) return previous;
  const target = panel.ownerDocument.getElementById(recoveryJobAnchor(follow.job.id));
  if (!target || !panel.contains(target)) return previous;
  const history = target.closest('details');
  if (history) history.open = true;
  const reduced = panel.ownerDocument.defaultView?.matchMedia?.(
    '(prefers-reduced-motion: reduce)',
  ).matches;
  target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
  target.focus({ preventScroll: true });
  return token;
}

export default function RecoveryJobPanel({
  world,
  incident,
  disabled,
  onRefresh,
  onMutationPending,
  refreshSignal = 0,
  receivedJob,
  followJob,
  initialSelection,
  onSelectionChange,
  onDraftChange,
  onReviewItinerary,
  reviewDisabled = false,
}: {
  world: World;
  incident: RecoveryIncident;
  disabled: boolean;
  onRefresh: () => Promise<unknown>;
  onMutationPending: (value: boolean) => void;
  refreshSignal?: number;
  receivedJob?: RecoveryJob | null;
  followJob?: RecoveryJobFollow;
  initialSelection?: RecoveryDataContext;
  onSelectionChange?: (groupIds: string[]) => void;
  onDraftChange?: (draft: RecoveryFormDraft) => void;
  onReviewItinerary?: (intent: RecoveryItineraryIntent) => void;
  reviewDisabled?: boolean;
}) {
  const [jobs, setJobs] = useState<RecoveryJob[]>([]);
  const panel = useRef<HTMLElement>(null);
  const followedJob = useRef<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [actionError, setActionError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState<TransportError | null>(null);
  const [selectionSeed] = useState(() =>
    initialRecoverySelection(world, incident, initialSelection),
  );
  const [selectionWarning, setSelectionWarning] = useState(selectionSeed.issue);
  const [restoredDraft] = useState(() =>
    restoreRecoveryFormDraft(world, incident, initialSelection),
  );
  const [draftIssue, setDraftIssue] = useState(restoredDraft.issue);
  const newDraft = (groupIds: string[] = []): RecoveryFormDraft => ({
    groupIds,
    objective: 'balanced',
    modes: ['flight'],
    limit: '8',
    instructions: '',
    hotelMode: false,
    hotelPackage: 'uk_apl',
    romanianHotelFields: emptyRomanianHotelRecoveryForm(),
    hotelFields: emptyHotelRecoveryForm(),
    airportTransfers: [],
    afterFlightTransfer: null,
  });
  const [draft, setDraft] = useState<RecoveryFormDraft>(
    () => restoredDraft.draft ?? newDraft(restoredDraft.issue ? [] : selectionSeed.groupIds),
  );
  const draftRef = useRef(draft);
  function updateDraft<K extends keyof RecoveryFormDraft>(
    key: K,
    value: RecoveryFormDraft[K] | ((previous: RecoveryFormDraft[K]) => RecoveryFormDraft[K]),
  ) {
    const previous = draftRef.current;
    const next = {
      ...previous,
      [key]:
        typeof value === 'function'
          ? (value as (previous: RecoveryFormDraft[K]) => RecoveryFormDraft[K])(previous[key])
          : value,
    };
    draftRef.current = next;
    setDraft(next);
    if (key === 'groupIds') {
      setSelectionWarning(null);
      onSelectionChange?.([...next.groupIds]);
    }
    onDraftChange?.(cloneRecoveryFormDraft(next));
  }
  const {
    groupIds: selectedGroups,
    objective,
    modes,
    limit,
    instructions,
    hotelMode,
    hotelPackage = 'uk_apl',
    romanianHotelFields = emptyRomanianHotelRecoveryForm(),
    hotelFields,
    airportTransfers,
    afterFlightTransfer,
  } = draft;
  const setSelectedGroups = (
    value:
      | RecoveryFormDraft['groupIds']
      | ((previous: RecoveryFormDraft['groupIds']) => RecoveryFormDraft['groupIds']),
  ) => updateDraft('groupIds', value);
  const setObjective = (value: RecoveryFormDraft['objective']) => updateDraft('objective', value);
  const setModes = (
    value:
      | RecoveryFormDraft['modes']
      | ((previous: RecoveryFormDraft['modes']) => RecoveryFormDraft['modes']),
  ) => updateDraft('modes', value);
  const setLimit = (value: string) => updateDraft('limit', value);
  const setInstructions = (value: string) => updateDraft('instructions', value);
  const setHotelMode = (value: boolean) => updateDraft('hotelMode', value);
  const setHotelFields = (
    value:
      | RecoveryFormDraft['hotelFields']
      | ((previous: RecoveryFormDraft['hotelFields']) => RecoveryFormDraft['hotelFields']),
  ) => updateDraft('hotelFields', value);
  const setAirportTransfers = (value: RecoveryFormDraft['airportTransfers']) =>
    updateDraft('airportTransfers', value);
  const setAfterFlightTransfer = (value: RecoveryFormDraft['afterFlightTransfer']) =>
    updateDraft('afterFlightTransfer', value);
  const running = useRef(false);
  const mounted = useRef(true);
  const current = useRef({ world, onRefresh, onMutationPending });
  current.current = { world, onRefresh, onMutationPending };
  const refreshedVersion = useRef(0);
  const groups = world.groups.filter((group) => incident.group_ids.includes(group.id));
  const groupIssues = new Map(
    groups.map((group) => [group.id, recoveryGroupSelectionIssue(world, incident, [group.id])]),
  );
  const eligible = groups.filter((group) => !groupIssues.get(group.id));
  const selectionIssue = recoveryGroupSelectionIssue(world, incident, selectedGroups);
  const hotelGroupCandidate =
    selectedGroups.length === 1
      ? eligible.find((group) => group.id === selectedGroups[0])
      : undefined;
  const hotelGroup =
    hotelGroupCandidate &&
    !(hotelPackage === 'romania_transfero' ? romanianHotelGroupIssue : hotelRecoveryGroupIssue)(
      hotelGroupCandidate,
      world,
    )
      ? hotelGroupCandidate
      : undefined;
  const active = jobs.some(recoveryJobActive);
  const locked = busy || Boolean(uncertain) || disabled;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const received = [receivedJob, followJob?.job].filter((job): job is RecoveryJob =>
      Boolean(job && job.incident_id === incident.id),
    );
    if (received.length) setJobs((previous) => mergeRecoveryJobs(previous, received));
    async function poll() {
      let next = false;
      try {
        const response = await api.recoveryJobs(incident.id, controller.signal);
        if (controller.signal.aborted) return;
        setJobs((previous) => mergeRecoveryJobs(previous, response.jobs));
        setLoadError('');
        next =
          response.jobs.some(recoveryJobActive) ||
          received.some(
            (job) =>
              recoveryJobActive(job) && !response.jobs.some((recorded) => recorded.id === job.id),
          );
        const applied = Math.max(0, ...response.jobs.map((job) => job.applied_version ?? 0));
        if (applied > current.current.world.version && applied > refreshedVersion.current) {
          await current.current.onRefresh();
          refreshedVersion.current = applied;
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        setLoadError(
          cause instanceof Error ? cause.message : 'Could not retrieve recovery progress.',
        );
        // Status reads are safe to repeat; this never dispatches a supplier search.
        next = true;
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (next) timer = setTimeout(() => void poll(), 2000);
        }
      }
    }
    void poll();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [incident.id, refresh, refreshSignal, receivedJob, followJob?.job]);
  useEffect(() => {
    followedJob.current = revealRecoveryJob(
      followJob,
      incident.id,
      followedJob.current,
      panel.current,
    );
  }, [followJob, incident.id, jobs]);

  async function mutate(action: () => Promise<RecoveryJobResponse>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setActionError('');
    current.current.onMutationPending(true);
    let unknown = false;
    try {
      const response = await action();
      if (mounted.current) {
        setJobs((previous) => mergeRecoveryJobs(previous, [response.job]));
        setUncertain(null);
        setRefresh((value) => value + 1);
      }
    } catch (cause) {
      unknown = cause instanceof TransportError;
      if (mounted.current) {
        setUncertain(unknown ? (cause as TransportError) : null);
        setActionError(cause instanceof Error ? cause.message : 'The recovery request failed.');
        if (cause instanceof ApiError && cause.status === 409) {
          await current.current.onRefresh().catch(() => {});
          setRefresh((value) => value + 1);
        }
      }
    } finally {
      running.current = false;
      if (mounted.current) {
        setBusy(false);
        current.current.onMutationPending(unknown);
      }
    }
  }
  return (
    <section ref={panel} className="case-job-panel" aria-label="Source recovery alternatives">
      <h3>Find sourced alternatives</h3>
      {draftIssue && (
        <div className="case-source-warning" role="alert">
          <p>
            The saved search draft needs review: {draftIssue} No replacement inputs or call
            allowance have been accepted.
          </p>
          <button
            type="button"
            className="secondary-button"
            disabled={locked}
            onClick={() => {
              const fresh = newDraft();
              draftRef.current = fresh;
              setDraft(fresh);
              setDraftIssue(null);
              setSelectionWarning(null);
              onSelectionChange?.([]);
              onDraftChange?.(cloneRecoveryFormDraft(fresh));
            }}
          >
            Discard saved draft and start again · default 8 calls
          </button>
        </div>
      )}
      {selectionWarning && (
        <p className="case-source-warning">
          Requested selection could not be restored: {selectionWarning} Choose the groups explicitly
          below.
        </p>
      )}
      {selectionIssue && (
        <p className="case-source-warning" role="alert">
          {selectionIssue}
        </p>
      )}
      <p className="field-hint">
        Search suppliers for explicitly selected groups in this case. Calls can incur provider
        charges. Results are proposals; this action does not book or pay for travel. Existing
        supplier bookings are preserved.
      </p>
      {loading && (
        <p className="case-feedback" role="status">
          <Loader2 className="spin" size={14} />
          Checking recorded recovery attempts…
        </p>
      )}
      {loadError && (
        <p className="form-error" role="alert">
          Progress could not be refreshed. {loadError} Existing records are preserved.
        </p>
      )}
      {jobs.map((job, index) =>
        index === 0 || recoveryJobActive(job) ? (
          <div key={job.id}>
            <RecoveryJobDetails
              job={job}
              world={world}
              relatedJobs={jobs}
              incidentId={incident.id}
              onReviewItinerary={onReviewItinerary}
              reviewDisabled={locked || loading || reviewDisabled}
            />
            {recoveryJobActive(job) && (
              <button
                className="secondary-button"
                disabled={locked || job.cancel_requested}
                onClick={() => void mutate(() => api.cancelRecoveryJob(job.id))}
              >
                <Square size={13} />
                {job.cancel_requested ? 'Cancellation requested' : 'Cancel remaining work'}
              </button>
            )}
          </div>
        ) : (
          <details key={job.id} className="case-job-ledger">
            <summary>
              Earlier attempt · {utcTimestamp(job.created_at)} · {job.status.replaceAll('_', ' ')}
            </summary>
            <RecoveryJobDetails
              job={job}
              world={world}
              relatedJobs={jobs}
              incidentId={incident.id}
              onReviewItinerary={onReviewItinerary}
              reviewDisabled={locked || loading || reviewDisabled}
            />
          </details>
        ),
      )}
      {active && (
        <p className="field-hint">
          This durable attempt continues if you close the case. Reopen this case to review its
          progress.
        </p>
      )}
      {!active && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (locked || active || loading || loadError || draftIssue) return;
            if (
              selectionIssue ||
              !selectedGroups.length ||
              selectedGroups.length > 24 ||
              selectedGroups.some((id) => !eligible.some((group) => group.id === id))
            ) {
              setActionError('Select 1–24 currently linked, unconfirmed passenger groups.');
              return;
            }
            const maxCalls = Number(limit);
            if (
              !Number.isInteger(maxCalls) ||
              maxCalls < 3 ||
              maxCalls > 26 ||
              (!hotelMode && !modes.length)
            ) {
              setActionError(
                'Choose at least one service type and a provider-call limit between 3 and 26.',
              );
              return;
            }
            let hotelRecovery: RecoveryJobInput['hotel_recovery'];
            let romanianHotelRecovery: RecoveryJobInput['romanian_hotel_recovery'];
            let transfers: RecoveryJobInput['airport_transfers'];
            let afterFlight: RecoveryJobInput['after_flight_transfer'];
            try {
              afterFlight = afterFlightTransferInput(
                afterFlightTransfer,
                world,
                selectedGroups,
                modes,
                maxCalls,
                hotelMode || airportTransfers.length > 0,
              );
              transfers = airportTransferInputs(
                airportTransfers,
                world,
                selectedGroups,
                modes,
                maxCalls,
                hotelMode,
              );
            } catch (cause) {
              setActionError(
                cause instanceof Error ? cause.message : 'Review airport-transfer inputs.',
              );
              return;
            }
            if (hotelMode) {
              try {
                if (hotelPackage === 'romania_transfero') {
                  romanianHotelRecovery = romanianHotelRecoveryInput(
                    romanianHotelFields,
                    selectedGroups,
                    world,
                    maxCalls,
                  );
                } else {
                  hotelRecovery = hotelRecoveryInput(hotelFields, selectedGroups, world, maxCalls);
                }
              } catch (cause) {
                setActionError(
                  cause instanceof Error ? cause.message : 'Review the hotel recovery inputs.',
                );
                return;
              }
            }
            void mutate(() =>
              api.startRecoveryJob(world, incident.id, {
                group_ids: [...selectedGroups],
                objective,
                modes: hotelMode ? ['flight', 'hotel', 'bus'] : [...modes],
                max_provider_calls: maxCalls,
                instructions: instructions.trim(),
                ...(hotelRecovery ? { hotel_recovery: hotelRecovery } : {}),
                ...(romanianHotelRecovery
                  ? { romanian_hotel_recovery: romanianHotelRecovery }
                  : {}),
                ...(transfers.length ? { airport_transfers: transfers } : {}),
                ...(afterFlight ? { after_flight_transfer: afterFlight } : {}),
              }),
            );
          }}
        >
          <fieldset
            className="case-fieldset"
            disabled={locked || active || loading || Boolean(draftIssue)}
          >
            <legend className="field-hint">Groups to search for</legend>
            {world.mode === 'live' && (
              <fieldset className="case-job-modes hotel-recovery-choice">
                <legend>Recovery workflow</legend>
                <label>
                  <input
                    type="radio"
                    name="recovery-workflow"
                    checked={!hotelMode}
                    onChange={() => setHotelMode(false)}
                  />
                  Source alternatives
                </label>
                <label>
                  <input
                    type="radio"
                    name="recovery-workflow"
                    checked={hotelMode}
                    onChange={() => setHotelMode(true)}
                  />
                  Hotel + both transfers + onward flight
                </label>
              </fieldset>
            )}
            {hotelMode && (
              <>
                <label className="field">
                  Hotel recovery package
                  <select
                    value={hotelPackage}
                    onChange={(event) =>
                      updateDraft(
                        'hotelPackage',
                        event.target.value as 'uk_apl' | 'romania_transfero',
                      )
                    }
                  >
                    <option value="uk_apl">
                      UK · APL private transfers + sourced hotel + flight
                    </option>
                    <option value="romania_transfero">
                      Romania · Direct Aeroport + Hotel Aro Palace + flight
                    </option>
                  </select>
                </label>
                <p className="field-hint">
                  Choose exactly one linked, explicitly all-adult group.{' '}
                  {hotelPackage === 'romania_transfero'
                    ? 'This package begins at reported OTP and returns there before a real onward flight.'
                    : 'UK origin and onward-airport coverage are checked before source searches.'}{' '}
                  Package selection does not change your group selection or provider-call allowance.
                </p>
              </>
            )}
            <div className="case-group-options">
              {groups.map((group) => {
                const hotelIssue = hotelMode
                  ? (hotelPackage === 'romania_transfero'
                      ? romanianHotelGroupIssue
                      : hotelRecoveryGroupIssue)(group, world)
                  : null;
                return (
                  <label className="case-group" key={group.id}>
                    <input
                      type={hotelMode ? 'radio' : 'checkbox'}
                      name={hotelMode ? 'hotel-recovery-group' : undefined}
                      checked={
                        selectedGroups.includes(group.id) &&
                        (!hotelMode || (selectedGroups.length === 1 && !hotelIssue))
                      }
                      disabled={Boolean(groupIssues.get(group.id)) || Boolean(hotelIssue)}
                      onChange={(event) => {
                        if (hotelMode) {
                          setSelectedGroups([group.id]);
                          setHotelFields((previous) => ({
                            ...previous,
                            initial_point: { ...previous.initial_point, confirmed: false },
                            airport_dropoff: { ...previous.airport_dropoff, confirmed: false },
                          }));
                          return;
                        }
                        setSelectedGroups((previous) =>
                          event.target.checked
                            ? [...previous, group.id]
                            : previous.filter((id) => id !== group.id),
                        );
                      }}
                    />
                    <span>
                      <strong>
                        {group.name} · {group.size} passengers
                      </strong>
                      <small>
                        {passengerSourceLabel(group.source)} ·{' '}
                        {groupIssues.get(group.id) ?? `${group.origin} → ${group.destination}`}
                      </small>
                      {hotelIssue && <small>{hotelIssue}</small>}
                    </span>
                  </label>
                );
              })}
            </div>
            {!groups.length && (
              <p className="case-source-warning">
                Link passenger requirements to this case before starting a recovery search.
              </p>
            )}
            {selectedGroups.length > 0 && (
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setSelectedGroups([]);
                  setHotelFields((previous) => ({
                    ...previous,
                    initial_point: { ...previous.initial_point, confirmed: false },
                    airport_dropoff: { ...previous.airport_dropoff, confirmed: false },
                  }));
                }}
              >
                Clear search selection
              </button>
            )}
            <div className="case-form-grid">
              <label className="field">
                Objective
                <select
                  value={objective}
                  onChange={(event) =>
                    setObjective(event.target.value as RecoveryJobInput['objective'])
                  }
                >
                  <option value="balanced">Balanced</option>
                  <option value="fastest">Earliest arrival</option>
                  <option value="lowest_cost">Lowest cost</option>
                </select>
              </label>
              <label className="field">
                Maximum provider calls
                <input
                  type="number"
                  min={hotelMode ? (hotelPackage === 'romania_transfero' ? 8 : 10) : 3}
                  max={26}
                  step={1}
                  required
                  value={limit}
                  onChange={(event) => setLimit(event.target.value)}
                />
              </label>
            </div>
            {hotelMode && hotelPackage === 'romania_transfero' && (
              <>
                <RomanianHotelRecoveryFields
                  value={romanianHotelFields}
                  onChange={(value) => updateDraft('romanianHotelFields', value)}
                  world={world}
                  group={hotelGroup}
                  allowance={Number(limit)}
                />
                <SavedRomanianHotelInputs
                  world={world}
                  incident={incident}
                  groupIds={selectedGroups}
                  packageSelected={true}
                  incompatibleSetup={airportTransfers.length > 0 || afterFlightTransfer !== null}
                  allowance={Number(limit)}
                  draft={romanianHotelFields}
                  disabled={
                    locked || active || loading || Boolean(draftIssue) || Boolean(loadError)
                  }
                  onChange={(value) => updateDraft('romanianHotelFields', value)}
                />
              </>
            )}
            {hotelMode && hotelPackage === 'uk_apl' && (
              <>
                <p className={Number(limit) < 10 ? 'case-source-warning' : 'field-hint'}>
                  This workflow needs at least 10 provider calls. Your limit is {limit || 'not set'}
                  ; it is never raised automatically. Flights, hotel policy and both dated private
                  transfers are included in this explicit cap.
                </p>
                <HotelRecoveryFields
                  key={hotelGroup?.id ?? 'unselected'}
                  value={hotelFields}
                  onChange={setHotelFields}
                  group={hotelGroup}
                  disabled={locked || active || loading}
                />
              </>
            )}
            {!hotelMode && (
              <>
                <fieldset className="case-job-modes">
                  <legend>Services to consider</legend>
                  {(['flight', 'hotel', 'bus'] as const).map((mode) => (
                    <label key={mode}>
                      <input
                        type="checkbox"
                        checked={modes.includes(mode)}
                        onChange={(event) =>
                          setModes((previous) =>
                            event.target.checked
                              ? [...previous, mode]
                              : previous.filter((value) => value !== mode),
                          )
                        }
                      />
                      {mode === 'flight'
                        ? 'Flights'
                        : mode === 'hotel'
                          ? 'Hotels'
                          : 'Ground transport'}
                    </label>
                  ))}
                </fieldset>
                <p className="field-hint">
                  Unavailable supplier searches or missing physical connections are reported as
                  gaps. A listed hotel or route is not a confirmed journey.
                </p>
              </>
            )}
            {((!hotelMode && !afterFlightTransfer && modes.includes('bus')) ||
              airportTransfers.length > 0) && (
              <AirportTransferRecoveryFields
                world={world}
                groupIds={selectedGroups}
                modes={modes}
                allowance={Number(limit)}
                value={airportTransfers}
                onChange={setAirportTransfers}
                hotelRecovery={hotelMode}
              />
            )}
            {((!hotelMode && modes.includes('bus')) || Boolean(afterFlightTransfer)) && (
              <>
                <AfterFlightTransferFields
                  world={world}
                  groupIds={selectedGroups}
                  modes={modes}
                  allowance={Number(limit)}
                  value={afterFlightTransfer}
                  onChange={setAfterFlightTransfer}
                  incompatibleSetup={hotelMode || airportTransfers.length > 0}
                />
                {selectedGroups.length > 0 && (
                  <SavedAfterFlightInputs
                    world={world}
                    incident={incident}
                    groupIds={selectedGroups}
                    modes={modes}
                    allowance={Number(limit)}
                    draft={afterFlightTransfer}
                    onChange={setAfterFlightTransfer}
                    incompatibleSetup={hotelMode || airportTransfers.length > 0}
                    disabled={locked || active || loading}
                  />
                )}
              </>
            )}
            {!hotelMode &&
              !afterFlightTransfer &&
              modes.includes('bus') &&
              selectedGroups.length > 0 && (
                <SavedAirportTransferInputs
                  world={world}
                  incident={incident}
                  groupIds={selectedGroups}
                  value={airportTransfers}
                  onChange={setAirportTransfers}
                  disabled={locked || active || loading}
                />
              )}
            <label className="field">
              Additional recovery requirements
              <textarea
                value={instructions}
                maxLength={2000}
                rows={2}
                onChange={(event) => setInstructions(event.target.value)}
              />
            </label>
            <button
              className="primary-button"
              disabled={
                Boolean(selectionIssue) ||
                !selectedGroups.length ||
                selectedGroups.length > 24 ||
                (hotelMode ? !hotelGroup : !modes.length) ||
                Boolean(loadError)
              }
            >
              {jobs.length
                ? 'Start new provider search'
                : hotelMode
                  ? 'Search hotel and onward journey'
                  : 'Search provider alternatives'}
            </button>
          </fieldset>
        </form>
      )}
      {actionError && (
        <p className="form-error" role="alert">
          {actionError}
        </p>
      )}
      {busy && (
        <p className="case-feedback" role="status">
          <Loader2 className="spin" size={14} />
          Waiting for request acknowledgement…
        </p>
      )}
      {uncertain && (
        <div className="case-source-warning">
          <p>
            The request outcome is unknown. Recover the original request before closing or starting
            another attempt; its request ID will be reused.
          </p>
          <button
            className="secondary-button"
            disabled={busy}
            onClick={() => void mutate(() => uncertain.retry<RecoveryJobResponse>())}
          >
            <RefreshCw size={14} />
            Recover original request
          </button>
        </div>
      )}
      <button
        className="secondary-button case-job-refresh"
        disabled={busy}
        onClick={() => setRefresh((value) => value + 1)}
      >
        <RefreshCw size={13} />
        Refresh recorded progress
      </button>
    </section>
  );
}
