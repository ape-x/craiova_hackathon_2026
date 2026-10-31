import { useEffect, useRef, useState } from 'react';
import { Loader2, Plus, RefreshCw, Search } from 'lucide-react';
import { api, request, TransportError } from './api';
import type { MutationResult, World } from './types';
import { Tag } from './ui';
import { supplierStationRoute } from './liveData';
import { bucharestDate, formatOfferPrice, formatRetrievedAt } from './realData';
interface TravelQuote {
  id: string;
  provider: string;
  provider_offer_id: string;
  origin: string;
  destination: string;
  departure_at: string;
  arrival_at: string;
  total_price: string | number;
  currency: string;
  passengers: number;
  expires_at: string | null;
  quoted_at: string;
  segments: unknown[];
  accessibility_confirmed: boolean;
  mode: 'live' | 'sandbox';
  requires_revalidation: boolean;
}
interface SearchResult {
  state: string;
  source: string;
  message: string | null;
  items: TravelQuote[];
  search_id?: string;
}
export default function LiveTravelSearch({
  world,
  onApplied,
}: {
  world: World;
  onApplied: (result: MutationResult) => void;
}) {
  const [query, setQuery] = useState({
    group_id: '',
    kind: 'flight',
    origin_code: '',
    destination_code: '',
    departure_date: bucharestDate(),
    ages: '',
  });
  const [result, setResult] = useState<SearchResult | null>(null);
  const [resultGroup, setResultGroup] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [uncertain, setUncertain] = useState<TransportError | null>(null);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);
  function change(key: keyof typeof query, value: string) {
    setQuery((previous) => {
      const group =
        key === 'group_id' ? world.groups.find((candidate) => candidate.id === value) : null;
      return {
        ...previous,
        [key]: value,
        ...(group
          ? {
              origin_code: group.origin,
              destination_code: group.destination,
              departure_date: bucharestDate(
                new Date(
                  new Date(world.epoch).getTime() +
                    Math.max(world.sim_time, group.ready_at ?? world.sim_time) * 60_000,
                ),
              ),
            }
          : {}),
      };
    });
    setResult(null);
    setError('');
    setNotice('');
  }
  async function search() {
    const group = world.groups.find((candidate) => candidate.id === query.group_id);
    if (!group) {
      setError('Choose an existing passenger group.');
      return;
    }
    if (query.kind === 'flight' && group.adult_count !== group.size) {
      setError(
        'The current flight supplier supports adult-only groups. Choose a group with verified adult ages.',
      );
      return;
    }
    const ages = query.ages
      .trim()
      .split(/[\s,;]+/)
      .filter(Boolean)
      .map(Number);
    if (
      query.kind === 'bus' &&
      (ages.length !== group.size ||
        ages.some((age) => !Number.isInteger(age) || age < 0 || age > 120))
    ) {
      setError(`Enter exactly ${group.size} passenger ages, separated by commas.`);
      return;
    }
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setError('');
    setNotice('');
    setResult(null);
    try {
      const { ages: ignored, ...input } = query;
      void ignored;
      const next = await request<SearchResult>(
        '/live/travel/search',
        { ...input, ...(query.kind === 'bus' ? { passenger_ages: ages } : {}) },
        current.signal,
      );
      if (mounted.current && !current.signal.aborted) {
        setResult(next);
        setResultGroup(query.group_id);
      }
    } catch (cause) {
      if (mounted.current && !current.signal.aborted)
        setError(cause instanceof Error ? cause.message : 'Travel search failed.');
    } finally {
      if (mounted.current && controller.current === current) setBusy(false);
    }
  }
  async function importQuote(quote: TravelQuote, retry?: TransportError) {
    if (!result?.search_id && !retry) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const imported = retry
        ? await retry.retry<MutationResult>()
        : await api.liveInput(world, 'travel/import', {
            search_id: result!.search_id,
            offer_id: quote.id,
            group_id: resultGroup,
          });
      onApplied(imported);
      if (mounted.current) {
        setNotice('Live quote added to this group’s planning inventory. No booking was made.');
        setUncertain(null);
        setResult(null);
      }
    } catch (cause) {
      if (mounted.current) {
        setError(cause instanceof Error ? cause.message : 'Quote import failed.');
        if (cause instanceof TransportError) setUncertain(cause);
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  const quotes =
    result?.state === 'ready' ? result.items.filter((quote) => quote.mode === 'live') : [];
  return (
    <div>
      <p className="field-hint">
        Search connected suppliers for live group quotes, then add a selected quote to the recovery
        planner. Importing a quote does not book tickets or a vehicle.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void search();
        }}
      >
        <fieldset className="live-form-fieldset" disabled={busy || Boolean(uncertain)}>
          <div className="live-form-grid">
            <label className="field">
              Passenger group
              <select
                required
                value={query.group_id}
                onChange={(event) => change('group_id', event.target.value)}
              >
                <option disabled value="">
                  Choose group
                </option>
                {world.groups.map((group) => (
                  <option key={group.id} value={group.id}>
                    {group.name} · {group.size} people
                    {group.source === 'synthetic' ? ' · TEST' : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Service
              <select value={query.kind} onChange={(event) => change('kind', event.target.value)}>
                <option value="flight">Flights</option>
                <option value="bus">Buses / coaches</option>
              </select>
            </label>
            <label className="field">
              Origin IATA
              <input
                required
                pattern="[A-Z]{3}"
                maxLength={3}
                value={query.origin_code}
                onChange={(event) => change('origin_code', event.target.value.toUpperCase())}
              />
            </label>
            <label className="field">
              Destination IATA
              <input
                required
                pattern="[A-Z]{3}"
                maxLength={3}
                value={query.destination_code}
                onChange={(event) => change('destination_code', event.target.value.toUpperCase())}
              />
            </label>
            <label className="field">
              Departure date
              <input
                required
                type="date"
                min={bucharestDate()}
                value={query.departure_date}
                onChange={(event) => change('departure_date', event.target.value)}
              />
            </label>
            {query.kind === 'bus' && (
              <label className="field">
                Passenger ages · one per person
                <input
                  required
                  value={query.ages}
                  onChange={(event) => change('ages', event.target.value)}
                  placeholder="Separate ages with commas"
                />
              </label>
            )}
          </div>
          <button className="primary-button" type="submit" disabled={!world.groups.length}>
            {busy ? <Loader2 className="spin" size={14} /> : <Search size={14} />} Search live
            travel
          </button>
        </fieldset>
      </form>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="live-feedback" role="status">
          {notice}
        </p>
      )}
      {result && (
        <div className="live-travel-results">
          <div className="real-source-status">
            <span>{result.source}</span>
            <Tag tone={result.state === 'ready' ? 'cyan' : 'amber'}>
              {result.state === 'ready'
                ? 'LIVE QUOTES'
                : result.state.replaceAll('_', ' ').toUpperCase()}
            </Tag>
          </div>
          {result.message && <p className="field-hint">{result.message}</p>}
          {result.state === 'ready' && !quotes.length && (
            <p className="field-hint">
              No live quotes were returned for this group and departure date.
            </p>
          )}
          {quotes.map((quote) => (
            <article className="live-travel-quote" key={quote.id}>
              <strong>
                {quote.origin} → {quote.destination}
              </strong>
              {quote.segments.map((segment, index) => {
                const names = supplierStationRoute(segment);
                return names ? (
                  <p key={`stations-${index}`}>
                    <b>Supplier stations:</b> {names}
                  </p>
                ) : null;
              })}
              <p>
                {formatRetrievedAt(quote.departure_at)} → {formatRetrievedAt(quote.arrival_at)}
              </p>
              <p>
                {quote.passengers} passengers · {quote.segments.length} segments · {quote.provider}
              </p>
              <strong>{formatOfferPrice(String(quote.total_price), quote.currency)} total</strong>
              <p className="field-hint">
                Quoted {formatRetrievedAt(quote.quoted_at)} · expires{' '}
                {formatRetrievedAt(quote.expires_at)}.{' '}
                {quote.accessibility_confirmed
                  ? 'Accessibility confirmed by supplier.'
                  : 'Accessibility is not confirmed.'}
              </p>
              <button
                type="button"
                className="secondary-button"
                disabled={
                  busy ||
                  Boolean(uncertain) ||
                  !result.search_id ||
                  Boolean(quote.expires_at && Date.parse(quote.expires_at) <= Date.now())
                }
                onClick={() => {
                  void importQuote(quote);
                }}
              >
                <Plus size={14} /> Add quote to planner
              </button>
            </article>
          ))}
        </div>
      )}
      {uncertain && (
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => {
            void importQuote({} as TravelQuote, uncertain);
          }}
        >
          <RefreshCw size={14} /> Recover original quote import
        </button>
      )}
    </div>
  );
}
