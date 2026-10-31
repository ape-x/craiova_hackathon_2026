import { useId, useState } from 'react';
import type { PassengerGroup } from './types';
import { hotelRecoveryTimeline, savedHotelRecoveryInputs } from './hotelRecovery';
import type { DeclaredRecoveryPointForm, HotelRecoveryForm } from './hotelRecovery';
import { utcTimestamp } from './recoveryCase';
import './hotelRecovery.css';

function DeclaredPointFields({
  value,
  onChange,
  name,
  context,
}: {
  value: DeclaredRecoveryPointForm;
  onChange: (value: DeclaredRecoveryPointForm) => void;
  name: string;
  context: string;
}) {
  const hintId = useId();
  const update = (key: 'label' | 'latitude' | 'longitude', next: string) =>
    onChange({ ...value, [key]: next, confirmed: false });
  return (
    <div className="hotel-recovery-point">
      <label className="field">
        {name} label
        <input
          required
          maxLength={160}
          value={value.label}
          onChange={(event) => update('label', event.target.value)}
        />
      </label>
      <div className="case-form-grid">
        <label className="field">
          {name} latitude
          <input
            type="number"
            required
            step="any"
            min={-90}
            max={90}
            value={value.latitude}
            onChange={(event) => update('latitude', event.target.value)}
          />
        </label>
        <label className="field">
          {name} longitude
          <input
            type="number"
            required
            step="any"
            min={-180}
            max={180}
            value={value.longitude}
            onChange={(event) => update('longitude', event.target.value)}
          />
        </label>
      </div>
      <label className="hotel-recovery-confirm">
        <input
          type="checkbox"
          required
          checked={value.confirmed}
          aria-describedby={hintId}
          onChange={(event) => onChange({ ...value, confirmed: event.target.checked })}
        />
        <span>
          I confirm this {name.toLowerCase()} as an operator-declared point for this group.
        </span>
      </label>
      <p className="field-hint" id={hintId}>
        {context} These coordinates do not verify terminal or pedestrian access.
      </p>
    </div>
  );
}

export default function HotelRecoveryFields({
  value,
  onChange,
  group,
  disabled = false,
}: {
  value: HotelRecoveryForm;
  onChange: (value: HotelRecoveryForm) => void;
  group?: PassengerGroup;
  disabled?: boolean;
}) {
  const [reviewed, setReviewed] = useState(false);
  const saved = savedHotelRecoveryInputs(group);
  const timeline = hotelRecoveryTimeline(value.checkout_at);
  const update = <Key extends keyof HotelRecoveryForm>(key: Key, next: HotelRecoveryForm[Key]) =>
    onChange({ ...value, [key]: next });
  return (
    <div className="hotel-recovery-fields">
      <div className="hotel-recovery-intro">
        <strong>One group. One hotel shortlist. Both transfers.</strong>
        <p>
          Configure this attempt once: airport pickup → hotel stay → airport drop-off → onward
          flight. The service checks source prices, dated driving routes and the requested rest
          interval before proposing a complete journey.
        </p>
        <p className="field-hint">
          UK coverage, explicitly all-adult parties only. One property shortlist and one
          departure-date query; this is not a search of every possible alternative. No supplier
          booking or hold is made.
        </p>
      </div>
      {group && (
        <section className="hotel-recovery-saved" aria-label="Earlier operator configuration">
          <strong>Earlier operator configuration</strong>
          {saved.state === 'ready' ? (
            <>
              <p>
                Saved inputs for this group are available to review. Loading them replaces the
                current form values; nothing is submitted or searched.
              </p>
              <button
                type="button"
                className="secondary-button"
                disabled={disabled}
                onClick={() => {
                  if (disabled) return;
                  onChange(saved.form);
                  setReviewed(true);
                }}
              >
                Review saved inputs
              </button>
              <p className="field-hint">
                Dates stay exactly as saved, including past dates. Check the times and reconfirm
                both reported points. Current case eligibility, prices and availability still need
                checking.
              </p>
              {reviewed && (
                <p role="status">
                  Saved inputs loaded for review. Both point confirmations are cleared; your
                  provider-call limit is unchanged.
                </p>
              )}
            </>
          ) : (
            <p>
              {saved.state === 'unavailable'
                ? 'Saved inputs are unavailable or invalid. Reconfigure this group using the fields below.'
                : 'No saved inputs for this group. Configure the fields below for this attempt.'}
            </p>
          )}
          <p className="field-hint">
            Typed search: minimum 10 provider calls. Voice reuse: minimum 11 total calls — 1
            interpretation + 10 recovery-job calls. State the total allowance explicitly; a saved
            configuration never supplies or increases it.
          </p>
        </section>
      )}
      <fieldset className="hotel-recovery-section">
        <legend>
          <span>1</span> Initial airport pickup
        </legend>
        <p className="field-hint">
          {group
            ? `${group.name} · ${group.size} adults · ${group.origin}`
            : 'Choose one linked group above.'}{' '}
          The initial meeting point is a reported starting assumption for that group.
        </p>
        <div className="case-form-grid">
          <label className="field">
            Initial pickup (UTC)
            <input
              type="datetime-local"
              step={60}
              required
              value={value.pickup_at}
              onChange={(event) => update('pickup_at', event.target.value)}
            />
          </label>
          <label className="field">
            Suitcases for each transfer
            <input
              type="number"
              min={0}
              max={200}
              step={1}
              required
              value={value.suitcases}
              onChange={(event) => update('suitcases', event.target.value)}
            />
          </label>
        </div>
        <DeclaredPointFields
          name="Initial meeting point"
          value={value.initial_point}
          onChange={(point) => update('initial_point', point)}
          context={`The point must be near the group's ${group?.origin ?? 'origin'} airport.`}
        />
      </fieldset>
      <fieldset className="hotel-recovery-section">
        <legend>
          <span>2</span> Required hotel stay
        </legend>
        <p className="field-hint">
          Enter UTC times. Published property policy must support the full requested interval; an
          unsupported stay is excluded. Checkout must be on a later Europe/London calendar date. An
          explicit zero rest duration is allowed, but the requested hotel stay remains required.
        </p>
        <div className="case-form-grid">
          <label className="field">
            Required check-in (UTC)
            <input
              type="datetime-local"
              step={60}
              required
              value={value.checkin_at}
              onChange={(event) => update('checkin_at', event.target.value)}
            />
          </label>
          <label className="field">
            Required checkout (UTC)
            <input
              type="datetime-local"
              step={60}
              required
              value={value.checkout_at}
              onChange={(event) => update('checkout_at', event.target.value)}
            />
          </label>
          <label className="field">
            Rooms
            <input
              type="number"
              min={1}
              max={Math.min(group?.size ?? 30, 30)}
              step={1}
              required
              value={value.rooms}
              onChange={(event) => update('rooms', event.target.value)}
            />
          </label>
          <label className="field">
            Minimum rest (minutes)
            <input
              type="number"
              min={0}
              max={10080}
              step={1}
              required
              value={value.minimum_rest_minutes}
              onChange={(event) => update('minimum_rest_minutes', event.target.value)}
            />
          </label>
        </div>
      </fieldset>
      <fieldset className="hotel-recovery-section">
        <legend>
          <span>3</span> Return to an onward flight
        </legend>
        <div className="case-form-grid">
          <label className="field">
            UK onward-airport code (IATA)
            <input
              required
              minLength={3}
              maxLength={3}
              pattern="[A-Za-z]{3}"
              autoCapitalize="characters"
              value={value.onward_airport_code}
              onChange={(event) =>
                onChange({
                  ...value,
                  onward_airport_code: event.target.value.toUpperCase(),
                  airport_dropoff: { ...value.airport_dropoff, confirmed: false },
                })
              }
            />
          </label>
          <label className="field">
            Operator airport buffer (minutes)
            <input
              type="number"
              min={1}
              max={720}
              step={1}
              required
              value={value.airport_processing_minutes}
              onChange={(event) => update('airport_processing_minutes', event.target.value)}
            />
          </label>
        </div>
        <p className="field-hint">
          The buffer runs from driving arrival at the reported drop-off until flight departure.
          Departure terminal is unknown. This is your planning allowance, not airline guidance or a
          guarantee of boarding.
        </p>
        <DeclaredPointFields
          name="Airport drop-off"
          value={value.airport_dropoff}
          onChange={(point) => update('airport_dropoff', point)}
          context={`The point must be near ${value.onward_airport_code || 'the selected UK onward airport'}.`}
        />
      </fieldset>
      <div className="hotel-recovery-timeline" aria-label="Requested recovery sequence">
        <strong>Sequence for this attempt</strong>
        <ol>
          <li>
            <span>Airport → property</span>
            <small>Driving must arrive by the requested check-in.</small>
          </li>
          <li>
            <span>Required hotel stay</span>
            <small>The selected journey must include the full requested rest interval.</small>
          </li>
          <li>
            <span>Property → airport</span>
            <small>
              {timeline
                ? `Return pickup: ${utcTimestamp(timeline.returnPickup)}.`
                : 'Set checkout to see the return pickup.'}{' '}
              Checkout + 10 minutes is an application connection allowance, not a supplier pickup
              promise.
            </small>
          </li>
          <li>
            <span>Onward flight</span>
            <small>
              {timeline
                ? `Search date: ${timeline.flightQueryDate} (Europe/London checkout date).`
                : 'One flight query on the Europe/London checkout date.'}{' '}
              Actual driving, operator buffer and the group’s deadline must fit.
            </small>
          </li>
        </ol>
        <p className="field-hint">
          Private transfers retain their exact GBP quotes. EUR totals use the approved ECB
          reference-estimate policy and require revalidation before booking.
        </p>
      </div>
    </div>
  );
}
