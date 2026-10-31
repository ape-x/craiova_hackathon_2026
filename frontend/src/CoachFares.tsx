import { useEffect, useId, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowUpRight, BusFront, ChevronDown, Loader2, Search } from 'lucide-react';
import { request, TransportError } from './api';
import { addCalendarDays, bucharestDate } from './realData';
import { Tag } from './ui';
import {
  CoachFareSearch,
  coachEuroEstimate,
  coachFxReference,
  coachMoney,
  coachObservedAt,
  coachRequest,
  coachSourceUrl,
  verifyCoachFareResult,
  verifyCoachFareStatus,
} from './coachFareData';
import type { CoachFareForm, CoachFareResult, CoachFareStatus } from './coachFareData';
import './coachFares.css';

export function CoachFareResults({
  result,
  historical = false,
}: {
  result: CoachFareResult;
  historical?: boolean;
}) {
  let verified: CoachFareResult;
  try {
    verified = verifyCoachFareResult(result);
  } catch {
    return (
      <p className="form-error" role="alert">
        The recorded coach fare evidence is incomplete or inconsistent. No transport availability
        can be inferred.
      </p>
    );
  }
  const reference = coachFxReference(verified.fx);
  const hasEstimate = verified.options.some((option) => coachEuroEstimate(option, verified.fx));
  const sourceUrl = coachSourceUrl(verified.source_url, verified.request);
  return (
    <section
      className="coach-results"
      aria-label={historical ? 'Recorded coach fare observations' : 'Coach fare observations'}
    >
      <div className="coach-receipt">
        <strong>
          {verified.request.origin_airport} → {verified.request.destination_airport} ·{' '}
          {verified.request.travel_date}
        </strong>
        <span>
          {verified.request.adults} adults · {verified.provider}
        </span>
        <span>
          {historical ? 'Historical observation' : 'Observed by this application'}:{' '}
          {coachObservedAt(verified.received_at)}
        </span>
      </div>
      <p className="coach-boundary">
        Seat availability and supplier expiry are unknown. These fares are not allocated to a
        recovery plan, and no seats are held or booked.
      </p>
      <p className="coach-note">
        Times below are the source’s local clock times. The source did not supply a UTC offset.
        Verify boarding access, luggage terms and connections before relying on a service.
      </p>
      <div className="coach-options">
        {verified.options.map((option) => {
          const estimate = coachEuroEstimate(option, verified.fx);
          return (
            <details className="coach-option" key={option.id}>
              <summary>
                <div className="coach-option-main">
                  <strong>
                    {option.departure_time} → {option.arrival_time}
                  </strong>
                  <span>{option.operator}</span>
                  <span>
                    {option.duration_text}
                    {option.arrival_date !== option.departure_date
                      ? ` · arrives ${option.arrival_date}`
                      : ''}
                  </span>
                </div>
                <div className="coach-option-price">
                  <strong>
                    {coachMoney(option.price.amount)} <small>RON</small>
                  </strong>
                  <span>Total for {option.adults} adults</span>
                  {estimate && <span>Approximately {estimate}</span>}
                </div>
                <ChevronDown size={14} aria-hidden="true" />
              </summary>
              <div className="coach-option-details">
                <dl>
                  <div>
                    <dt>Departure</dt>
                    <dd>
                      {option.departure_date} · {option.departure_time}
                      <br />
                      {option.departure_stop.locality} · {option.departure_stop.name}
                    </dd>
                  </div>
                  <div>
                    <dt>Arrival</dt>
                    <dd>
                      {option.arrival_date} · {option.arrival_time}
                      <br />
                      {option.arrival_stop.locality} · {option.arrival_stop.name}
                    </dd>
                  </div>
                </dl>
                <p>
                  {option.purchase_action_enabled
                    ? 'A purchase action was shown by the source; this does not confirm seats for the requested party.'
                    : 'The source did not show an enabled purchase action for this observation.'}
                </p>
                {option.notices.map((notice, index) => (
                  <p key={index}>{notice}</p>
                ))}
                <p>Accessibility, luggage acceptance and boarding access are unverified.</p>
              </div>
            </details>
          );
        })}
      </div>
      {!verified.options.length && (
        <p className="coach-note">
          No supported dated fares were returned for this request. This does not prove that no
          transport exists.
        </p>
      )}
      <div className="coach-fx" aria-label="Coach currency reference">
        <strong>{hasEstimate ? 'Approximate EUR reference' : 'EUR estimate unavailable'}</strong>
        {reference && (
          <p>
            European Central Bank · rate dated {reference.rate_date}
            {hasEstimate && reference.source_units_per_eur
              ? ` · 1 EUR = ${reference.source_units_per_eur} RON`
              : ''}
            <br />
            Assessed {coachObservedAt(reference.as_of)} · at most {reference.max_age_days} calendar
            days ({reference.age_timezone})
          </p>
        )}
        <p>
          Original RON fares remain unchanged. EUR values are reference estimates, not supplier EUR
          prices or amounts allocated to the operation’s budget. Revalidate before booking; EUR
          settlement is not guaranteed.
        </p>
        {verified.fx?.source_currency === 'RON' &&
          verified.fx.state !== 'ready' &&
          typeof verified.fx.message === 'string' && <p>{verified.fx.message}</p>}
      </div>
      {verified.rejected_count > 0 && (
        <p className="coach-note">
          {verified.rejected_count} source entries could not be represented with complete, supported
          fare evidence.
        </p>
      )}
      {verified.notices.map((notice, index) => (
        <p className="coach-note" key={index}>
          {notice}
        </p>
      ))}
      {sourceUrl && (
        <a className="coach-source-link" href={sourceUrl} target="_blank" rel="noopener noreferrer">
          Review dated search on BILETERIA <ArrowUpRight size={12} aria-hidden="true" />
        </a>
      )}
    </section>
  );
}

export default function CoachFares() {
  const headingId = useId();
  const [status, setStatus] = useState<CoachFareStatus | null>(null);
  const [setupError, setSetupError] = useState('');
  const [form, setForm] = useState<CoachFareForm>(() => ({
    origin_airport: 'CRA',
    destination_airport: 'OTP',
    travel_date: addCalendarDays(bucharestDate(), 1),
    adults: '',
  }));
  const [session] = useState(() => new CoachFareSearch());
  const [result, setResult] = useState<CoachFareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void request<unknown>('/coaches/status', undefined, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setStatus(verifyCoachFareStatus(value));
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setSetupError(
            cause instanceof Error ? cause.message : 'Coach fare source status is unavailable.',
          );
      });
    return () => {
      controller.abort();
      session.invalidate();
    };
  }, [session]);
  function change(key: keyof CoachFareForm, value: string) {
    session.invalidate();
    setBusy(false);
    setForm((previous) => ({ ...previous, [key]: value }));
    setResult(null);
    setError('');
  }
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!status || session.pending) return;
    let body;
    try {
      body = coachRequest(form, status);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Check the requested route and party.');
      return;
    }
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const next = await session.search(body);
      if (next) setResult(next);
    } catch (cause) {
      setError(
        cause instanceof TransportError
          ? 'The fare search did not complete. No automatic retry was made; a new search will contact the source again.'
          : cause instanceof Error
            ? cause.message
            : 'The source did not return usable fares.',
      );
    } finally {
      if (!session.pending) setBusy(false);
    }
  }
  return (
    <section className="coach-fares" aria-labelledby={headingId}>
      <div className="coach-heading">
        <h3 id={headingId}>
          <BusFront size={17} aria-hidden="true" /> Romanian airport coaches
        </h3>
        <Tag tone="amber">FARE REFERENCES</Tag>
      </div>
      <p className="coach-note">
        Search public BILETERIA fares for an explicit date and adult party. No account is needed for
        this search. Seat availability remains unverified.
      </p>
      {setupError && (
        <p className="form-error" role="alert">
          {setupError}
        </p>
      )}
      {status && !status.configured && (
        <p className="coach-note">The coach fare source is currently unavailable.</p>
      )}
      <form
        onSubmit={(event) => {
          void search(event);
        }}
      >
        <fieldset className="coach-fields" disabled={busy || !status?.configured}>
          <legend className="real-sr-only">Romanian coach fare search</legend>
          <label className="field coach-route-field">
            Airport route
            <select
              value={`${form.origin_airport}-${form.destination_airport}`}
              onChange={(event) => {
                const route = status?.supported_routes.find(
                  (item) =>
                    `${item.origin_airport}-${item.destination_airport}` === event.target.value,
                );
                if (route) {
                  change('origin_airport', route.origin_airport);
                  change('destination_airport', route.destination_airport);
                }
              }}
              required
            >
              {(status?.supported_routes ?? []).map((route) => (
                <option
                  key={`${route.origin_airport}-${route.destination_airport}`}
                  value={`${route.origin_airport}-${route.destination_airport}`}
                >
                  {route.origin_airport} · {route.origin_name} → {route.destination_airport} ·{' '}
                  {route.destination_name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Coach departure date
            <input
              type="date"
              min={bucharestDate()}
              max={addCalendarDays(bucharestDate(), status?.max_advance_days ?? 31)}
              value={form.travel_date}
              onChange={(event) => change('travel_date', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Adult passengers
            <input
              type="number"
              min={1}
              max={Math.min(8, status?.adult_limit ?? 8)}
              step={1}
              value={form.adults}
              onChange={(event) => change('adults', event.target.value)}
              required
            />
          </label>
        </fieldset>
        <div className="coach-submit">
          <span className="coach-note">Adult fares only · no seat hold or booking</span>
          <button className="primary-button" type="submit" disabled={busy || !status?.configured}>
            {busy ? (
              <Loader2 size={15} className="real-data-spinner" aria-hidden="true" />
            ) : (
              <Search size={15} aria-hidden="true" />
            )}
            {busy ? 'Reading dated fares…' : 'Find coach fares'}
          </button>
        </div>
      </form>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="coach-note" role="status">
          Checking the public source for this exact date and party…
        </p>
      )}
      {result && <CoachFareResults result={result} />}
    </section>
  );
}
