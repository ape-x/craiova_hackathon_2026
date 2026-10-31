import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ArrowDownToLine, CheckCircle2, FileUp, Loader2, Plus, RefreshCw } from 'lucide-react';
import { api, TransportError } from './api';
import type { MutationResult, World } from './types';
import { Modal, Tag } from './ui';
import { MANIFEST_TEMPLATE, inspectManifest, utcInputToIso } from './liveData';
import { addCalendarDays, bucharestDate } from './realData';
import './liveData.css';
import LiveTravelSearch from './LiveTravelSearch';
import { isAirportNode, locationKindLabel } from './locationNodes';

type Tab = 'search' | 'generate' | 'passengers' | 'transport' | 'hotels' | 'confirm';
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      {label}
      {children}
    </label>
  );
}
function UtcField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={`${label} (UTC)`}>
      <input
        type="datetime-local"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required
      />
    </Field>
  );
}
function Evidence({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (key: string, value: string) => void;
}) {
  return (
    <div className="live-evidence">
      <h3>Source verification</h3>
      <div className="live-form-grid">
        <Field label="Source / operator">
          <input
            value={value.source_name}
            onChange={(e) => onChange('source_name', e.target.value)}
            required
          />
        </Field>
        <Field label="Source reference">
          <input
            value={value.source_reference}
            onChange={(e) => onChange('source_reference', e.target.value)}
            placeholder="Manifest, written quote or operator reference"
            required
          />
        </Field>
        <Field label="Verified by">
          <input
            value={value.confirmed_by}
            onChange={(e) => onChange('confirmed_by', e.target.value)}
            required
          />
        </Field>
        <UtcField
          label="Source verified at"
          value={value.observed_at}
          onChange={(v) => onChange('observed_at', v)}
        />
      </div>
    </div>
  );
}
const emptyEvidence = { source_name: '', source_reference: '', confirmed_by: '', observed_at: '' };
export default function LiveDataDialog({
  world,
  onClose,
  onApplied,
}: {
  world: World;
  onClose: () => void;
  onApplied: (result: MutationResult) => void;
}) {
  const [tab, setTab] = useState<Tab>('passengers');
  const [csv, setCsv] = useState('');
  const [generated, setGenerated] = useState({
    passengers: '20',
    group_size: '2',
    origin: '',
    destination: '',
    original_flight_id: '',
    ready_at: '',
    deadline: '',
    group_budget_eur: '1000',
    accessible: 'false',
    priority: '1',
    is_adult: 'true',
  });
  const [evidence, setEvidence] = useState(emptyEvidence);
  const [transport, setTransport] = useState({
    id: '',
    kind: 'flight',
    name: '',
    origin: '',
    destination: '',
    departure_at: '',
    arrival_at: '',
    capacity: '',
    unit_price_eur: '',
    accessible: '',
    vehicle_id: '',
    valid_until: '',
    ...emptyEvidence,
  });
  const today = bucharestDate();
  const [hotel, setHotel] = useState({
    group_id: '',
    airport_code: '',
    checkin: today,
    checkout: addCalendarDays(today, 1),
    rooms: '',
    checkin_at: '',
    checkout_at: '',
    stay_times_confirmed: false,
  });
  const [offerId, setOfferId] = useState('');
  const [booking, setBooking] = useState<
    Record<string, { confirmation_reference: string; confirmed_by: string; confirmed_at: string }>
  >({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [uncertain, setUncertain] = useState<TransportError | null>(null);
  const [travelPending, setTravelPending] = useState(false);
  const pending = useRef(false);
  pending.current = busy || Boolean(uncertain) || travelPending;
  const close = useCallback(() => {
    if (pending.current) {
      setError('Finish or recover the pending submission before closing this dialog.');
      return;
    }
    onClose();
  }, [onClose]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const selectedOffer = world.offers.find((offer) => offer.id === offerId);
  const hotelGroup = world.groups.find((group) => group.id === hotel.group_id);
  const hotelNeedsAdults = Boolean(hotelGroup && hotelGroup.adult_count !== hotelGroup.size);
  const confirmable = world.offers.filter((offer) =>
    ['accepted', 'proposed', 'held'].includes(offer.status),
  );
  const evidenceChange = (key: string, value: string) =>
    setEvidence((previous) => ({ ...previous, [key]: value }));
  const transportChange = (key: string, value: string) =>
    setTransport((previous) => ({ ...previous, [key]: value }));
  function resetNotice() {
    setError('');
    setSuccess('');
  }
  async function send(action: () => Promise<MutationResult>, message: string) {
    if (busy) return;
    setBusy(true);
    resetNotice();
    try {
      const result = await action();
      onApplied(result);
      if (mounted.current) {
        setSuccess(`${message} · state v${result.world.version}`);
        setUncertain(null);
      }
    } catch (cause) {
      if (mounted.current) {
        setError(cause instanceof Error ? cause.message : 'The update failed.');
        if (cause instanceof TransportError) setUncertain(cause);
        else setUncertain(null);
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  function saveManifest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    resetNotice();
    const preview = inspectManifest(csv);
    if (preview.error) {
      setError(preview.error);
      return;
    }
    try {
      const payload = {
        ...evidence,
        observed_at: utcInputToIso(evidence.observed_at),
        csv_text: csv,
      };
      void send(
        () => api.liveInput(world, 'import-passengers', payload),
        'Passenger manifest imported',
      );
    } catch (cause) {
      setError((cause as Error).message);
    }
  }
  function saveTransport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    resetNotice();
    try {
      const { capacity, unit_price_eur, accessible, ...fields } = transport;
      if (!['true', 'false'].includes(accessible))
        throw new Error('Confirm the accessibility status.');
      const payload = {
        ...fields,
        vehicle_id: transport.kind === 'bus' ? transport.vehicle_id : null,
        capacity: Number(capacity),
        unit_price_eur: Number(unit_price_eur),
        accessible: accessible === 'true',
        departure_at: utcInputToIso(transport.departure_at),
        arrival_at: utcInputToIso(transport.arrival_at),
        observed_at: utcInputToIso(transport.observed_at),
        valid_until: utcInputToIso(transport.valid_until),
      };
      void send(() => api.liveInput(world, 'resources', payload), 'Verified transport added');
    } catch (cause) {
      setError((cause as Error).message);
    }
  }
  function saveHotel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    resetNotice();
    if (hotelNeedsAdults) {
      setError('The current hotel supplier requires an adult-only group.');
      return;
    }
    if (!hotel.stay_times_confirmed) {
      setError('Confirm the planned stay times with the property before continuing.');
      return;
    }
    void send(
      () =>
        api.liveInput(world, 'hotel-quotes', {
          ...hotel,
          rooms: Number(hotel.rooms),
          checkin_at: utcInputToIso(hotel.checkin_at),
          checkout_at: utcInputToIso(hotel.checkout_at),
        }),
      'Current hotel quotes added for this group',
    );
  }
  function confirmBooking(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    resetNotice();
    if (!selectedOffer) {
      setError('Select a current offer.');
      return;
    }
    if (world.groups.find((group) => group.id === selectedOffer.group_id)?.source === 'synthetic') {
      setError(
        'Planning assumptions cannot receive supplier bookings. Import the actual passenger manifest first.',
      );
      return;
    }
    try {
      const bookings = [...new Set(selectedOffer.itinerary.legs.map((leg) => leg.resource_id))].map(
        (resource_id) => {
          const item = booking[resource_id];
          if (!item?.confirmation_reference.trim() || !item.confirmed_by.trim())
            throw new Error('Record a supplier confirmation and verifier for every segment.');
          return { resource_id, ...item, confirmed_at: utcInputToIso(item.confirmed_at) };
        },
      );
      void send(
        () =>
          api.liveInput(world, `offers/${encodeURIComponent(selectedOffer.id)}/confirm`, {
            bookings,
          }),
        'Supplier confirmations recorded',
      );
    } catch (cause) {
      setError((cause as Error).message);
    }
  }
  function downloadHeader() {
    const url = URL.createObjectURL(new Blob([MANIFEST_TEMPLATE], { type: 'text/csv' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'passenger-manifest-template.csv';
    link.click();
    URL.revokeObjectURL(url);
  }
  return createPortal(
    <Modal title="Operation data" onClose={close} wide>
      <p className="live-dialog-intro">
        Import a passenger manifest or enter explicitly labeled planning assumptions. Transport and
        accommodation must come from live supplier quotes or documented operator verification.
      </p>
      <div className="live-tabs" role="tablist" aria-label="Live data actions">
        {(
          [
            ['search', 'Find live travel'],
            ['generate', 'Planning assumptions'],
            ['passengers', 'Passenger manifest'],
            ['transport', 'Transport'],
            ['hotels', 'Hotel quotes'],
            ['confirm', 'Booking confirmations'],
          ] as const
        ).map(([value, label]) => (
          <button
            type="button"
            role="tab"
            aria-selected={tab === value}
            disabled={busy || Boolean(uncertain) || travelPending}
            key={value}
            onClick={() => {
              setTab(value);
              resetNotice();
            }}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'search' && (
        <LiveTravelSearch
          world={world}
          onApplied={onApplied}
          onMutationPending={setTravelPending}
        />
      )}
      {tab === 'generate' && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            resetNotice();
            try {
              const payload = {
                ...generated,
                passengers: Number(generated.passengers),
                group_size: Number(generated.group_size),
                group_budget_eur: Number(generated.group_budget_eur),
                priority: Number(generated.priority),
                accessible: generated.accessible === 'true',
                is_adult: generated.is_adult === 'true',
                ready_at: utcInputToIso(generated.ready_at),
                deadline: utcInputToIso(generated.deadline),
              };
              void send(
                () => api.liveInput(world, 'generate-passengers', payload),
                'Planning assumptions saved',
              );
            } catch (cause) {
              setError((cause as Error).message);
            }
          }}
        >
          <fieldset className="live-form-fieldset" disabled={busy || Boolean(uncertain)}>
            <Tag tone="amber">PLANNING ASSUMPTIONS</Tag>
            <p className="field-hint">
              These groups represent assumed passenger requirements, not verified passenger
              identities. They can be evaluated against real services but cannot receive supplier
              booking confirmations. Import the actual manifest before booking.
            </p>
            <div className="live-form-grid">
              {(
                [
                  ['passengers', 'Passenger count', 1, 500],
                  ['group_size', 'People per group', 1, 12],
                  ['group_budget_eur', 'Budget per group · EUR', 0, 1000000],
                  ['priority', 'Priority', 1, 10],
                ] as const
              ).map(([key, label, min, max]) => (
                <Field label={label} key={key}>
                  <input
                    type="number"
                    min={min}
                    max={max}
                    step={key === 'group_budget_eur' ? '0.01' : '1'}
                    value={generated[key]}
                    onChange={(e) => setGenerated((p) => ({ ...p, [key]: e.target.value }))}
                    required
                  />
                </Field>
              ))}
              {(
                [
                  ['origin', 'Origin airport IATA'],
                  ['destination', 'Destination airport IATA'],
                  ['original_flight_id', 'Disrupted flight reference'],
                ] as const
              ).map(([key, label]) => (
                <Field label={label} key={key}>
                  <input
                    value={generated[key]}
                    onChange={(e) =>
                      setGenerated((p) => ({ ...p, [key]: e.target.value.toUpperCase() }))
                    }
                    required
                  />
                </Field>
              ))}
              <UtcField
                label="Ready to travel"
                value={generated.ready_at}
                onChange={(value) => setGenerated((p) => ({ ...p, ready_at: value }))}
              />
              <UtcField
                label="Arrival deadline"
                value={generated.deadline}
                onChange={(value) => setGenerated((p) => ({ ...p, deadline: value }))}
              />
              <Field label="Accessibility needs">
                <select
                  value={generated.accessible}
                  onChange={(e) => setGenerated((p) => ({ ...p, accessible: e.target.value }))}
                >
                  <option value="false">Standard</option>
                  <option value="true">Accessible transport required</option>
                </select>
              </Field>
              <Field label="Passenger age group">
                <select
                  value={generated.is_adult}
                  onChange={(e) => setGenerated((p) => ({ ...p, is_adult: e.target.value }))}
                >
                  <option value="true">Adults</option>
                  <option value="false">Children · hotel provider may not support this</option>
                </select>
              </Field>
            </div>
            <button className="primary-button" type="submit">
              <Plus size={14} /> Save planning assumptions
            </button>
          </fieldset>
        </form>
      )}
      {tab === 'passengers' && (
        <form onSubmit={saveManifest}>
          <fieldset disabled={busy || Boolean(uncertain)} className="live-form-fieldset">
            <div className="live-actions">
              <button className="secondary-button" type="button" onClick={downloadHeader}>
                <ArrowDownToLine size={14} /> Download CSV header
              </button>
              <label className="secondary-button live-file">
                <FileUp size={14} /> Select CSV
                <input
                  type="file"
                  accept=".csv,text/csv"
                  onChange={async (event) => {
                    const file = event.target.files?.[0];
                    if (!file) return;
                    if (file.size > 1_000_000) {
                      setError('Use a CSV smaller than 1 MB.');
                      return;
                    }
                    try {
                      setCsv(await file.text());
                      resetNotice();
                    } catch {
                      setError('The file could not be read.');
                    }
                  }}
                />
              </label>
            </div>
            <Field label="Passenger manifest CSV">
              <textarea
                rows={7}
                value={csv}
                onChange={(event) => setCsv(event.target.value)}
                placeholder={MANIFEST_TEMPLATE.trim()}
                required
              />
            </Field>
            <p className="field-hint">
              One row per passenger. Use ISO timestamps with timezones. Members of a group share the
              route, readiness, deadline, group budget and priority. Group budget is the total in
              EUR. The template contains headers only.
            </p>
            <Evidence value={evidence} onChange={evidenceChange} />
            <button className="primary-button" type="submit">
              <FileUp size={14} /> Import actual passengers
            </button>
          </fieldset>
        </form>
      )}
      {tab === 'transport' && (
        <form onSubmit={saveTransport}>
          <fieldset disabled={busy || Boolean(uncertain)} className="live-form-fieldset">
            <div className="live-form-grid">
              <Field label="Service type">
                <select
                  value={transport.kind}
                  onChange={(event) => transportChange('kind', event.target.value)}
                >
                  <option value="flight">Flight</option>
                  <option value="bus">Bus / coach</option>
                </select>
              </Field>
              <Field label="Service ID">
                <input
                  value={transport.id}
                  onChange={(e) => transportChange('id', e.target.value)}
                  required
                />
              </Field>
              <Field label="Supplier / service name">
                <input
                  value={transport.name}
                  onChange={(e) => transportChange('name', e.target.value)}
                  required
                />
              </Field>
              <Field
                label={transport.kind === 'bus' ? 'Origin node / airport' : 'Origin airport IATA'}
              >
                <input
                  value={transport.origin}
                  onChange={(e) => transportChange('origin', e.target.value.toUpperCase())}
                  pattern={transport.kind === 'bus' ? '[A-Z0-9_-]+' : '[A-Z]{3}'}
                  maxLength={transport.kind === 'bus' ? 40 : 3}
                  list="live-transport-nodes"
                  required
                />
              </Field>
              <Field
                label={
                  transport.kind === 'bus'
                    ? 'Destination node / airport'
                    : 'Destination airport IATA'
                }
              >
                <input
                  value={transport.destination}
                  onChange={(e) => transportChange('destination', e.target.value.toUpperCase())}
                  pattern={transport.kind === 'bus' ? '[A-Z0-9_-]+' : '[A-Z]{3}'}
                  maxLength={transport.kind === 'bus' ? 40 : 3}
                  list="live-transport-nodes"
                  required
                />
              </Field>
              <Field label="Verified available seats">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={transport.capacity}
                  onChange={(e) => transportChange('capacity', e.target.value)}
                  required
                />
              </Field>
              <Field label="Price per person · EUR">
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  value={transport.unit_price_eur}
                  onChange={(e) => transportChange('unit_price_eur', e.target.value)}
                  required
                />
              </Field>
              <Field label="Accessibility confirmed">
                <select
                  value={transport.accessible}
                  onChange={(e) => transportChange('accessible', e.target.value)}
                  required
                >
                  <option value="" disabled>
                    Select verified status
                  </option>
                  <option value="true">Accessible</option>
                  <option value="false">Not accessible</option>
                </select>
              </Field>
              {transport.kind === 'bus' && (
                <Field label="Vehicle ID">
                  <input
                    value={transport.vehicle_id}
                    onChange={(e) => transportChange('vehicle_id', e.target.value)}
                    required
                  />
                </Field>
              )}
              <UtcField
                label="Departure"
                value={transport.departure_at}
                onChange={(v) => transportChange('departure_at', v)}
              />
              <UtcField
                label="Arrival"
                value={transport.arrival_at}
                onChange={(v) => transportChange('arrival_at', v)}
              />
              <UtcField
                label="Valid until"
                value={transport.valid_until}
                onChange={(v) => transportChange('valid_until', v)}
              />
            </div>
            <datalist id="live-transport-nodes">
              {world.airports
                .filter((node) => transport.kind === 'bus' || isAirportNode(node))
                .map((node) => (
                  <option key={node.id} value={node.id}>
                    {node.name} · {locationKindLabel(node)}
                  </option>
                ))}
            </datalist>
            {transport.kind === 'bus' && (
              <p className="field-hint">
                Use the actual airport, station or hotel property node IDs. A transfer between those
                locations must be separately confirmed by an operator.
              </p>
            )}
            <Evidence value={transport} onChange={transportChange} />
            <button type="submit" className="primary-button">
              <Plus size={14} /> Add verified service
            </button>
          </fieldset>
        </form>
      )}
      {tab === 'hotels' && (
        <form onSubmit={saveHotel}>
          {hotelNeedsAdults && (
            <p className="form-error">
              The current hotel supplier supports adult-only groups. Choose a group with verified
              adult ages.
            </p>
          )}
          <fieldset disabled={busy || Boolean(uncertain)} className="live-form-fieldset">
            <p className="field-hint">
              Fetch current supplier offers for one imported passenger group. The server checks the
              group, dates and quote expiry. These quotes are unbooked until separately confirmed
              with the supplier.
            </p>
            <div className="live-form-grid">
              <Field label="Passenger group">
                <select
                  value={hotel.group_id}
                  onChange={(e) => setHotel((p) => ({ ...p, group_id: e.target.value }))}
                  required
                >
                  <option value="" disabled>
                    Select imported group
                  </option>
                  {world.groups.map((group) => (
                    <option value={group.id} key={group.id}>
                      {group.name} · {group.size} passengers
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Airport IATA">
                <input
                  value={hotel.airport_code}
                  onChange={(e) =>
                    setHotel((p) => ({ ...p, airport_code: e.target.value.toUpperCase() }))
                  }
                  pattern="[A-Z]{3}"
                  maxLength={3}
                  required
                />
              </Field>
              <Field label="Check-in">
                <input
                  type="date"
                  value={hotel.checkin}
                  min={today}
                  onChange={(e) => setHotel((p) => ({ ...p, checkin: e.target.value }))}
                  required
                />
              </Field>
              <Field label="Check-out">
                <input
                  type="date"
                  value={hotel.checkout}
                  min={hotel.checkin}
                  onChange={(e) => setHotel((p) => ({ ...p, checkout: e.target.value }))}
                  required
                />
              </Field>
              <UtcField
                label="Confirmed stay start"
                value={hotel.checkin_at}
                onChange={(value) => setHotel((previous) => ({ ...previous, checkin_at: value }))}
              />
              <UtcField
                label="Confirmed stay end"
                value={hotel.checkout_at}
                onChange={(value) => setHotel((previous) => ({ ...previous, checkout_at: value }))}
              />
              <Field label="Requested rooms">
                <input
                  type="number"
                  min={1}
                  max={30}
                  step={1}
                  value={hotel.rooms}
                  onChange={(e) => setHotel((p) => ({ ...p, rooms: e.target.value }))}
                  required
                />
              </Field>
            </div>
            <label className="check-field">
              <input
                type="checkbox"
                checked={hotel.stay_times_confirmed}
                onChange={(event) =>
                  setHotel((previous) => ({
                    ...previous,
                    stay_times_confirmed: event.target.checked,
                  }))
                }
                required
              />
              I confirmed these planned check-in and check-out times with the property.
            </label>
            <button
              className="primary-button"
              type="submit"
              disabled={!world.groups.length || !hotel.stay_times_confirmed || hotelNeedsAdults}
            >
              <RefreshCw size={14} /> Fetch and add current quotes
            </button>
          </fieldset>
        </form>
      )}
      {tab === 'confirm' && (
        <form onSubmit={confirmBooking}>
          <fieldset disabled={busy || Boolean(uncertain)} className="live-form-fieldset">
            <p className="field-hint">
              Use this only after every segment has actually been booked with its supplier.
              Passenger acceptance alone does not book a service. Groups based on planning
              assumptions cannot receive supplier booking confirmations.
            </p>
            <Field label="Current itinerary">
              <select
                value={offerId}
                onChange={(e) => {
                  setOfferId(e.target.value);
                  // Supplier evidence belongs to this offer's passengers, even
                  // when another offer includes the same transport resource.
                  setBooking({});
                }}
                required
              >
                <option value="" disabled>
                  Select itinerary
                </option>
                {confirmable.map((offer) => (
                  <option key={offer.id} value={offer.id}>
                    {offer.group_id} · {offer.id} · {offer.status}
                    {world.groups.find((group) => group.id === offer.group_id)?.source ===
                    'synthetic'
                      ? ' · Planning assumptions'
                      : ''}
                  </option>
                ))}
              </select>
            </Field>
            {selectedOffer &&
              [...new Set(selectedOffer.itinerary.legs.map((leg) => leg.resource_id))].map((id) => {
                const item = booking[id] ?? {
                  confirmation_reference: '',
                  confirmed_by: '',
                  confirmed_at: '',
                };
                const update = (key: string, value: string) =>
                  setBooking((previous) => ({ ...previous, [id]: { ...item, [key]: value } }));
                return (
                  <div className="live-evidence" key={id}>
                    <h3>{id}</h3>
                    <div className="live-form-grid">
                      <Field label="Supplier booking confirmation">
                        <input
                          value={item.confirmation_reference}
                          onChange={(e) => update('confirmation_reference', e.target.value)}
                          required
                        />
                      </Field>
                      <Field label="Confirmed by">
                        <input
                          value={item.confirmed_by}
                          onChange={(e) => update('confirmed_by', e.target.value)}
                          required
                        />
                      </Field>
                      <UtcField
                        label="Booking confirmed at"
                        value={item.confirmed_at}
                        onChange={(v) => update('confirmed_at', v)}
                      />
                    </div>
                  </div>
                );
              })}
            <button
              className="primary-button"
              type="submit"
              disabled={
                !selectedOffer ||
                world.groups.find((group) => group.id === selectedOffer.group_id)?.source ===
                  'synthetic'
              }
            >
              <CheckCircle2 size={14} /> Record completed bookings
            </button>
          </fieldset>
        </form>
      )}
      {busy && (
        <p className="live-feedback" role="status">
          <Loader2 size={15} className="spin" /> Saving verified data…
        </p>
      )}
      {success && (
        <p className="live-feedback" role="status">
          <Tag tone="mint">SAVED</Tag>
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
          type="button"
          disabled={busy}
          onClick={() => {
            void send(() => uncertain.retry<MutationResult>(), 'Original submission recovered');
          }}
        >
          <RefreshCw size={14} /> Recover original submission
        </button>
      )}
    </Modal>,
    document.body,
  );
}
