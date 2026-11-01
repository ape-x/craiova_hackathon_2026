import type { AfterFlightTransferInput, ResourceKind, World } from './types';
import {
  afterFlightTransferGroupIssue,
  afterFlightTransferInput,
  emptyAfterFlightTransfer,
  type AfterFlightTransferDraft,
} from './afterFlightTransfer';
import { airportTransferScope } from './airportTransferRecovery';
import { freshnessTime, sourceInstant } from './resourceFreshness';

export default function AfterFlightTransferFields({
  world,
  groupIds,
  modes,
  allowance,
  value,
  onChange,
  incompatibleSetup = false,
  scopeKey = '',
  reportedArrivalGroupIds = [],
}: {
  world: World;
  groupIds: string[];
  modes: ResourceKind[];
  allowance: number;
  value?: AfterFlightTransferDraft | null;
  onChange: (value: AfterFlightTransferDraft | null) => void;
  incompatibleSetup?: boolean;
  scopeKey?: string;
  reportedArrivalGroupIds?: string[];
}) {
  const scope = airportTransferScope(world, groupIds, scopeKey),
    eligibility = afterFlightTransferGroupIssue(world, groupIds, reportedArrivalGroupIds);
  const incompatible = incompatibleSetup || modes.includes('hotel');
  const stale = Boolean(value && (value.scope !== scope || value.group_id !== groupIds[0]));
  let issue = '';
  try {
    afterFlightTransferInput(
      value,
      world,
      groupIds,
      modes,
      allowance,
      incompatible,
      scopeKey,
      Date.now(),
      reportedArrivalGroupIds,
    );
  } catch (cause) {
    issue = cause instanceof Error ? cause.message : 'Review after-flight transfer inputs.';
  }
  const update = (field: keyof AfterFlightTransferDraft, next: string) =>
    value && onChange({ ...value, [field]: next });
  return (
    <fieldset className="arrival-recovery-options">
      <legend>Flight to OTP + transfer to CRA</legend>
      <label className="case-group">
        <input
          type="checkbox"
          checked={Boolean(value)}
          disabled={
            !value &&
            (Boolean(eligibility) ||
              incompatible ||
              !modes.includes('flight') ||
              !modes.includes('bus'))
          }
          onChange={(event) =>
            onChange(event.target.checked ? emptyAfterFlightTransfer(groupIds[0], scope) : null)
          }
        />
        <span>
          <strong>Search flights arriving at OTP</strong>
          <small>
            Search a sourced flight, then quote a connecting Bucharest.CAB vehicle to CRA.
          </small>
        </span>
      </label>
      {eligibility && <p className="field-hint">{eligibility}</p>}
      {(!modes.includes('flight') || !modes.includes('bus')) && (
        <p className="field-hint">Select Flights and Ground transport to enable this workflow.</p>
      )}
      {incompatible && (
        <p className="case-source-warning">
          Remove hotel and initial airport-transfer setup before enabling this separate workflow.
          Existing saved initial pickup times are not reused.
        </p>
      )}
      {value && (
        <>
          <p className="field-hint">
            {world.groups.find((group) => group.id === value.group_id)?.name ?? value.group_id} ·
            origin used for this request{' '}
            {world.groups.find((group) => group.id === value.group_id)?.origin ?? 'unavailable'}.
            This search does not move passengers or report a landing.
          </p>
          <div className="case-form-grid">
            {(['pickup_window_start', 'pickup_window_end'] as const).map((field) => (
              <label className="field" key={field}>
                {field === 'pickup_window_start'
                  ? 'Earliest permitted pickup (UTC)'
                  : 'Latest permitted pickup (UTC)'}
                <input
                  type="datetime-local"
                  step="60"
                  disabled={stale}
                  value={value[field]}
                  onChange={(event) => update(field, event.target.value)}
                />
              </label>
            ))}
            <label className="field">
              Arrival processing allowance (minutes)
              <input
                type="number"
                min="1"
                max="720"
                step="1"
                disabled={stale}
                value={value.airport_processing_minutes}
                onChange={(event) => update('airport_processing_minutes', event.target.value)}
              />
            </label>
            {(['suitcases', 'handbags'] as const).map((field) => (
              <label className="field" key={field}>
                Total {field} for this group
                <input
                  type="number"
                  min="0"
                  max="7"
                  step="1"
                  disabled={stale}
                  value={value[field]}
                  onChange={(event) => update(field, event.target.value)}
                />
              </label>
            ))}
            <label className="field">
              Maximum inbound flight options
              <select
                value={value.max_inbound_options}
                disabled={stale}
                onChange={(event) => update('max_inbound_options', event.target.value)}
              >
                <option value="1">Up to 1 option · minimum 6 calls</option>
                <option value="2">Up to 2 options · 8 calls for both</option>
              </select>
            </label>
          </div>
          <p className="field-hint">
            Pickup is the later of your window start and the sourced flight’s arrival plus your
            processing allowance, rounded up to a whole minute. Candidates outside the window or
            source rules are rejected. Terminal, baggage collection and connection protection are
            unverified.
          </p>
          <p className="field-hint">
            Minimum 6 calls for one candidate; 8 for two. Your limit remains{' '}
            {Number.isFinite(allowance) ? allowance : 'unset'}. A limit below 8 permits at most one
            candidate; the selected maximum is not a promise of results. No fare, vehicle or
            complete itinerary is guaranteed.
          </p>
          {stale && (
            <button
              type="button"
              className="secondary-button"
              disabled={Boolean(eligibility) || incompatible}
              onClick={() => onChange(emptyAfterFlightTransfer(groupIds[0], scope))}
            >
              Reset after-flight inputs
            </button>
          )}
          <button type="button" className="secondary-button" onClick={() => onChange(null)}>
            Remove after-flight transfer search
          </button>
        </>
      )}
      {issue && (
        <p role="status" className="case-source-warning">
          {issue}
        </p>
      )}
      <p className="field-hint">
        Pickup window within seven days. Supplier advance-notice requirements are checked against
        each derived pickup at quote time; pickup is not moved just to meet those rules. Native EUR
        vehicle fare, fleet unconfirmed, no booking. Road arrival is an estimate without
        departure-time traffic.
      </p>
    </fieldset>
  );
}

export function AfterFlightTransferRequestDetails({
  input,
  world,
}: {
  input?: AfterFlightTransferInput | null;
  world: World;
}) {
  if (!input) return null;
  const time = (value: string) => {
    const instant = sourceInstant(value);
    return instant === null ? 'Not recorded' : freshnessTime(instant);
  };
  return (
    <section className="case-job-result" aria-label="Recorded flight and airport-transfer request">
      <h4>Requested flight to OTP + transfer to CRA</h4>
      <p>
        {world.groups.find((group) => group.id === input.group_id)?.name ?? input.group_id} ·{' '}
        {input.group_id}. Frozen authorization, not a landed position or booking.
      </p>
      <p>
        Pickup window: {time(input.pickup_window_start)} – {time(input.pickup_window_end)}. Operator
        arrival processing: {input.airport_processing_minutes} minutes. {input.suitcases} suitcases
        · {input.handbags} handbags.
      </p>
      <p>
        At most {input.max_inbound_options} inbound options. Each pickup is derived from its exact
        sourced flight arrival and this window. Road arrival remains estimated; no vehicle
        availability or protected connection is confirmed.
      </p>
    </section>
  );
}
