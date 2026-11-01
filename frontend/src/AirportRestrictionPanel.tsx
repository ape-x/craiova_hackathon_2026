import { useId, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { api } from './api';
import type { AirportOperation, MutationResult, RecoveryIncident, World } from './types';
import { ArrivalRecoveryFields } from './ArrivalRecoveryFields';
import { emptyArrivalRecoveryForm } from './arrivalRecoveryData';
import { ReportFeedback, useReportMutation } from './OperationalReportForms';
import { isAirportNode } from './locationNodes';
import { utcTimestamp } from './recoveryCase';
import {
  activeCaseRestrictions,
  airportOperationsLabel,
  airportRestrictionRecoveryIssue,
  airportRestrictionRecoveryReceipt,
  airportRestrictionSubmission,
  emptyAirportRestrictionForm,
  restrictionRecoveryGroupIssue,
} from './airportRestrictionData';
import './operationalReports.css';

export function CaseAirportRestrictions({ incident }: { incident: RecoveryIncident }) {
  const active = activeCaseRestrictions(incident);
  return (
    <section className="case-airport-restrictions" aria-label="Current case airport restrictions">
      <h4>
        Airport restrictions for this case <span>{active.length} active directions</span>
      </h4>
      {!active.length && (
        <p className="field-hint">No active airport restriction recorded for this case.</p>
      )}
      {active.map((item) => (
        <div className="operator-report-note" key={`${item.airport_code}:${item.operation}`}>
          <strong>
            {item.airport_code} · {airportOperationsLabel([item.operation])} restricted
          </strong>
          <p>{item.description}</p>
          <small>
            Operator report · {utcTimestamp(item.received_at)}
            {item.inherited_from_legacy || incident.unavailable_airport_code === item.airport_code
              ? ' · Original incident constraint'
              : ''}
          </small>
        </div>
      ))}
      {Boolean(incident.airport_restriction_reports?.length) && (
        <details className="case-restriction-history">
          <summary>Recent restriction updates · operator reports</summary>
          <ol>
            {[...(incident.airport_restriction_reports ?? [])].reverse().map((report) => (
              <li key={report.id}>
                <strong>
                  {report.airport_code} · {airportOperationsLabel(report.operations)} ·{' '}
                  {report.action === 'clear' ? 'Case restriction cleared' : 'Restricted'}
                </strong>
                <small>{utcTimestamp(report.received_at)}</small>
                <p>{report.description}</p>
              </li>
            ))}
          </ol>
        </details>
      )}
      <p className="field-hint">
        Case planning constraints · airport operating status and Flightradar24 observations are
        separate.
      </p>
    </section>
  );
}

export function AirportRestrictionReceipt({ result }: { result: MutationResult }) {
  const report = result.report;
  if (report?.type !== 'airport_restriction') return null;
  if (result.applied !== true || result.incident_id !== report.incident_id)
    return (
      <p className="form-error" role="alert">
        Restriction receipt needs review. The response does not confirm this case update.
      </p>
    );
  const recovery = airportRestrictionRecoveryReceipt(result);
  const conflicts = result.confirmed_conflicts ?? report.confirmed_conflicts ?? [];
  const needsReview =
    result.manual_review_required || report.manual_review_required || conflicts.length > 0;
  return (
    <section className="arrival-recovery-receipt" aria-label="Recorded airport restriction outcome">
      <h4>
        {report.airport_code} · {airportOperationsLabel(report.operations)} ·{' '}
        {report.action === 'clear' ? 'Case restriction cleared' : 'Case restriction recorded'}
      </h4>
      {report.action === 'clear' && (
        <p>
          Removed this case constraint. Airport opening and operating permission remain unconfirmed.
        </p>
      )}
      <p>
        {report.affected_offer_ids.length} pending proposals invalidated ·{' '}
        {report.affected_group_ids.length} affected groups. Passenger locations unchanged.
      </p>
      {report.affected_offer_ids.length > 0 && (
        <p className="field-hint">Proposal references: {report.affected_offer_ids.join(', ')}</p>
      )}
      {needsReview && (
        <div className="arrival-recovery-blocked" role="status">
          <strong>Recorded bookings require operator review</strong>
          <p>These bookings were not changed or cancelled.</p>
          {conflicts.length > 0 && (
            <ul>
              {conflicts.map((conflict, index) => (
                <li key={`${conflict.group_id}:${conflict.offer_id}:${index}`}>
                  Group {conflict.group_id}
                  {conflict.offer_id ? ` · offer ${conflict.offer_id}` : ''}
                  {conflict.resource_ids.length
                    ? ` · services ${conflict.resource_ids.join(', ')}`
                    : ''}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {recovery && (
        <div className={recovery.warning ? 'arrival-recovery-blocked' : undefined}>
          <h4>{recovery.title}</h4>
          <p>{recovery.detail}</p>
        </div>
      )}
      {!recovery && <p>No recovery search was included in this recorded response.</p>}
      {result.replayed && (
        <p className="field-hint">Recovered original response · no new submission was made.</p>
      )}
    </section>
  );
}

export function AirportRestrictionPanel({
  world,
  incident,
  disabled = false,
  onApplied,
  onRefresh,
  onMutationPending,
}: {
  world: World;
  incident: RecoveryIncident;
  disabled?: boolean;
  onApplied: (result: MutationResult) => void;
  onRefresh: () => Promise<unknown>;
  onMutationPending: (pending: boolean) => void;
}) {
  const mutation = useReportMutation({ onApplied, onRefresh }, onMutationPending);
  const [form, setForm] = useState(emptyAirportRestrictionForm);
  const [recovery, setRecovery] = useState(emptyArrivalRecoveryForm);
  const [submission, setSubmission] = useState<'report' | 'recover'>('report');
  const airportListId = useId();
  const locked = disabled || mutation.locked;
  const groups = world.groups.filter((group) => incident.group_ids.includes(group.id));
  const issue = airportRestrictionRecoveryIssue(recovery, form.groupIds.length);
  return (
    <details
      className="operational-report"
      onToggle={(event) => {
        if (mutation.locked) event.currentTarget.open = true;
      }}
    >
      <summary
        onClick={(event) => {
          if (mutation.locked) event.preventDefault();
        }}
      >
        <ShieldAlert size={16} /> Update airport restrictions for this case
      </summary>
      <p>
        Restrict arrivals, departures or both for this case. Clearing removes only this case’s
        constraint; it does not confirm the airport is open.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (locked) return;
          try {
            const action =
              (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value') === 'recover'
                ? 'recover'
                : 'report';
            const input = airportRestrictionSubmission(world, incident, form, recovery, action);
            setSubmission(action);
            void mutation.run(() => api.reportAirportRestriction(world, incident.id, input));
          } catch (cause) {
            mutation.setError(
              cause instanceof Error ? cause.message : 'Review the restriction update.',
            );
          }
        }}
      >
        <fieldset className="case-fieldset" disabled={locked}>
          <div className="case-form-grid">
            <label className="field">
              Airport · IATA
              <input
                required
                maxLength={3}
                autoComplete="off"
                list={airportListId}
                value={form.airportCode}
                placeholder="OTP"
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    airportCode: event.target.value.toUpperCase(),
                  }))
                }
              />
              <datalist id={airportListId}>
                {world.airports?.filter(isAirportNode).map((airport) => (
                  <option key={airport.id} value={airport.code}>
                    {airport.name}
                  </option>
                ))}
              </datalist>
            </label>
            <label className="field">
              Action
              <select
                value={form.action}
                onChange={(event) =>
                  setForm((previous) => ({
                    ...previous,
                    action: event.target.value as 'restrict' | 'clear',
                  }))
                }
              >
                <option value="restrict">Restrict for this case</option>
                <option value="clear">Clear case restriction</option>
              </select>
            </label>
          </div>
          <fieldset className="case-job-modes">
            <legend>Airport operations</legend>
            {(['arrivals', 'departures'] as AirportOperation[]).map((operation) => (
              <label key={operation}>
                <input
                  type="checkbox"
                  checked={form.operations.includes(operation)}
                  onChange={(event) =>
                    setForm((previous) => ({
                      ...previous,
                      operations: event.target.checked
                        ? [...previous.operations, operation]
                        : previous.operations.filter((item) => item !== operation),
                    }))
                  }
                />
                {airportOperationsLabel([operation])}
              </label>
            ))}
          </fieldset>
          <label className="field">
            Operator explanation
            <textarea
              rows={3}
              required
              minLength={3}
              maxLength={2000}
              value={form.description}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, description: event.target.value }))
              }
            />
          </label>
          <p className="field-hint">
            Dependent pending proposals may be invalidated. Recorded bookings are flagged for
            review. Passenger locations stay unchanged.
          </p>
          <fieldset className="case-fieldset">
            <legend>Groups to recalculate · optional</legend>
            {!groups.length && (
              <p className="field-hint">
                The restriction can be recorded now. Link passenger groups before requesting
                recalculation.
              </p>
            )}
            <div className="report-group-list">
              {groups.map((group) => {
                const groupIssue = restrictionRecoveryGroupIssue(world, incident, group);
                return (
                  <label className="case-group" key={group.id}>
                    <input
                      type="checkbox"
                      checked={form.groupIds.includes(group.id)}
                      disabled={Boolean(groupIssue)}
                      onChange={(event) =>
                        setForm((previous) => ({
                          ...previous,
                          groupIds: event.target.checked
                            ? [...previous.groupIds, group.id]
                            : previous.groupIds.filter((id) => id !== group.id),
                        }))
                      }
                    />
                    <span>
                      <strong>
                        {group.name} · {group.size} passengers
                      </strong>
                      <small>
                        {group.id} · {group.origin} → {group.destination}
                      </small>
                      {groupIssue && <small>{groupIssue}</small>}
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
          <ArrivalRecoveryFields
            value={recovery}
            onChange={setRecovery}
            legend="Optional recalculation with the updated case restriction"
          />
          {issue && <p className="field-hint arrival-recovery-blocked">{issue}</p>}
          <div className="arrival-report-actions">
            <button
              className="secondary-button"
              type="submit"
              name="restriction_action"
              value="report"
            >
              {mutation.busy && submission === 'report'
                ? 'Recording…'
                : 'Record restriction update only'}
            </button>
            <button
              className="primary-button"
              type="submit"
              name="restriction_action"
              value="recover"
              disabled={Boolean(issue)}
            >
              {mutation.busy && submission === 'recover'
                ? 'Recording and queuing…'
                : 'Record and recalculate selected groups'}
            </button>
          </div>
        </fieldset>
      </form>
      <ReportFeedback mutation={mutation} />
      {mutation.result && <AirportRestrictionReceipt result={mutation.result} />}
    </details>
  );
}
