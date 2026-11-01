import { useEffect, useId, useRef, useState } from 'react';
import { AlertTriangle, MapPin, RefreshCw } from 'lucide-react';
import { api, ApiError, TransportError } from './api';
import type { MutationResult, RecoveryIncident, World } from './types';
import { Modal } from './ui';
import { utcTimestamp } from './recoveryCase';
import { resourceTitle } from './resourceLabels';
import { isAirportNode } from './locationNodes';
import { sourceInstant } from './resourceFreshness';
import {
  affectedPendingGroups,
  arrivalReportIssue,
  latestOperatorReport,
  reportDescription,
  unavailabilityIssue,
  type ArrivalReportForm,
} from './operationalReports';
import {
  arrivalRecoveryIssue,
  arrivalReportSubmission,
  projectedArrivalRecovery,
  emptyArrivalRecoveryForm,
} from './arrivalRecoveryData';
import { ArrivalRecoveryFields, ArrivalRecoveryReceipt } from './ArrivalRecoveryFields';
import './operationalReports.css';

type MutationProps = {
  onApplied: (result: MutationResult) => void;
  onRefresh: () => Promise<unknown>;
};

export function useReportMutation(
  { onApplied, onRefresh }: MutationProps,
  onPending?: (pending: boolean) => void,
) {
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState<TransportError | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [result, setResult] = useState<MutationResult | null>(null);
  const running = useRef(false);
  const mounted = useRef(true);
  const pending = useRef(false);
  const locked = busy || Boolean(uncertain);
  pending.current = locked;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    onPending?.(locked);
  }, [locked, onPending]);
  useEffect(() => () => onPending?.(false), [onPending]);
  async function run(action: () => Promise<MutationResult>, recovering = false) {
    if (running.current || (pending.current && !recovering)) return;
    running.current = true;
    pending.current = true;
    onPending?.(true);
    setBusy(true);
    setError('');
    setSuccess('');
    setResult(null);
    try {
      const result = await action();
      if (!mounted.current) return;
      onApplied(result);
      setResult(result);
      setUncertain(null);
      pending.current = false;
      setSuccess(
        result.explanation ??
          (result.recovery_job || result.recovery_blocked
            ? 'Operator report processed. Review the recorded recovery outcome below.'
            : 'Operator report recorded. No replacement search or booking was made.'),
      );
    } catch (cause) {
      if (!mounted.current) return;
      const unknown = cause instanceof TransportError ? cause : null;
      setUncertain(unknown);
      pending.current = Boolean(unknown);
      setError(cause instanceof Error ? cause.message : 'The report could not be recorded.');
      if (cause instanceof ApiError && cause.status === 409) await onRefresh().catch(() => {});
    } finally {
      running.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function allowClose() {
    if (!running.current && !pending.current) return true;
    setError('Recover the original submission before closing. Its outcome is not yet confirmed.');
    return false;
  }
  return { locked, busy, uncertain, error, success, result, run, setError, allowClose };
}

export function ReportFeedback({ mutation }: { mutation: ReturnType<typeof useReportMutation> }) {
  return (
    <>
      {mutation.error && (
        <p className="form-error" role="alert">
          {mutation.error}
        </p>
      )}
      {mutation.success && (
        <p className="report-success" role="status">
          {mutation.success}
        </p>
      )}
      {mutation.result?.report?.type === 'arrival_report' && (
        <ArrivalRecoveryReceipt result={mutation.result} />
      )}
      {mutation.uncertain && (
        <button
          type="button"
          className="secondary-button"
          disabled={mutation.busy}
          onClick={() => void mutation.run(() => mutation.uncertain!.retry<MutationResult>(), true)}
        >
          <RefreshCw size={14} /> Recover original report
        </button>
      )}
    </>
  );
}

export function OperatorReportNote({
  provenance,
  field,
}: {
  provenance?: Record<string, unknown>;
  field: 'arrival_reports' | 'unavailability_reports';
}) {
  const report = latestOperatorReport(provenance, field);
  if (!report) return null;
  return (
    <div className="operator-report-note">
      <strong>Operator report · {utcTimestamp(report.receivedAt)}</strong>
      <p>{report.description}</p>
    </div>
  );
}

export function ArrivalReportPanel({
  world,
  incident,
  disabled = false,
  onMutationPending,
  revealRequest,
  ...props
}: MutationProps & {
  world: World;
  incident: RecoveryIncident;
  disabled?: boolean;
  onMutationPending: (pending: boolean) => void;
  revealRequest?: number;
}) {
  const section = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    if (revealRequest === undefined || !section.current) return;
    section.current.open = true;
    section.current.scrollIntoView?.({ block: 'nearest' });
    section.current.querySelector('summary')?.focus();
  }, [revealRequest]);
  const mutation = useReportMutation(props, onMutationPending);
  const airportListId = useId();
  const [form, setForm] = useState<ArrivalReportForm>({
    groupIds: [],
    airportCode: '',
    readyAt: '',
    description: '',
  });
  const [recovery, setRecovery] = useState(emptyArrivalRecoveryForm);
  const [submissionMode, setSubmissionMode] = useState<'report' | 'recover'>('report');
  const groups = world.groups.filter((group) => incident.group_ids.includes(group.id));
  const locked = mutation.locked || disabled;
  const epoch = sourceInstant(world.epoch);
  const selected = groups.filter((group) => form.groupIds.includes(group.id));
  const transferContext = projectedArrivalRecovery(world, form);
  const recoveryIssue = arrivalRecoveryIssue(recovery, selected.length, transferContext);
  return (
    <details
      ref={section}
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
        <MapPin size={16} /> Report passenger arrival after diversion
      </summary>
      <p>
        Record where the selected passengers are available to start recovery and when they will be
        ready. This is an operator report, not a Flightradar24 landing confirmation or an
        instruction to an aircraft.
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
            const input = arrivalReportSubmission(world, incident, form, recovery, action);
            setSubmissionMode(action);
            void mutation.run(() => api.reportArrival(world, incident.id, input));
          } catch (cause) {
            mutation.setError(
              cause instanceof Error ? cause.message : 'Review the arrival report.',
            );
          }
        }}
      >
        <fieldset className="case-fieldset" disabled={locked}>
          <legend>Passenger scope</legend>
          {!groups.length && (
            <p className="field-hint">
              Link passenger groups to this case before reporting their arrival.
            </p>
          )}
          <div className="report-group-list">
            {groups.map((group) => {
              const issue = arrivalReportIssue(world, incident, group);
              const readyAt =
                epoch !== null && typeof group.ready_at === 'number'
                  ? new Date(epoch + group.ready_at * 60_000).toISOString()
                  : null;
              return (
                <div key={group.id} className="report-group">
                  <label className="case-group">
                    <input
                      type="checkbox"
                      checked={form.groupIds.includes(group.id)}
                      disabled={locked || Boolean(issue)}
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
                        Current start: {group.origin} · ready{' '}
                        {readyAt ? utcTimestamp(readyAt) : 'not recorded'} · destination{' '}
                        {group.destination}
                      </small>
                      {issue && <small>{issue}</small>}
                    </span>
                  </label>
                  <OperatorReportNote provenance={group.provenance} field="arrival_reports" />
                </div>
              );
            })}
          </div>
          <div className="case-form-grid">
            <label className="field">
              Reported arrival airport · IATA
              <input
                value={form.airportCode}
                list={airportListId}
                maxLength={3}
                autoComplete="off"
                required
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
              Passengers ready · UTC
              <input
                type="datetime-local"
                step={60}
                required
                value={form.readyAt}
                onChange={(event) =>
                  setForm((previous) => ({ ...previous, readyAt: event.target.value }))
                }
              />
            </label>
          </div>
          <p className="field-hint">
            Another known airport may be entered. The server validates its reference; reporting
            arrival does not establish airport operating permission. Existing deadlines and
            destinations stay unchanged, including a deadline already missed.
          </p>
          <label className="field">
            Operator report
            <textarea
              value={form.description}
              required
              minLength={3}
              maxLength={2000}
              rows={3}
              onChange={(event) =>
                setForm((previous) => ({ ...previous, description: event.target.value }))
              }
            />
          </label>
          <p className="report-scope">
            {selected.length} groups · {selected.reduce((sum, group) => sum + group.size, 0)}{' '}
            passengers selected. Their unconfirmed proposals will be invalidated. Other groups and
            recorded bookings are preserved.
          </p>
          <ArrivalRecoveryFields
            value={recovery}
            onChange={setRecovery}
            transferContext={transferContext}
          />
          {recoveryIssue && selected.length > 0 && (
            <p className="field-hint arrival-recovery-blocked">{recoveryIssue}</p>
          )}
          <div className="arrival-report-actions">
            <button
              type="submit"
              name="arrival_action"
              value="report"
              formNoValidate
              className="secondary-button"
              disabled={locked || !form.groupIds.length}
            >
              {mutation.busy && submissionMode === 'report'
                ? 'Recording report…'
                : 'Record arrival only'}
            </button>
            <button
              type="submit"
              name="arrival_action"
              value="recover"
              className="primary-button"
              disabled={locked || Boolean(recoveryIssue) || !form.groupIds.length}
            >
              {mutation.busy && submissionMode === 'recover'
                ? 'Recording and queueing…'
                : 'Record arrival and recalculate'}
            </button>
          </div>
          <p className="field-hint">
            Report-only changes passenger location/readiness without starting a search.
            Recalculation uses exactly these selected groups and can replace their earlier attempts;
            in-flight calls must finish and unknown outcomes require review. Results are proposals,
            not bookings.
          </p>
        </fieldset>
      </form>
      <ReportFeedback mutation={mutation} />
    </details>
  );
}

export function ResourceUnavailabilityDialog({
  world,
  resourceId,
  onClose,
  ...props
}: MutationProps & {
  world: World;
  resourceId: string;
  onClose: () => void;
}) {
  const [description, setDescription] = useState('');
  const mutation = useReportMutation(props);
  const resource = world.resources.find((item) => item.id === resourceId);
  const issue = resource
    ? unavailabilityIssue(world, resource)
    : 'This service is no longer in the current operation.';
  const affected = affectedPendingGroups(world, resourceId);
  return (
    <Modal
      title="Report service unavailable"
      onClose={() => {
        if (mutation.allowClose()) onClose();
      }}
    >
      {resource && (
        <>
          <h3>{resourceTitle(resource)}</h3>
          <p className="field-hint report-service-reference">
            {resource.source} · {resource.id}
          </p>
        </>
      )}
      <p>
        Record an operator-reported disruption to this service. Source prices, schedules and
        supplier evidence are retained. This does not cancel a supplier booking or request a
        replacement.
      </p>
      {issue && (
        <p className="form-error" role="alert">
          {issue}
        </p>
      )}
      <div className="report-scope">
        <AlertTriangle size={16} />
        <div>
          <strong>
            {affected.reduce((sum, group) => sum + group.size, 0)} passengers in {affected.length}{' '}
            groups currently have dependent unconfirmed proposals.
          </strong>
          {affected.length > 0 && <p>{affected.map((group) => group.name).join(', ')}</p>}
          <p>
            Only those pending proposals will be invalidated. Other services and groups remain in
            place.
          </p>
        </div>
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (mutation.locked || issue) return;
          try {
            const input = { description: reportDescription(description) };
            void mutation.run(() => api.reportUnavailability(world, resourceId, input));
          } catch (cause) {
            mutation.setError(
              cause instanceof Error ? cause.message : 'Review the service report.',
            );
          }
        }}
      >
        <fieldset className="case-fieldset" disabled={mutation.locked || Boolean(issue)}>
          <label className="field">
            Operator report
            <textarea
              required
              minLength={3}
              maxLength={2000}
              rows={4}
              value={description}
              data-autofocus
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <button className="primary-button" disabled={mutation.locked || Boolean(issue)}>
            {mutation.busy ? 'Recording report…' : 'Record service unavailability'}
          </button>
        </fieldset>
      </form>
      <ReportFeedback mutation={mutation} />
    </Modal>
  );
}
