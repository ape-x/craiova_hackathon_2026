import { useEffect, useRef, useState } from 'react';
import { CarFront, Loader2, Plus, RefreshCw, Search } from 'lucide-react';
import { api, SearchOutcomeUnknownError, TransportError } from './api';
import type { MutationResult, World } from './types';
import { freshnessTime, sourceInstant, useFreshnessClock } from './resourceFreshness';
import { transferEuroEstimate, transferFxReference, transferPrice } from './transferQuoteData';
import { planningQuoteCurrent, planningTransferInput } from './planningTransferData';
import type { PlanningTransferForm, PlanningTransferQuote } from './planningTransferData';
import './planningTransfers.css';

const emptyForm: PlanningTransferForm = {
  group_id: '',
  hotel_node_id: '',
  pickup_at: '',
  suitcases: '',
  label: '',
  latitude: '',
  longitude: '',
  confirmed: false,
};

export function PlanningTransferResults({
  result,
  now,
  pending,
  importedOptionId,
  onImport,
}: {
  result: PlanningTransferQuote;
  now: number;
  pending: boolean;
  importedOptionId: string | null;
  onImport: (optionId: string) => void;
}) {
  const current = planningQuoteCurrent(result, now);
  const imported = importedOptionId !== null;
  const reference = transferFxReference(result.transfer?.fx);
  const usable = sourceInstant(result.usable_until);
  const received = sourceInstant(result.transfer?.received_at ?? result.created_at);
  const pickup = sourceInstant(result.route?.request.departure_time);
  const seconds = Number(result.route?.duration_seconds);
  return (
    <section className="planning-transfer-results" aria-label="Scoped private-transfer quote">
      <h3>{result.state === 'ready' ? 'Planning quotes' : 'Planning quote blocked'}</h3>
      <p>
        <strong>{result.initial_point.label}</strong> →{' '}
        <strong>{result.hotel?.name ?? result.hotel_node_id}</strong>
      </p>
      <p className="field-hint">
        Initial meeting point reported by operations. No arriving-flight connection is verified.
      </p>
      {result.hotel && (
        <p>
          {result.hotel.address}
          {result.hotel.city ? `, ${result.hotel.city}` : ''}
          {result.hotel.country ? `, ${result.hotel.country}` : ''}
          <br />
          Supplier property coordinates: {result.hotel.latitude}, {result.hotel.longitude}.
        </p>
      )}
      <p className="field-hint">
        {received !== null ? `Received by app ${freshnessTime(received)}. ` : ''}
        {usable !== null
          ? `Local planning window through ${freshnessTime(usable)}. `
          : 'Local planning window unavailable. '}
        Supplier quote time and expiry are unknown. No vehicle is held.
      </p>
      {result.state === 'ready' && !current && !imported && (
        <p className="text-amber" role="status">
          The local planning window has ended. Start a new explicit search before importing.
        </p>
      )}
      {result.route && (
        <div className="planning-route-evidence">
          <strong>Google Routes · dated driving prediction</strong>
          <span>
            Requested pickup: {pickup !== null ? freshnessTime(pickup) : 'Time unavailable'}
          </span>
          <span>
            {Number.isFinite(seconds) && seconds > 0
              ? `${(seconds / 60).toFixed(1)} minutes predicted driving`
              : 'Driving duration unavailable'}{' '}
            · not guaranteed
          </span>
          <span>Road travel only; no flight-transfer or walking time is inferred.</span>
          {result.road_snap_offsets_m && (
            <span>
              Road endpoint offsets: {result.road_snap_offsets_m.origin.toFixed(1)} m at pickup /{' '}
              {result.road_snap_offsets_m.destination.toFixed(1)} m at the property. Pedestrian
              access is not verified.
            </span>
          )}
          {result.route.warnings.map((warning, index) => (
            <p key={index}>{warning}</p>
          ))}
        </div>
      )}
      {reference && (
        <p className="field-hint">
          EUR planning estimates use European Central Bank reference rates dated{' '}
          {reference.rate_date}
          {reference.source_units_per_eur ? ` · 1 EUR = ${reference.source_units_per_eur} GBP` : ''}
          . EUR settlement is not guaranteed; revalidation is required before booking.
        </p>
      )}
      {result.transfer?.fx && result.transfer.fx.state !== 'ready' && (
        <p className="text-amber">{result.transfer.fx.message}</p>
      )}
      {result.blockers.length > 0 && (
        <ul className="planning-blockers" aria-label="Import requirements">
          {result.blockers.map((blocker, index) => (
            <li key={`${blocker.code}-${index}`}>{blocker.message}</li>
          ))}
        </ul>
      )}
      {result.transfer?.options.map((option) => {
        const eligibility = result.options.find((item) => item.option_id === option.id);
        const estimate = transferEuroEstimate(option, result.transfer!.fx);
        return (
          <article className="planning-transfer-option" key={option.id}>
            <div>
              <CarFront size={17} aria-hidden="true" />
              <strong>{option.car_type} · private transfer</strong>
            </div>
            <strong className="planning-supplier-price">
              {transferPrice(option.price.amount)} per vehicle
            </strong>
            <span>
              {estimate
                ? `Approximately ${estimate} · EUR planning estimate`
                : 'EUR planning estimate unavailable'}
            </span>
            <span>
              Quoted for {result.transfer!.request.passengers} passengers and{' '}
              {result.transfer!.request.suitcases} suitcases.
            </span>
            <span className="field-hint">
              Vehicle class capacity: {option.max_passengers} passengers / {option.max_suitcases}{' '}
              suitcases. This does not confirm fleet availability.
            </span>
            {eligibility?.blockers.length ? (
              <ul className="planning-blockers">
                {eligibility.blockers.map((blocker, index) => (
                  <li key={`${blocker.code}-${index}`}>{blocker.message}</li>
                ))}
              </ul>
            ) : null}
            <button
              type="button"
              className="secondary-button"
              disabled={pending || imported || !current || !eligibility?.importable}
              onClick={() => onImport(option.id)}
            >
              <Plus size={14} />
              {importedOptionId === option.id
                ? 'Planning leg added'
                : imported
                  ? 'Not selected'
                  : 'Add planning leg'}
            </button>
          </article>
        );
      })}
      <p className="field-hint">
        Adding a planning leg does not book a vehicle, reserve a room, or complete the group’s
        recovery. The original destination stays unchanged.
      </p>
    </section>
  );
}

export default function PlanningTransfers({
  world,
  onApplied,
  onMutationPending,
}: {
  world: World;
  onApplied: (result: MutationResult) => void;
  onMutationPending: (pending: boolean) => void;
}) {
  const [form, setForm] = useState(emptyForm);
  const [result, setResult] = useState<PlanningTransferQuote | null>(null);
  const [resultScenario, setResultScenario] = useState('');
  const [busy, setBusy] = useState<'quote' | 'import' | null>(null);
  const [uncertain, setUncertain] = useState<{
    kind: 'quote' | 'import';
    error: TransportError;
    optionId?: string;
  } | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [terminalSearch, setTerminalSearch] = useState('');
  const [importedOptionId, setImportedOptionId] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  const scope = useRef(world.scenario_id);
  scope.current = world.scenario_id;
  const locked = busy !== null || uncertain !== null;
  const now = useFreshnessClock(result !== null);
  useEffect(() => {
    onMutationPending(locked);
  }, [locked, onMutationPending]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      onMutationPending(false);
    };
  }, [onMutationPending]);
  const selectedGroup = world.groups.find((group) => group.id === form.group_id);
  const hotels = world.airports.filter((node) => node.kind === 'hotel');
  const visibleResult = resultScenario === world.scenario_id ? result : null;
  function change<K extends keyof PlanningTransferForm>(field: K, value: PlanningTransferForm[K]) {
    if (pending.current || uncertain) return;
    setForm((previous) => ({
      ...previous,
      [field]: value,
      ...(field === 'group_id' ||
      field === 'label' ||
      field === 'latitude' ||
      field === 'longitude' ||
      field === 'pickup_at'
        ? { confirmed: false }
        : {}),
    }));
    setResult(null);
    setImportedOptionId(null);
    setError('');
    setNotice('');
  }
  async function send(
    kind: 'quote' | 'import',
    action: () => Promise<PlanningTransferQuote | MutationResult>,
    optionId?: string,
  ) {
    if (pending.current) return;
    pending.current = true;
    onMutationPending(true);
    setBusy(kind);
    setError('');
    setNotice('');
    const scenario = scope.current;
    try {
      const response = await action();
      if (!mounted.current || scope.current !== scenario) return;
      if (kind === 'quote') {
        setResult(response as PlanningTransferQuote);
        setResultScenario(scenario);
        setImportedOptionId(null);
      } else {
        onApplied(response as MutationResult);
        setImportedOptionId(optionId ?? null);
        setNotice(
          (response as MutationResult).explanation ||
            'The private-transfer planning leg was added. No booking was made.',
        );
      }
      setUncertain(null);
    } catch (cause) {
      if (mounted.current && scope.current === scenario) {
        setError(cause instanceof Error ? cause.message : 'The request failed.');
        setUncertain(cause instanceof TransportError ? { kind, error: cause, optionId } : null);
        if (kind === 'quote' && cause instanceof SearchOutcomeUnknownError) {
          setTerminalSearch(cause.message);
          setError('');
        }
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(null);
    }
  }
  function search() {
    if (locked || pending.current) return;
    try {
      const input = planningTransferInput(form, world);
      setResult(null);
      setImportedOptionId(null);
      setTerminalSearch('');
      void send('quote', () => api.planningTransferQuote(world, input));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Check the request details.');
    }
  }
  function importOption(optionId: string) {
    if (
      locked ||
      !visibleResult ||
      importedOptionId !== null ||
      !planningQuoteCurrent(visibleResult, Date.now())
    )
      return;
    void send(
      'import',
      () => api.importPlanningTransfer(world, visibleResult.quote_id, optionId),
      optionId,
    );
  }
  return (
    <div className="planning-transfers">
      <h3>Airport meeting point → hotel property</h3>
      <p className="field-hint">
        Choose an existing group and an imported supplier property. The pickup point is your
        declared initial position, not a verified arrival terminal. Hotel price freshness does not
        establish transport or room availability.
      </p>
      {terminalSearch && (
        <div className="planning-uncertain" role="alert">
          <p>{terminalSearch}</p>
          <p>
            Provider calls may have completed and incurred charges. Starting a new search is a
            separate attempt and may incur new charges. No new request will be sent until you choose
            “Get planning quotes”.
          </p>
        </div>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          search();
        }}
      >
        <fieldset disabled={locked} className="live-form-fieldset">
          <div className="live-form-grid">
            <label className="field">
              Passenger group
              <select
                required
                value={form.group_id}
                onChange={(event) => change('group_id', event.target.value)}
              >
                <option value="">Choose a group</option>
                {world.groups.map((group) => (
                  <option
                    key={group.id}
                    value={group.id}
                    disabled={
                      group.status === 'confirmed' ||
                      world.offers.some(
                        (offer) => offer.group_id === group.id && offer.status === 'confirmed',
                      )
                    }
                  >
                    {group.name} · {group.size} passengers · {group.origin}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Supplier hotel property
              <select
                required
                value={form.hotel_node_id}
                onChange={(event) => change('hotel_node_id', event.target.value)}
              >
                <option value="">Choose an imported property</option>
                {hotels.map((hotel) => (
                  <option key={hotel.id} value={hotel.id}>
                    {hotel.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Pickup (UTC)
              <input
                type="datetime-local"
                required
                step="60"
                value={form.pickup_at}
                onChange={(event) => change('pickup_at', event.target.value)}
              />
            </label>
            <label className="field">
              Suitcases
              <input
                type="number"
                required
                min="0"
                max="200"
                step="1"
                value={form.suitcases}
                onChange={(event) => change('suitcases', event.target.value)}
              />
            </label>
            <label className="field">
              Initial meeting point
              <input
                required
                minLength={2}
                maxLength={160}
                value={form.label}
                onChange={(event) => change('label', event.target.value)}
                placeholder="Precise pickup location reported by operations"
              />
            </label>
            <div className="planning-coordinates">
              <label className="field">
                Latitude
                <input
                  type="number"
                  required
                  min="-90"
                  max="90"
                  step="any"
                  value={form.latitude}
                  onChange={(event) => change('latitude', event.target.value)}
                />
              </label>
              <label className="field">
                Longitude
                <input
                  type="number"
                  required
                  min="-180"
                  max="180"
                  step="any"
                  value={form.longitude}
                  onChange={(event) => change('longitude', event.target.value)}
                />
              </label>
            </div>
          </div>
          {selectedGroup && (
            <p className="field-hint">
              Party size comes from {selectedGroup.name}: {selectedGroup.size} passengers. No change
              to the group’s origin or final destination is implied.
            </p>
          )}
          {!hotels.length && (
            <p className="text-amber">
              Import a real supplier hotel property under Hotel quotes first.
            </p>
          )}
          <label className="planning-point-confirmation">
            <input
              type="checkbox"
              required
              checked={form.confirmed}
              onChange={(event) => change('confirmed', event.target.checked)}
            />
            I confirm this group will be ready at the declared meeting point at pickup.
          </label>
          <button className="primary-button" type="submit" disabled={locked || !hotels.length}>
            {busy === 'quote' ? <Loader2 size={15} className="spin" /> : <Search size={15} />}Get
            planning quotes
          </button>
          <p className="field-hint">
            This explicit search requests supplier property details, APL prices and a dated road
            prediction. No booking is made. EUR reference estimates follow the agreed ECB planning
            policy.
          </p>
        </fieldset>
      </form>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {uncertain && (
        <div className="planning-uncertain">
          <p>
            The outcome is unknown. Recover the same request before editing, closing, or starting
            another search.
          </p>
          <button
            type="button"
            className="secondary-button"
            disabled={busy !== null}
            onClick={() =>
              void send(
                uncertain.kind,
                () => uncertain.error.retry<PlanningTransferQuote | MutationResult>(),
                uncertain.optionId,
              )
            }
          >
            <RefreshCw size={14} />
            Recover same request
          </button>
        </div>
      )}
      {notice && (
        <p className="text-mint" role="status">
          {notice}
        </p>
      )}
      {visibleResult && (
        <PlanningTransferResults
          result={visibleResult}
          now={now}
          pending={locked}
          importedOptionId={importedOptionId}
          onImport={importOption}
        />
      )}
    </div>
  );
}
