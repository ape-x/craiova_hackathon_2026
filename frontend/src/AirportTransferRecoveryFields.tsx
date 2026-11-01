import type { AirportTransferRecoveryInput, ResourceKind, World } from './types';
import {
  airportTransferInputs,
  airportTransferGroupIssue,
  airportTransferScope,
  emptyAirportTransferDraft,
} from './airportTransferRecovery';
import type { AirportTransferDraft } from './airportTransferRecovery';
import { freshnessTime, sourceInstant } from './resourceFreshness';

export default function AirportTransferRecoveryFields({
  world,
  groupIds,
  modes,
  allowance,
  value,
  onChange,
  hotelRecovery = false,
  scopeKey = '',
}: {
  world: World;
  groupIds: string[];
  modes: ResourceKind[];
  allowance: number;
  value: AirportTransferDraft[];
  onChange: (value: AirportTransferDraft[]) => void;
  hotelRecovery?: boolean;
  scopeKey?: string;
}) {
  const scope = airportTransferScope(world, groupIds, scopeKey);
  let issue = '';
  try {
    airportTransferInputs(value, world, groupIds, modes, allowance, hotelRecovery, scopeKey);
  } catch (cause) {
    issue = cause instanceof Error ? cause.message : 'Review the transfer inputs.';
  }
  const groups = world.groups.filter((group) => groupIds.includes(group.id));
  return (
    <fieldset className="arrival-recovery-options">
      <legend>Optional Bucharest.CAB searches · OTP → CRA</legend>
      <p className="field-hint">
        Opt in separately for each selected group. Each search uses two provider calls within your
        total allowance. Pickup and bag totals are explicit planning inputs; readiness is unchanged.
      </p>
      {groups.map((group) => {
        const selected = value.some((item) => item.group_id === group.id),
          reason = airportTransferGroupIssue(world, group.id);
        return (
          <label className="case-group" key={group.id}>
            <input
              type="checkbox"
              checked={selected}
              disabled={
                !selected &&
                (Boolean(reason) || !modes.includes('bus') || hotelRecovery || value.length >= 12)
              }
              onChange={(event) =>
                onChange(
                  event.target.checked
                    ? [...value, emptyAirportTransferDraft(group.id, scope)]
                    : value.filter((item) => item.group_id !== group.id),
                )
              }
            />
            <span>
              <strong>
                {group.name} · {group.size} passengers
              </strong>
              <small>
                {reason ??
                  `${group.origin} → CRA initial leg · final destination ${group.destination}`}
              </small>
            </span>
          </label>
        );
      })}
      {!groups.length && <p className="field-hint">Select passenger groups first.</p>}
      {!modes.includes('bus') && (
        <p className="field-hint">Select Ground transport to enable these searches.</p>
      )}
      {hotelRecovery && (
        <p className="case-source-warning">
          This source cannot be combined with the hotel recovery workflow.
        </p>
      )}
      {value.map((draft) => {
        const group = world.groups.find((item) => item.id === draft.group_id);
        const epoch = sourceInstant(world.epoch);
        const groupTime = (minutes: number | undefined) =>
          epoch !== null && minutes !== undefined && Number.isFinite(minutes)
            ? freshnessTime(epoch + minutes * 60_000)
            : 'Not recorded';
        const stale = draft.scope !== scope || !groupIds.includes(draft.group_id);
        const update = (field: 'pickup_at' | 'suitcases' | 'handbags', next: string) =>
          onChange(
            value.map((item) =>
              item.group_id === draft.group_id ? { ...item, [field]: next } : item,
            ),
          );
        return (
          <fieldset className="arrival-recovery-options" key={draft.group_id}>
            <legend>{group?.name ?? draft.group_id} · requested transfer</legend>
            <p className="field-hint">
              Readiness used for this request: {groupTime(group?.ready_at)} · deadline{' '}
              {groupTime(group?.deadline)}.
            </p>
            {stale && (
              <p role="status" className="case-source-warning">
                The group selection, location or readiness changed. Reset these inputs or remove
                this transfer.
              </p>
            )}
            <div className="case-form-grid">
              <label className="field">
                Pickup date and time (UTC)
                <input
                  type="datetime-local"
                  step="60"
                  value={draft.pickup_at}
                  disabled={stale}
                  onChange={(event) => update('pickup_at', event.target.value)}
                />
              </label>
              <label className="field">
                Total suitcases for this group
                <input
                  type="number"
                  min="0"
                  max="7"
                  step="1"
                  value={draft.suitcases}
                  disabled={stale}
                  onChange={(event) => update('suitcases', event.target.value)}
                />
              </label>
              <label className="field">
                Total handbags for this group
                <input
                  type="number"
                  min="0"
                  max="7"
                  step="1"
                  value={draft.handbags}
                  disabled={stale}
                  onChange={(event) => update('handbags', event.target.value)}
                />
              </label>
            </div>
            {stale && (
              <button
                type="button"
                className="secondary-button"
                disabled={
                  !groupIds.includes(draft.group_id) ||
                  Boolean(airportTransferGroupIssue(world, draft.group_id))
                }
                onClick={() =>
                  onChange(
                    value.map((item) =>
                      item.group_id === draft.group_id
                        ? emptyAirportTransferDraft(draft.group_id, scope)
                        : item,
                    ),
                  )
                }
              >
                Reset transfer inputs
              </button>
            )}
            <button
              type="button"
              className="secondary-button"
              onClick={() => onChange(value.filter((item) => item.group_id !== draft.group_id))}
            >
              Remove this transfer search
            </button>
          </fieldset>
        );
      })}
      {value.length > 0 && (
        <p className="field-hint">
          Minimum {2 * value.length + 1} calls for {value.length} transfer searches and a decision.
          A new flight search usually needs two more calls (5 total for one transfer). Your limit
          remains {Number.isFinite(allowance) ? allowance : 'unset'}.
        </p>
      )}
      {issue && (
        <p role="status" className="case-source-warning">
          {issue}
        </p>
      )}
      <p className="field-hint">
        1–7 recorded adults per group · current conservative advance-notice rule: at least 9 hours,
        rechecked when dispatched · maximum 31 days. Fares are per vehicle; fleet availability is
        unconfirmed and no booking is made. Driving arrival is estimated. The application can
        allocate at most one Bucharest.CAB vehicle per plan; searching several groups does not
        secure several vehicles. A complete onward itinerary is still required.
      </p>
    </fieldset>
  );
}

export function AirportTransferRequestDetails({
  inputs,
  world,
}: {
  inputs?: AirportTransferRecoveryInput[];
  world: World;
}) {
  if (!inputs?.length) return null;
  return (
    <section className="case-job-result" aria-label="Recorded airport-transfer request">
      <h4>Requested Bucharest.CAB searches</h4>
      <p>
        Frozen request inputs · initial OTP → CRA legs. This records authorization, not availability
        or a booking.
      </p>
      <ul>
        {inputs.map((input) => {
          const pickup = sourceInstant(input.pickup_at);
          return (
            <li key={input.group_id}>
              <strong>
                {world.groups.find((group) => group.id === input.group_id)?.name ?? input.group_id}
              </strong>{' '}
              · {input.group_id}
              <br />
              Pickup {pickup === null ? input.pickup_at : freshnessTime(pickup)} · {input.suitcases}{' '}
              suitcases · {input.handbags} handbags
            </li>
          );
        })}
      </ul>
      <p>
        {inputs.length * 2} source HTTP requests authorized within the job’s total limit. Actual
        dispatched calls are shown above.
      </p>
    </section>
  );
}
