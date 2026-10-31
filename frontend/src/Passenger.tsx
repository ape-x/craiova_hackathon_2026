import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import {
  Accessibility,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock3,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Users,
} from 'lucide-react';
import { ApiError, TransportError, formatTime, formatOperationTime, key, request } from './api';
import type { Airport, Offer, PassengerGroup, Resource } from './types';
import { Brand, Dot, Tag } from './ui';
import { ItineraryView } from './Inspector';
import ThemeToggle from './ThemeToggle';
import PassengerHistory from './PassengerHistory';
import { sourcedArrival } from './journeyPresentation';
import { costPresentation, EstimatedCostNote } from './priceEvidence';
import {
  LIVE_PASSENGER_HORIZON_MINUTES,
  parsePassengerDeadlineUtc,
  passengerDeadlineUtcInput,
} from './passengerDeadline';

interface PassengerState {
  version: number;
  epoch: string;
  sim_time: number;
  group: Omit<PassengerGroup, 'token'>;
  offer: Offer | null;
  last_offer: Offer | null;
  airports: Airport[];
  resources: Resource[];
  history_resources?: Resource[];
  history_airports?: Airport[];
  history_archived_resource_ids?: string[];
  simulated: boolean;
  currency: string;
}
type OfferAction = 'accept' | 'decline';
interface OfferResult {
  offer: Offer;
  version: number;
}
interface UncertainOfferAction {
  offerId: string;
  action: OfferAction;
  error: TransportError;
}
function readPassengerToken() {
  try {
    return decodeURIComponent(location.hash.slice(1));
  } catch {
    return '';
  }
}
function subscribePassengerToken(changed: () => void) {
  window.addEventListener('hashchange', changed);
  return () => window.removeEventListener('hashchange', changed);
}
export default function Passenger() {
  const token = useSyncExternalStore(subscribePassengerToken, readPassengerToken);
  // A new boarding link owns fresh state, timers and pending actions. Never
  // display the previous family's offer while another token is loading.
  return <PassengerJourney key={token} token={token} />;
}
function PassengerJourney({ token }: { token: string }) {
  const [state, setState] = useState<PassengerState | null>(null);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState<OfferAction | null>(null);
  const [uncertain, setUncertain] = useState<UncertainOfferAction | null>(null);
  const [editing, setEditing] = useState(false);
  const [deadline, setDeadline] = useState('22:00');
  const [liveDeadline, setLiveDeadline] = useState('');
  const [accessible, setAccessible] = useState(false);
  const [deadlineDay, setDeadlineDay] = useState(0);
  const [now, setNow] = useState(Date.now());
  const offerKeys = useRef(new Map<string, string>());
  const refresh = useCallback(async () => {
    if (!token)
      throw new Error(
        'This boarding link is missing its passenger access token. Open the QR link from the operations screen.',
      );
    const next = await request<PassengerState>(`/passenger/${encodeURIComponent(token)}`);
    setState((previous) => (previous && previous.version > next.version ? previous : next));
    setLoadError('');
    return next;
  }, [token]);
  useEffect(() => {
    let stopped = false;
    let timeout: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const load = async () => {
      try {
        if (!token)
          throw new Error('Open the passenger link from the QR code in the operations screen.');
        const next = await request<PassengerState>(
          `/passenger/${encodeURIComponent(token)}`,
          undefined,
          controller.signal,
        );
        if (!stopped) {
          setState((previous) => (previous && previous.version > next.version ? previous : next));
          setLoadError('');
        }
      } catch (cause) {
        if (!stopped) {
          if (cause instanceof ApiError && [401, 403, 404].includes(cause.status)) setState(null);
          setLoadError(cause instanceof Error ? cause.message : 'Unable to load your itinerary');
        }
      } finally {
        if (!stopped) timeout = setTimeout(load, document.hidden ? 15000 : 3000);
      }
    };
    void load();
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timeout);
      clearInterval(clock);
    };
  }, [token]);
  const offer = state?.offer;
  const seconds = offer ? Math.max(0, Math.ceil((Date.parse(offer.expires_at) - now) / 1000)) : 0;
  const confirmed = offer?.status === 'confirmed';
  const accepted = offer?.status === 'accepted';
  const live = state?.simulated === false;
  const declined = !offer && state?.last_offer?.status === 'declined';
  useEffect(() => {
    if (
      uncertain &&
      state &&
      (state.offer?.id !== uncertain.offerId || !['held', 'proposed'].includes(state.offer.status))
    ) {
      setUncertain(null);
      setError('');
    }
  }, [state, uncertain]);
  async function answerOffer(action: OfferAction, retry?: UncertainOfferAction) {
    const offerId = retry?.offerId ?? offer?.id;
    if (!offerId || busy) return;
    const identity = `${action}:${offerId}`;
    let idempotencyKey = offerKeys.current.get(identity);
    if (!idempotencyKey) {
      idempotencyKey = key();
      offerKeys.current.set(identity, idempotencyKey);
    }
    setBusy(true);
    setBusyAction(action);
    setError('');
    try {
      const result = retry
        ? await retry.error.retry<OfferResult>()
        : await request<OfferResult>(`/offers/${encodeURIComponent(offerId)}/${action}`, {
            token,
            idempotency_key: idempotencyKey,
          });
      setUncertain(null);
      // Reflect the acknowledged transaction immediately even if the following
      // refresh loses its connection. A newer polled version always wins.
      setState((previous) =>
        previous && previous.version <= result.version
          ? {
              ...previous,
              version: result.version,
              offer: ['confirmed', 'accepted'].includes(result.offer.status) ? result.offer : null,
              last_offer: result.offer,
              group: {
                ...previous.group,
                status:
                  result.offer.status === 'confirmed'
                    ? 'confirmed'
                    : result.offer.status === 'accepted'
                      ? 'offered'
                      : 'waiting',
              },
            }
          : previous,
      );
      await refresh().catch(() =>
        setLoadError('Your answer was saved. Reconnecting to refresh your journey.'),
      );
    } catch (cause) {
      if (cause instanceof TransportError) setUncertain({ offerId, action, error: cause });
      else setUncertain(null);
      setError(cause instanceof Error ? cause.message : 'Could not save your answer');
      await refresh().catch(() => {});
    } finally {
      setBusy(false);
      setBusyAction(null);
    }
  }
  return (
    <main className="passenger-page">
      <header>
        <Brand compact />
        <div className="passenger-header-actions">
          <Tag tone={live ? 'cyan' : 'amber'}>
            {state ? (live ? 'LIVE · ASSISTED' : 'SIMULATION') : 'YOUR JOURNEY'}
          </Tag>
          <ThemeToggle />
        </div>
      </header>
      <div className="passenger-content">
        {!state ? (
          <div className="passenger-loading">
            {loadError || error ? (
              <>
                <h1>Your journey link</h1>
                <p role="alert">{loadError || error}</p>
              </>
            ) : (
              <>
                <Loader2 className="spin" size={30} />
                <h1>Finding your journey…</h1>
              </>
            )}
          </div>
        ) : (
          <>
            <div className="passenger-greeting">
              <span className="eyebrow">YOUR JOURNEY, RECONNECTED</span>
              <h1>
                {confirmed
                  ? live
                    ? 'Your bookings are confirmed.'
                    : 'You’re all set.'
                  : accepted
                    ? 'Your choice is saved.'
                    : offer
                      ? 'A new way forward.'
                      : declined
                        ? 'Let’s find another way.'
                        : 'We’re finding your way.'}
              </h1>
              <p>
                {state.group.name} <span>·</span> {state.group.size} passengers
              </p>
            </div>
            <div className="boarding-route">
              <div>
                <span>FROM</span>
                <strong>{state.group.origin}</strong>
                <p>
                  {state.airports.find((item) => item.id === state.group.origin)?.name ??
                    state.group.origin}
                </p>
              </div>
              <div className="boarding-route-line">
                <i />
                <ArrowRight size={22} />
                <i />
              </div>
              <div>
                <span>TO</span>
                <strong>{state.group.destination}</strong>
                <p>
                  {state.airports.find((item) => item.id === state.group.destination)?.name ??
                    state.group.destination}
                </p>
              </div>
            </div>
            <section className={`boarding-card ${confirmed ? 'confirmed' : ''}`}>
              <div className="boarding-card-heading" aria-live="polite">
                {confirmed ? (
                  <CheckCircle2 size={20} />
                ) : offer ? (
                  <ShieldCheck size={20} />
                ) : (
                  <RefreshCw size={20} />
                )}
                <div>
                  <h2>
                    {confirmed
                      ? 'Your itinerary is confirmed'
                      : accepted
                        ? 'Awaiting supplier bookings'
                        : offer
                          ? 'Your recovery offer'
                          : declined
                            ? 'Offer declined'
                            : state.last_offer?.status === 'invalidated'
                              ? 'Your previous journey was affected'
                              : 'Recovery in progress'}
                  </h2>
                  <span>
                    {confirmed
                      ? live
                        ? 'Supplier confirmations recorded by operations'
                        : 'All segments secured together in the simulation'
                      : accepted
                        ? 'Operations will confirm each booking with the suppliers'
                        : offer
                          ? live
                            ? 'Current proposal; no supplier booking has been made'
                            : 'All resources held while you decide'
                          : declined
                            ? live
                              ? 'Your preference has been recorded'
                              : 'Your held seats and rooms have been released'
                            : 'The operations team is recalculating your options'}
                  </span>
                </div>
              </div>
              {offer ? (
                <>
                  <ItineraryView
                    offer={offer}
                    world={{
                      mode: live ? 'live' : 'simulation',
                      epoch: state.epoch,
                      resources: state.resources,
                      airports: state.airports,
                    }}
                  />
                  <div className="boarding-facts">
                    <span>
                      <Users size={15} /> {state.group.size} people stay together
                    </span>
                    {state.group.accessible && (
                      <span>
                        <Accessibility size={15} />{' '}
                        {live ? 'Accessible travel requested' : 'Accessible throughout'}
                      </span>
                    )}
                    <span>
                      <Clock3 size={15} /> Arrival{' '}
                      {live
                        ? (sourcedArrival(offer, state.resources, state.epoch) ??
                          `${formatOperationTime(state.epoch, offer.itinerary.arrival)} · Europe/Bucharest operations time`)
                        : formatTime(offer.itinerary.arrival)}
                    </span>
                  </div>
                  <div
                    className={`boarding-total${costPresentation(offer.itinerary).estimated ? ' estimated-total' : ''}`}
                  >
                    <span>
                      {costPresentation(offer.itinerary).estimated
                        ? 'Estimated itinerary total'
                        : 'Complete group itinerary'}
                    </span>
                    <strong>{costPresentation(offer.itinerary).amount}</strong>
                  </div>
                  <EstimatedCostNote value={offer.itinerary} />
                  {!confirmed && !accepted && (
                    <>
                      <div className="hold-countdown">
                        <Dot tone={seconds < 60 ? 'amber' : 'mint'} />
                        <span>
                          {seconds > 0
                            ? `${live ? 'Proposal valid for' : 'Held for'} ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
                            : 'This hold has expired. Checking for an updated offer.'}
                        </span>
                      </div>
                      <button
                        className="accept-offer"
                        disabled={busy || seconds <= 0 || Boolean(uncertain)}
                        onClick={() => void answerOffer('accept')}
                      >
                        {busyAction === 'accept' ? (
                          <Loader2 size={18} className="spin" />
                        ) : (
                          <Check size={18} />
                        )}{' '}
                        {live ? 'Accept this proposal' : 'Accept this itinerary'}
                      </button>
                      <button
                        className="decline-offer"
                        disabled={busy || seconds <= 0 || Boolean(uncertain)}
                        onClick={() => void answerOffer('decline')}
                      >
                        {busyAction === 'decline' && <Loader2 size={16} className="spin" />}
                        Decline this offer
                      </button>
                      <p className="decline-offer-hint">
                        {live
                          ? 'Choosing a proposal records your preference. Only supplier confirmations complete your bookings.'
                          : 'Declining releases every held segment for your group. You can update your needs while the team finds another option.'}
                      </p>
                    </>
                  )}
                </>
              ) : (
                <div className="waiting-journey">
                  <div className="waiting-path">
                    <i />
                    <i />
                    <i />
                  </div>
                  <p>
                    {state.last_offer?.reason ??
                      'You will see a complete, validated itinerary here as soon as one is available.'}
                  </p>
                  <small>This page updates automatically.</small>
                </div>
              )}
            </section>
            {live && !offer && (
              <PassengerHistory
                offer={state.last_offer}
                epoch={state.epoch}
                resources={state.history_resources}
                airports={state.history_airports}
                archivedResourceIds={state.history_archived_resource_ids}
              />
            )}
            {(loadError || error) && (
              <p className="form-error" role="alert">
                {loadError || error}
              </p>
            )}
            {uncertain && (
              <button
                className="retry-offer"
                disabled={busy}
                onClick={() => void answerOffer(uncertain.action, uncertain)}
              >
                <RefreshCw size={16} className={busy ? 'spin' : ''} />
                {uncertain.action === 'accept' ? 'Retry confirmation' : 'Retry decline'}
              </button>
            )}
            <button
              className="passenger-preferences"
              onClick={() => {
                setEditing((value) => !value);
                setAccessible(state.group.accessible);
                if (live) {
                  setLiveDeadline(passengerDeadlineUtcInput(state.epoch, state.group.deadline));
                } else {
                  setDeadline(formatTime(state.group.deadline).slice(0, 5));
                  setDeadlineDay(Math.floor(state.group.deadline / 1440));
                }
              }}
            >
              <span>Something we should know?</span>
              <span>
                {editing ? 'Close' : 'Update your needs'}
                <ArrowRight size={14} />
              </span>
            </button>
            {editing && (
              <form
                className="passenger-constraints"
                onSubmit={async (event) => {
                  event.preventDefault();
                  setBusy(true);
                  setError('');
                  const [hours, minutes] = deadline.split(':').map(Number);
                  try {
                    const updatedDeadline = live
                      ? parsePassengerDeadlineUtc(liveDeadline, state.epoch, state.sim_time)
                      : deadlineDay * 1440 + hours * 60 + minutes;
                    await request(`/passenger/${encodeURIComponent(token)}/constraints`, {
                      idempotency_key: key(),
                      deadline: updatedDeadline,
                      accessible,
                    });
                    await refresh();
                    setEditing(false);
                  } catch (cause) {
                    setError(
                      cause instanceof Error ? cause.message : 'Could not update preferences',
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {live ? (
                  <>
                    <label className="field">
                      Arrive by (UTC)
                      <input
                        type="datetime-local"
                        min={passengerDeadlineUtcInput(state.epoch, state.sim_time)}
                        max={passengerDeadlineUtcInput(
                          state.epoch,
                          state.sim_time + LIVE_PASSENGER_HORIZON_MINUTES,
                        )}
                        step={60}
                        value={liveDeadline}
                        onChange={(event) => setLiveDeadline(event.target.value)}
                        required
                      />
                    </label>
                    <p className="field-hint">
                      Enter UTC time, within seven days of the current server time. Your existing
                      deadline is not moved automatically.
                    </p>
                  </>
                ) : (
                  <>
                    <label className="field">
                      Arrival day
                      <select
                        value={deadlineDay}
                        onChange={(event) => setDeadlineDay(Number(event.target.value))}
                      >
                        {Array.from({ length: Math.max(3, deadlineDay + 1) }, (_, day) => (
                          <option key={day} value={day}>
                            {day === 0 ? 'Scenario day' : day === 1 ? 'Next day' : `Day ${day + 1}`}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      Arrive by
                      <input
                        type="time"
                        min={deadlineDay === 0 ? formatTime(state.sim_time).slice(0, 5) : undefined}
                        value={deadline}
                        onChange={(event) => setDeadline(event.target.value)}
                        required
                      />
                    </label>
                  </>
                )}
                <label className="check-field">
                  <input
                    type="checkbox"
                    checked={accessible}
                    onChange={(event) => setAccessible(event.target.checked)}
                  />{' '}
                  Accessible transport required
                </label>
                <p className="field-hint">
                  A change withdraws an incompatible offer and asks the operator to replan your
                  journey.
                </p>
                <button className="primary-button" disabled={busy || Boolean(uncertain)}>
                  Save requirements
                  <ArrowRight size={14} />
                </button>
              </form>
            )}
            <div className="passenger-trust">
              <ShieldCheck size={16} />
              <p>
                {live
                  ? 'Your selection is shared with the operations team. It does not purchase tickets or rooms; supplier bookings require confirmation by the team.'
                  : 'Connected to the same live simulation as the operations desk. No real booking or payment is made.'}
              </p>
            </div>
            <div className="passenger-reference">
              <code>{state.group.id}</code>
              <span>STATE v{state.version}</span>
            </div>
          </>
        )}
      </div>
      <footer>
        Recovery Live <span>·</span> Every journey matters.
      </footer>
    </main>
  );
}
