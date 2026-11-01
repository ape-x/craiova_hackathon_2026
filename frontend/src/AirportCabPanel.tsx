import { useEffect, useRef, useState } from 'react';
import { CarFront, Loader2, Plus, RefreshCw, Search } from 'lucide-react';
import { SearchOutcomeUnknownError, TransportError } from './api';
import { freshnessTime, sourceInstant, useFreshnessClock } from './resourceFreshness';
import type { MutationResult, World } from './types';
import {
  AIRPORT_CAB_SOURCE,
  AirportCabRequestGate,
  airportCabApi,
  airportCabContext,
  airportCabCurrent,
  airportCabDefaultPickup,
  airportCabGroupIssue,
  airportCabInput,
  verifyAirportCabQuote,
} from './airportCabData';
import type {
  AirportCabForm,
  AirportCabInput,
  AirportCabQuote,
  AirportCabStatus,
} from './airportCabData';
import './airportCab.css';

const emptyForm: AirportCabForm = { group_id: '', pickup_at: '', suitcases: '', handbags: '' };
const timestamp = (value: string) => {
  const instant = sourceInstant(value);
  return instant === null ? 'Unknown' : freshnessTime(instant);
};

export function AirportCabResults({
  result,
  now,
  pending,
  contextCurrent,
  importedOptionId,
  onImport,
}: {
  result: AirportCabQuote;
  now: number;
  pending: boolean;
  contextCurrent: boolean;
  importedOptionId: string | null;
  onImport: (optionId: string) => void;
}) {
  const transfer = result.transfer;
  const current = airportCabCurrent(result, now);
  return (
    <section className="airport-cab-results" aria-label="Bucharest.CAB transfer receipt">
      <div className="airport-cab-heading">
        <h4>{result.state === 'ready' ? 'Vehicle fare options' : 'Transfer quote blocked'}</h4>
        <span className="airport-cab-provider">Bucharest.CAB</span>
      </div>
      <p className="airport-cab-boundary">
        Supplier-calculated EUR fare per vehicle · cash, no extras. Vehicle limits do not confirm
        fleet availability. No reservation or hold.
      </p>
      <p className="field-hint">
        App received {timestamp(transfer?.received_at ?? result.created_at)} · import window through{' '}
        {timestamp(result.usable_until)}. This is application freshness, not supplier expiry.
      </p>
      {transfer && (
        <>
          <div className="airport-cab-route">
            <strong>OTP → CRA</strong>
            <span>
              {transfer.request.passengers} adults · {transfer.request.suitcases} suitcases ·{' '}
              {transfer.request.handbags} handbags
            </span>
            <span>Pickup {timestamp(transfer.request.pickup_at)}</span>
            <span>
              Supplier local time {transfer.request.supplier_pickup_datetime} ·{' '}
              {transfer.request.timezone}
            </span>
          </div>
          <p className="field-hint">
            {transfer.route.duration_text} driving ·{' '}
            {(transfer.route.distance_meters / 1000).toFixed(1)} km. Source estimate without
            departure-time traffic; arrival is not guaranteed.
          </p>
        </>
      )}
      {!contextCurrent && !importedOptionId && (
        <p role="status" className="text-amber">
          The group’s recorded details changed after this search. Request a new quote before
          importing.
        </p>
      )}
      {result.state === 'ready' && !current && !importedOptionId && (
        <p role="status" className="text-amber">
          The app import window has ended. Search again explicitly for a current receipt.
        </p>
      )}
      {result.blockers.length > 0 && (
        <ul className="airport-cab-blockers">
          {result.blockers.map((blocker, index) => (
            <li key={`${blocker.code}-${index}`}>{blocker.message}</li>
          ))}
        </ul>
      )}
      <div className="airport-cab-options">
        {transfer?.options.map((option) => {
          const eligibility = result.options.find((item) => item.option_id === option.id);
          const imported = importedOptionId === option.id;
          return (
            <article className="airport-cab-option" key={option.id}>
              <div className="airport-cab-heading">
                <h5>{option.car_type}</h5>
                <strong className="airport-cab-price">
                  €{option.price.amount}
                  <small>per vehicle</small>
                </strong>
              </div>
              <p>
                Vehicle limits: {option.max_passengers} passengers · {option.max_suitcases}{' '}
                suitcases · {option.max_handbags} handbags
              </p>
              {!option.fits_request && (
                <p className="text-amber">Does not fit the requested party and bags.</p>
              )}
              {(eligibility?.blockers ?? []).map((blocker, index) => (
                <p className="text-amber" key={`${blocker.code}-${index}`}>
                  {blocker.message}
                </p>
              ))}
              <button
                type="button"
                className="secondary-button"
                disabled={
                  pending ||
                  !current ||
                  !contextCurrent ||
                  importedOptionId !== null ||
                  !eligibility?.importable ||
                  !option.fits_request
                }
                onClick={() => onImport(option.id)}
              >
                <Plus size={14} aria-hidden="true" />
                {imported ? 'Planning leg added' : 'Add group planning leg'}
              </button>
            </article>
          );
        })}
      </div>
      {transfer && (
        <details className="airport-cab-source">
          <summary>Source details and planning limits</summary>
          <p>
            Only the selected group’s initial OTP → CRA leg is imported. An onward flight or
            complete itinerary must be planned separately.
          </p>
          <p>
            Airport-level pickup and drop-off references; terminal, curb access and accessible
            vehicle suitability are unverified. Bag fit is checked against published vehicle limits;
            bags were not sent to the supplier quote endpoint.
          </p>
          <p>
            Supplier quote time and expiry: unknown. Remaining fleet: unknown. The application can
            use at most one Bucharest.CAB vehicle in a plan until additional vehicle availability is
            verified.
          </p>
          <p>Payment-link and optional-extra fees are excluded from the cash fare.</p>
          <p>
            {transfer.resolved_route.origin.name} · {transfer.resolved_route.origin.address}
            <br />
            {transfer.resolved_route.destination.name} ·{' '}
            {transfer.resolved_route.destination.address}
          </p>
          {transfer.notices.map((notice, index) => (
            <p key={index}>{notice}</p>
          ))}
          <a href={AIRPORT_CAB_SOURCE} target="_blank" rel="noopener noreferrer">
            View supplier source
          </a>
        </details>
      )}
    </section>
  );
}

interface Attempt {
  kind: 'quote' | 'import';
  action: () => Promise<unknown>;
  scenario: string;
  context: string;
  input?: AirportCabInput;
  passengers?: number;
  optionId?: string;
}
export default function AirportCabPanel({
  world,
  onApplied,
  onMutationPending,
}: {
  world: World;
  onApplied: (result: MutationResult) => void;
  onMutationPending: (pending: boolean) => void;
}) {
  const [status, setStatus] = useState<AirportCabStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [statusRefresh, setStatusRefresh] = useState(0);
  const [form, setForm] = useState<AirportCabForm>(emptyForm);
  const [result, setResult] = useState<{
    quote: AirportCabQuote;
    scenario: string;
    context: string;
  } | null>(null);
  const [busy, setBusy] = useState<Attempt['kind'] | null>(null);
  const [uncertain, setUncertain] = useState<{ attempt: Attempt; error: TransportError } | null>(
    null,
  );
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [terminalSearch, setTerminalSearch] = useState('');
  const [importedOptionId, setImportedOptionId] = useState<string | null>(null);
  const currentScenario = `${world.mode}:${world.scenario_id}`;
  const scenario = useRef(currentScenario);
  scenario.current = currentScenario;
  const gate = useRef(new AirportCabRequestGate());
  const pending = useRef(false);
  const mounted = useRef(true);
  const now = useFreshnessClock(true);
  const locked = busy !== null || uncertain !== null;
  const visibleResult = result?.scenario === currentScenario ? result : null;
  const group = world.groups.find((item) => item.id === form.group_id);
  const groupIssue = airportCabGroupIssue(world, group);
  const contextCurrent =
    visibleResult !== null &&
    airportCabContext(world, visibleResult.quote.group_id) === visibleResult.context &&
    airportCabGroupIssue(
      world,
      world.groups.find((item) => item.id === visibleResult.quote.group_id),
    ) === null;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      gate.current.invalidate();
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setStatusError('');
    void airportCabApi
      .status(controller.signal)
      .then(setStatus)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setStatusError(cause instanceof Error ? cause.message : 'Source status unavailable.');
      });
    return () => controller.abort();
  }, [statusRefresh]);
  useEffect(() => {
    gate.current.invalidate();
    pending.current = false;
    setForm(emptyForm);
    setResult(null);
    setBusy(null);
    setUncertain(null);
    setError('');
    setNotice('');
    setTerminalSearch('');
    setImportedOptionId(null);
  }, [currentScenario]);
  useEffect(() => {
    onMutationPending(locked);
    return () => onMutationPending(false);
  }, [locked, onMutationPending]);

  function change(field: keyof AirportCabForm, value: string) {
    if (locked || pending.current) return;
    setForm((previous) => {
      const selected = field === 'group_id' ? world.groups.find((item) => item.id === value) : null;
      return {
        ...previous,
        [field]: value,
        ...(selected ? { pickup_at: airportCabDefaultPickup(world, selected) } : {}),
      };
    });
    setResult(null);
    setImportedOptionId(null);
    setError('');
    setNotice('');
  }
  async function send(attempt: Attempt) {
    if (pending.current) return;
    const token = gate.current.begin();
    pending.current = true;
    setBusy(attempt.kind);
    setError('');
    setNotice('');
    onMutationPending(true);
    try {
      const response = await attempt.action();
      if (!mounted.current || !gate.current.accepts(token, attempt.scenario, scenario.current))
        return;
      if (attempt.kind === 'quote') {
        const quote = verifyAirportCabQuote(response, {
          input: attempt.input!,
          passengers: attempt.passengers!,
        });
        setResult({ quote, scenario: attempt.scenario, context: attempt.context });
        setImportedOptionId(null);
      } else {
        const mutation = response as MutationResult;
        if (
          !mutation ||
          typeof mutation.applied !== 'boolean' ||
          !mutation.world ||
          mutation.world.scenario_id !== world.scenario_id
        )
          throw new TransportError(<T,>() => attempt.action() as Promise<T>);
        onApplied(mutation);
        if (mutation.applied) setImportedOptionId(attempt.optionId ?? null);
        setNotice(
          mutation.explanation ||
            (mutation.applied
              ? 'Group planning leg added. No booking was made.'
              : 'The planning leg was not added.'),
        );
      }
      setUncertain(null);
    } catch (cause) {
      if (mounted.current && gate.current.accepts(token, attempt.scenario, scenario.current)) {
        setError(cause instanceof Error ? cause.message : 'The request failed.');
        setUncertain(cause instanceof TransportError ? { attempt, error: cause } : null);
        if (attempt.kind === 'quote' && cause instanceof SearchOutcomeUnknownError) {
          setTerminalSearch(cause.message);
          setError('');
        }
      }
    } finally {
      if (mounted.current && gate.current.accepts(token, attempt.scenario, scenario.current)) {
        pending.current = false;
        setBusy(null);
      }
    }
  }
  function search() {
    if (locked || pending.current || !status) return;
    try {
      const input = airportCabInput(form, world, status);
      setResult(null);
      setImportedOptionId(null);
      setTerminalSearch('');
      void send({
        kind: 'quote',
        action: airportCabApi.quoteAction(world, input),
        scenario: currentScenario,
        context: airportCabContext(world, input.group_id),
        input,
        passengers: group!.size,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Check the pickup details.');
    }
  }
  function importOption(optionId: string) {
    if (
      locked ||
      pending.current ||
      !visibleResult ||
      !contextCurrent ||
      importedOptionId !== null ||
      !airportCabCurrent(visibleResult.quote, Date.now()) ||
      !visibleResult.quote.options.some(
        (option) => option.option_id === optionId && option.importable,
      )
    )
      return;
    void send({
      kind: 'import',
      action: airportCabApi.importAction(world, visibleResult.quote.quote_id, optionId),
      scenario: currentScenario,
      context: visibleResult.context,
      optionId,
    });
  }
  return (
    <section className="airport-cab-panel" aria-label="Romanian airport private transfer">
      <div className="airport-cab-heading">
        <h3>
          <CarFront size={17} aria-hidden="true" /> OTP → CRA private transfer
        </h3>
        <span className="airport-cab-provider">Bucharest.CAB</span>
      </div>
      <p className="field-hint">
        Get a supplier-calculated vehicle fare for an existing group at OTP. CRA may be an
        intermediate stop; this search does not relocate passengers or book transport.
      </p>
      {terminalSearch && (
        <div className="airport-cab-uncertain" role="alert">
          <strong>The previous quote search cannot be recovered</strong>
          <p>{terminalSearch}</p>
          <p>
            The supplier may have received that search. A new search is a separate request and
            starts only when you choose “Get vehicle fares”. No transport was booked.
          </p>
        </div>
      )}
      {statusError && (
        <div role="alert">
          <p className="text-amber">{statusError}</p>
          <button
            type="button"
            className="secondary-button"
            disabled={locked}
            onClick={() => {
              setStatus(null);
              setStatusRefresh((value) => value + 1);
            }}
          >
            Check source connection
          </button>
        </div>
      )}
      {!status && !statusError && <p role="status">Checking source connection…</p>}
      {status && !status.configured && (
        <p role="status" className="text-amber">
          This source is not connected.
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          search();
        }}
      >
        <fieldset disabled={locked} className="live-form-fieldset">
          <legend className="real-sr-only">Airport transfer quote inputs</legend>
          <div className="airport-cab-form">
            <label className="field">
              Existing group
              <select
                required
                value={form.group_id}
                onChange={(event) => change('group_id', event.target.value)}
              >
                <option value="">Choose a group at OTP</option>
                {world.groups.map((item) => (
                  <option
                    key={item.id}
                    value={item.id}
                    disabled={airportCabGroupIssue(world, item) !== null}
                  >
                    {item.name} · {item.size} passengers · {item.origin}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Pickup date and time (UTC)
              <input
                required
                type="datetime-local"
                step="60"
                value={form.pickup_at}
                onChange={(event) => change('pickup_at', event.target.value)}
              />
            </label>
            <label className="field">
              Suitcases
              <input
                required
                type="number"
                min="0"
                max="7"
                step="1"
                value={form.suitcases}
                onChange={(event) => change('suitcases', event.target.value)}
              />
            </label>
            <label className="field">
              Handbags
              <input
                required
                type="number"
                min="0"
                max="7"
                step="1"
                value={form.handbags}
                onChange={(event) => change('handbags', event.target.value)}
              />
            </label>
          </div>
          {group && (
            <p className="field-hint">
              {group.size} recorded passengers · {group.adult_count ?? 'Unknown'} adults · final
              destination {group.destination}. Deadline{' '}
              {timestamp(new Date(Date.parse(world.epoch) + group.deadline * 60_000).toISOString())}
              .
            </p>
          )}
          {form.group_id && groupIssue && <p className="text-amber">{groupIssue}</p>}
          <p className="field-hint">
            1–7 recorded adults · pickup 9 hours to 31 days ahead. The 9-hour minimum conservatively
            covers the supplier’s day/night advance-notice rules. Suitability for accessible travel
            is unverified.
          </p>
          <button
            className="primary-button"
            type="submit"
            disabled={locked || !status?.configured || groupIssue !== null}
          >
            {busy === 'quote' ? (
              <Loader2 size={15} className="spin" aria-hidden="true" />
            ) : (
              <Search size={15} aria-hidden="true" />
            )}
            {busy === 'quote' ? 'Getting vehicle fares…' : 'Get vehicle fares'}
          </button>
        </fieldset>
      </form>
      {uncertain && (
        <div className="airport-cab-uncertain" role="alert">
          <strong>
            {uncertain.attempt.kind === 'import'
              ? 'Import outcome unknown'
              : 'Quote search outcome unknown'}
          </strong>
          <p>
            Keep this dialog open. Recover the original request before editing or starting another
            attempt; its request ID and inputs will be reused.
          </p>
          <button
            type="button"
            className="secondary-button"
            disabled={busy !== null}
            onClick={() =>
              void send({ ...uncertain.attempt, action: () => uncertain.error.retry<unknown>() })
            }
          >
            <RefreshCw size={14} aria-hidden="true" />
            {busy ? 'Recovering original request…' : 'Recover original request'}
          </button>
        </div>
      )}
      {error && !uncertain && (
        <p role="alert" className="text-amber">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="airport-cab-notice">
          {notice}
        </p>
      )}
      {visibleResult && (
        <AirportCabResults
          result={visibleResult.quote}
          now={now}
          pending={locked}
          contextCurrent={contextCurrent}
          importedOptionId={importedOptionId}
          onImport={importOption}
        />
      )}
      {busy === 'import' && <p role="status">Adding the selected group planning leg…</p>}
    </section>
  );
}
