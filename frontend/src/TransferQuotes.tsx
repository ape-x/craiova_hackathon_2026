import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { CarFront, Loader2, Search } from 'lucide-react';
import { request, TransportError } from './api';
import { Tag } from './ui';
import {
  londonTimestamp,
  transferEuroEstimate,
  transferFxReady,
  transferFxReference,
  transferPrice,
  transferRequest,
} from './transferQuoteData';
import type { TransferQuoteForm, TransferQuoteResult, TransferStatus } from './transferQuoteData';
import './transferQuotes.css';
import { SupplierMeetingInstructions } from './TransferEvidence';

export function TransferQuoteResults({ result }: { result: TransferQuoteResult }) {
  const reference = transferFxReference(result.fx);
  const fxReady = transferFxReady(result.fx);
  const fxState = result.fx?.state;
  const fxLabel = fxReady
    ? 'ECB reference estimate'
    : fxState === 'stale_rate'
      ? 'ECB rate is stale'
      : fxState === 'missing_rate'
        ? 'GBP reference rate unavailable'
        : fxState === 'not_requested'
          ? 'EUR estimate not requested'
          : 'EUR estimate unavailable';
  return (
    <div className="transfer-results">
      <div className="transfer-receipt">
        <strong>
          {result.resolved_route.pickup ?? result.request.origin} →{' '}
          {result.resolved_route.dropoff ?? result.request.destination}
        </strong>
        <span>
          {result.request.transfer_date} at {result.request.transfer_time} · Europe/London
        </span>
        <span>
          {result.request.passengers} passengers · {result.request.suitcases} suitcases
        </span>
        <span>
          Received by this app {londonTimestamp(result.received_at)} · {result.provider}
        </span>
      </div>
      <p className="transfer-note">
        Price snapshot only.{' '}
        {result.expires_at
          ? `Supplier expiry: ${londonTimestamp(result.expires_at)}.`
          : 'Supplier expiry was not provided.'}{' '}
        Search again before relying on a price.
      </p>
      <SupplierMeetingInstructions value={result.meeting_point} />
      {result.message && (
        <p className="transfer-note" role="status">
          {result.message}
        </p>
      )}
      <section
        className={`transfer-fx ${fxReady ? '' : 'transfer-fx-unavailable'}`}
        aria-label="Currency reference"
        role="status"
      >
        <strong>{fxLabel}</strong>
        {reference && (
          <div className="transfer-fx-reference">
            <a href={reference.source_url} target="_blank" rel="noreferrer">
              European Central Bank
            </a>
            <span>Rate date: {reference.rate_date}</span>
            {fxReady && <span>1 EUR = {reference.source_units_per_eur} GBP</span>}
            <span>
              App freshness policy: at most {reference.max_age_days} calendar days ·{' '}
              {reference.age_timezone}
            </span>
            <span>
              Rate assessment: {londonTimestamp(reference.as_of)}
              {result.fx?.cache === 'cached' ? ' · cached reference' : ''}
            </span>
          </div>
        )}
        {!fxReady && (
          <p>
            {fxState === 'ready'
              ? 'The returned reference is incomplete or inconsistent.'
              : result.fx?.message || 'No usable exchange-rate reference was returned.'}{' '}
            GBP source prices remain unchanged; no EUR estimate is shown.
          </p>
        )}
        <p>
          Reference estimate only, not an APL EUR price. Revalidate before booking; EUR settlement
          is not guaranteed.
        </p>
      </section>
      {!result.options.length && (
        <p className="transfer-note">The source returned no vehicle quotes for this request.</p>
      )}
      <div className="transfer-vehicle-grid">
        {result.options.map((option) => {
          const euroEstimate = transferEuroEstimate(option, result.fx);
          return (
            <article className="transfer-vehicle" key={option.id}>
              <div className="transfer-vehicle-heading">
                <strong>{option.car_type}</strong>
                <Tag tone={option.fits_request ? 'cyan' : 'amber'}>
                  {option.fits_request ? 'CAPACITY FITS' : 'DOES NOT FIT'}
                </Tag>
              </div>
              <div className="transfer-price">
                <strong>{transferPrice(option.price.amount)}</strong>
                <span>GBP per vehicle</span>
              </div>
              <div className="transfer-euro-estimate">
                {euroEstimate ? (
                  <>
                    Approximately {euroEstimate} <span>EUR reference estimate per vehicle</span>
                  </>
                ) : (
                  'EUR reference estimate unavailable'
                )}
              </div>
              <p>
                Up to {option.max_passengers} passengers · {option.max_suitcases} suitcases
              </p>
            </article>
          );
        })}
      </div>
      <p className="transfer-note">
        Vehicle assignment and accessibility are unconfirmed. No booking, driver dispatch or
        supplier hold was made.
      </p>
      <p className="transfer-note">
        Journey duration and arrival time are not verified. These quotes and reference estimates are
        not added to an itinerary or the operation’s EUR budget.
      </p>
      {result.provider_duration_text && (
        <p className="transfer-note">
          Supplier’s unverified duration text: {result.provider_duration_text}
        </p>
      )}
    </div>
  );
}

export default function TransferQuotes() {
  const [form, setForm] = useState<TransferQuoteForm>({
    origin: '',
    destination: '',
    date: '',
    time: '',
    passengers: '',
    suitcases: '',
  });
  const [status, setStatus] = useState<TransferStatus | null>(null);
  const [setupError, setSetupError] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TransferQuoteResult | null>(null);
  const active = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void request<TransferStatus>('/transfers/status', undefined, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setStatus(next);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setSetupError(
            cause instanceof Error ? cause.message : 'Could not check APL configuration.',
          );
      });
    return () => {
      controller.abort();
      active.current?.abort();
      active.current = null;
    };
  }, []);

  function change(field: keyof TransferQuoteForm, value: string) {
    setForm((previous) => ({ ...previous, [field]: value }));
    setResult(null);
    setError('');
  }
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (active.current || !status?.configured) return;
    let body;
    try {
      body = transferRequest(form);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Check the pickup details.');
      return;
    }
    const controller = new AbortController();
    active.current = controller;
    setBusy(true);
    setResult(null);
    setError('');
    try {
      const next = await request<TransferQuoteResult>('/transfers/quote', body, controller.signal);
      if (active.current === controller && !controller.signal.aborted) setResult(next);
    } catch (cause) {
      if (active.current === controller && !controller.signal.aborted)
        setError(
          cause instanceof TransportError
            ? 'The quote request did not complete. A new search will contact APL again.'
            : cause instanceof Error
              ? cause.message
              : 'APL did not return a quote.',
        );
    } finally {
      if (active.current === controller) {
        active.current = null;
        setBusy(false);
      }
    }
  }
  return (
    <section className="transfer-quotes" aria-labelledby="transfer-heading">
      <div className="transfer-heading">
        <h3 id="transfer-heading">
          <CarFront size={17} aria-hidden="true" /> UK private transfers
        </h3>
        <Tag tone={status?.configured ? 'cyan' : 'amber'}>
          {status?.configured
            ? 'APL CONFIGURED'
            : status
              ? 'NOT CONFIGURED'
              : setupError
                ? 'STATUS UNAVAILABLE'
                : 'CHECKING SETUP'}
        </Tag>
      </div>
      <p className="transfer-note">
        Request a current Airport Pickups London vehicle price for a UK airport, address or
        postcode. Enter the actual party and luggage counts. This search does not reserve transport.
      </p>
      {setupError && (
        <p className="form-error" role="alert">
          {setupError}
        </p>
      )}
      {status && !status.configured && (
        <p className="transfer-note">
          A registered APL key must be configured on the server before prices can be requested.
        </p>
      )}
      <form
        onSubmit={(event) => {
          void search(event);
        }}
      >
        <fieldset className="transfer-fields" disabled={busy}>
          <legend className="real-sr-only">APL transfer quote details</legend>
          <label className="field">
            UK pickup location
            <input
              value={form.origin}
              maxLength={500}
              placeholder="Airport terminal, full address or postcode"
              onChange={(event) => change('origin', event.target.value)}
              required
            />
          </label>
          <label className="field">
            UK dropoff location
            <input
              value={form.destination}
              maxLength={500}
              placeholder="Full address, postcode or airport terminal"
              onChange={(event) => change('destination', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Pickup date · London
            <input
              type="date"
              value={form.date}
              onChange={(event) => change('date', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Pickup time · Europe/London
            <input
              type="time"
              step={60}
              value={form.time}
              onChange={(event) => change('time', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Passengers
            <input
              type="number"
              min={1}
              max={100}
              step={1}
              value={form.passengers}
              onChange={(event) => change('passengers', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Suitcases / large bags
            <input
              type="number"
              min={0}
              max={200}
              step={1}
              value={form.suitcases}
              onChange={(event) => change('suitcases', event.target.value)}
              required
            />
          </label>
        </fieldset>
        <div className="transfer-submit">
          <span className="transfer-note">Prices are GBP for the entire vehicle.</span>
          <button className="primary-button" disabled={busy || !status?.configured} type="submit">
            {busy ? (
              <Loader2 className="real-data-spinner" size={15} aria-hidden="true" />
            ) : (
              <Search size={15} aria-hidden="true" />
            )}
            {busy ? 'Requesting APL quotes…' : 'Get current quotes'}
          </button>
        </div>
      </form>
      <div aria-live="polite">
        {busy && (
          <p className="transfer-note" role="status">
            Contacting Airport Pickups London…
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      {result && <TransferQuoteResults result={result} />}
    </section>
  );
}
