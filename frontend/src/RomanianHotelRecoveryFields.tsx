import { useId } from 'react';
import type { PassengerGroup, RomanianHotelRecoveryInput, World } from './types';
import {
  emptyRomanianHotelRecoveryForm,
  romanianHotelGroupIssue,
  romanianHotelScope,
  type RomanianHotelRecoveryForm,
} from './romanianHotelRecovery';
import { utcTimestamp } from './recoveryCase';
import './hotelRecovery.css';

export default function RomanianHotelRecoveryFields({
  value,
  onChange,
  world,
  group,
  allowance,
}: {
  value: RomanianHotelRecoveryForm;
  onChange: (value: RomanianHotelRecoveryForm) => void;
  world: World;
  group?: PassengerGroup;
  allowance: number;
}) {
  const hint = useId();
  const scope = group ? romanianHotelScope(world, group) : '';
  const issue = group
    ? romanianHotelGroupIssue(group, world)
    : 'Select exactly one eligible group.';
  const changed = Boolean(value.scope && value.scope !== scope);
  const update = <K extends keyof RomanianHotelRecoveryForm>(
    key: K,
    next: RomanianHotelRecoveryForm[K],
  ) =>
    onChange({
      ...value,
      scope,
      [key]: next,
      ...(key === 'suitcases' || key === 'handbags' ? { standard_baggage_confirmed: false } : {}),
    });
  return (
    <div className="hotel-recovery-fields">
      <div className="hotel-recovery-intro">
        <strong>OTP → Hotel Aro Palace, Brașov → OTP → onward flight</strong>
        <p>
          Transfero / Direct Aeroport scheduled transport and LiteAPI hotel quotes. This package
          uses the exact Hotel Aro Palace, 27–29 Eroilor, not a city-wide hotel shortlist.
        </p>
        <p className="field-hint">
          One explicitly all-adult group, up to 12 passengers. Reported seats and room rates are
          checked during the search; no seats, rooms or vehicles are reserved.
        </p>
      </div>
      {issue && <p className="case-source-warning">{issue}</p>}
      {changed && (
        <div className="case-source-warning">
          <p>
            The passenger group, location or readiness changed. Earlier values remain visible; reset
            and review them before searching.
          </p>
          <button
            type="button"
            className="secondary-button"
            onClick={() => onChange({ ...emptyRomanianHotelRecoveryForm(), scope })}
          >
            Reset Romania package inputs
          </button>
        </div>
      )}
      <fieldset className="hotel-recovery-section" disabled={Boolean(issue) || changed}>
        <legend>Required hotel stay · UTC input</legend>
        <p className="field-hint">
          Enter the full interval. Checkout must fall on a later Europe/Bucharest date. Current
          hotel policy must support both hours; a later check-in window is not assumed. Scheduled
          outbound transport uses the check-in date; the return uses the checkout date.
        </p>
        <div className="case-form-grid">
          {(['checkin_at', 'checkout_at'] as const).map((key) => (
            <label className="field" key={key}>
              {key === 'checkin_at' ? 'Required check-in (UTC)' : 'Required checkout (UTC)'}
              <input
                type="datetime-local"
                required
                step={60}
                value={value[key]}
                onChange={(event) => update(key, event.target.value)}
              />
            </label>
          ))}
          {(
            [
              ['rooms', 'Rooms', 1, group?.size ?? 12],
              ['minimum_rest_minutes', 'Minimum rest (minutes)', 0, 10080],
              ['suitcases', 'Hold bags for the whole party', 0, group?.size ?? 12],
              ['handbags', 'Hand bags for the whole party', 0, group?.size ?? 12],
              ['hotel_connection_minutes', 'Hotel connection margin (minutes)', 20, 720],
              ['airport_processing_minutes', 'OTP processing margin (minutes)', 180, 720],
            ] as const
          ).map(([key, label, min, max]) => (
            <label className="field" key={key}>
              {label}
              <input
                type="number"
                required
                step={1}
                min={min}
                max={max}
                value={value[key]}
                onChange={(event) => update(key, event.target.value)}
              />
            </label>
          ))}
        </div>
        <label className="hotel-recovery-confirm">
          <input
            type="checkbox"
            required
            checked={value.standard_baggage_confirmed}
            aria-describedby={hint}
            onChange={(event) => update('standard_baggage_confirmed', event.target.checked)}
          />
          <span>
            I confirm each adult’s baggage is within one hold bag up to 25 kg and one hand bag up to
            10 kg. No extra or overweight baggage is required.
          </span>
        </label>
        <p className="field-hint" id={hint}>
          Counts must be entered explicitly, including zero. The supplier’s standard allowance does
          not verify individual bags.
        </p>
      </fieldset>
      <p className="field-hint">
        Current conservative application rules: at least 12 hours’ notice, 20 minutes for boarding,
        20 minutes for the hotel connection and 3 hours before the onward flight. Exact services
        must fit the declared readiness, stay and deadline; your dates are not moved to make a
        connection fit.
      </p>
      <p className="field-hint">
        Named airport and hotel facilities are source references. Exact boarding bay and hotel-door
        access are unverified; timetable arrival is estimated. Scheduled vehicles may be vans or
        minibuses.
      </p>
      <p
        className={
          Number.isInteger(allowance) && allowance >= 8 ? 'field-hint' : 'case-source-warning'
        }
      >
        This complete workflow needs at least 8 provider calls. The current limit is{' '}
        {Number.isFinite(allowance) ? allowance : 'not set'} and is never increased automatically.
        Review fresh or separately saved Romania inputs; saved UK transfers are not reused.
      </p>
    </div>
  );
}

export function RomanianHotelRequestDetails({ input }: { input?: RomanianHotelRecoveryInput }) {
  if (!input) return null;
  return (
    <section className="case-job-result" aria-label="Recorded Romania hotel request">
      <h4>Requested Romania hotel journey</h4>
      <p>Transfero / Direct Aeroport · Hotel Aro Palace · OTP return and onward flight.</p>
      <p>
        {utcTimestamp(input.checkin_at)} → {utcTimestamp(input.checkout_at)} · {input.rooms} rooms ·{' '}
        {input.minimum_rest_minutes} minutes minimum rest.
      </p>
      <p>
        {input.suitcases} hold bags · {input.handbags} hand bags · standard baggage{' '}
        {input.standard_baggage_confirmed === true ? 'declared by operator' : 'not confirmed'}.
      </p>
      <p className="field-hint">
        Hotel margin {input.hotel_connection_minutes} min · OTP margin{' '}
        {input.airport_processing_minutes} min. These are frozen request inputs, not evidence that a
        complete journey was found or booked.
      </p>
    </section>
  );
}
