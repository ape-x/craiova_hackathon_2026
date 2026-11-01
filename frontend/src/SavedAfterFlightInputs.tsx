import { useEffect, useRef, useState } from 'react';
import { api, TransportError } from './api';
import type { AfterFlightTransferProfiles } from './types';
import { afterFlightTransferInput, type AfterFlightTransferDraft } from './afterFlightTransfer';
import { freshnessTime, sourceInstant } from './resourceFreshness';
import {
  AfterFlightProfileReadGate,
  afterFlightProfileLoadIssue,
  afterFlightProfileReason,
  afterFlightProfileScope,
  loadSavedAfterFlightInput,
  savedAfterFlightDraft,
  verifyAfterFlightProfiles,
  type AfterFlightProfileLoadScope,
} from './savedAfterFlightData';

type Props = AfterFlightProfileLoadScope & {
  allowance: number;
  disabled: boolean;
  onChange: (draft: AfterFlightTransferDraft) => void;
};
type Review = { scope: string; response: AfterFlightTransferProfiles };

export function SavedAfterFlightInputOptions(
  props: Omit<Props, 'onChange'> & {
    response: AfterFlightTransferProfiles;
    onLoad: (groupId: string) => void;
  },
) {
  const {
    world,
    groupIds,
    response,
    modes,
    allowance,
    incompatibleSetup,
    draft,
    disabled,
    onLoad,
  } = props;
  const issue = afterFlightProfileLoadIssue(props);
  return (
    <div aria-label="Saved flight and transfer inputs for selected groups">
      {groupIds.map((id) => {
        const profile = response.profiles.find((item) => item.group_id === id);
        let validation = '';
        if (profile?.configured && !issue) {
          try {
            afterFlightTransferInput(
              savedAfterFlightDraft(profile, world, groupIds),
              world,
              groupIds,
              modes,
              allowance,
              incompatibleSetup,
            );
          } catch (cause) {
            validation =
              cause instanceof Error
                ? cause.message
                : 'Review the saved window and current search requirements.';
          }
        }
        return (
          <div className="case-job-result" key={id}>
            <strong>
              {world.groups.find((group) => group.id === id)?.name ?? id} · {id}
            </strong>
            {profile?.configured ? (
              <>
                <p>
                  Flight via OTP → private transfer to CRA.
                  <br />
                  Pickup window: {freshnessTime(
                    sourceInstant(profile.inputs.pickup_window_start)!,
                  )}{' '}
                  – {freshnessTime(sourceInstant(profile.inputs.pickup_window_end)!)}.<br />
                  Arrival processing: {profile.inputs.airport_processing_minutes} minutes ·{' '}
                  {profile.inputs.suitcases} suitcases · {profile.inputs.handbags} handbags · at
                  most {profile.inputs.max_inbound_options} inbound options.
                </p>
                <p className="field-hint">
                  Saved {freshnessTime(sourceInstant(profile.saved_at)!)} · source attempt{' '}
                  {profile.source_job_id}. These inputs do not reuse a flight, fare or reservation.
                </p>
                {issue && <p className="field-hint">{issue}</p>}
                {validation && (
                  <p className="case-source-warning">
                    Review before searching: {validation} Loading preserves these saved values for
                    editing.
                  </p>
                )}
                <button
                  type="button"
                  className="secondary-button"
                  disabled={disabled || Boolean(issue)}
                  onClick={() => onLoad(id)}
                >
                  {draft
                    ? 'Replace this group’s after-flight inputs with saved values'
                    : 'Load saved flight + transfer inputs for review'}
                </button>
              </>
            ) : (
              <p className="field-hint">{afterFlightProfileReason(profile?.reason ?? 'missing')}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function SavedAfterFlightInputs(props: Props) {
  const { world, incident, groupIds, modes, incompatibleSetup, disabled } = props;
  const scope = afterFlightProfileScope(world, incident, groupIds, modes, incompatibleSetup);
  const latest = useRef({ ...props, scope });
  latest.current = { ...props, scope };
  const gate = useRef(new AfterFlightProfileReadGate());
  const controller = useRef<AbortController | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busyScope, setBusyScope] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ scope: string; text: string; error: boolean } | null>(
    null,
  );
  useEffect(
    () => () => {
      gate.current.invalidate();
      controller.current?.abort();
    },
    [scope],
  );
  const busy = busyScope === scope;
  const visibleReview = review?.scope === scope ? review : null;
  const visibleNotice = notice?.scope === scope ? notice : null;
  async function read() {
    if (disabled || busy || !groupIds.length) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const token = gate.current.begin();
    setBusyScope(scope);
    setNotice(null);
    setReview(null);
    try {
      const response = verifyAfterFlightProfiles(
        await api.afterFlightTransferProfiles(incident.id, current.signal),
      );
      if (
        current.signal.aborted ||
        latest.current.disabled ||
        !gate.current.accepts(token, scope, latest.current.scope)
      )
        return;
      if (response.world_version !== world.version)
        throw new Error(
          'The operation changed during this lookup. Refresh the case and review again.',
        );
      setReview({ scope, response });
    } catch (cause) {
      if (current.signal.aborted || !gate.current.accepts(token, scope, latest.current.scope))
        return;
      setNotice({
        scope,
        error: true,
        text:
          cause instanceof TransportError
            ? 'Saved flight + transfer inputs could not be read. Review again when connected; no provider search was started.'
            : cause instanceof Error
              ? cause.message
              : 'Saved flight + transfer inputs could not be read.',
      });
    } finally {
      if (gate.current.accepts(token, scope, latest.current.scope)) setBusyScope(null);
    }
  }
  function load(id: string) {
    const current = latest.current;
    if (current.disabled || !visibleReview) return;
    const profile = visibleReview.response.profiles.find((item) => item.group_id === id);
    if (!profile) return;
    try {
      const next = loadSavedAfterFlightInput({
        ...current,
        profile,
        responseVersion: visibleReview.response.world_version,
        reviewedScope: visibleReview.scope,
      });
      current.onChange(next);
      setNotice({
        scope: current.scope,
        error: false,
        text: 'Saved flight + transfer inputs loaded into the form above. Dates, bags, processing and the maximum options are unchanged. Review them, then submit explicitly. No search started; your selection, services and call limit are unchanged.',
      });
    } catch (cause) {
      setNotice({
        scope: current.scope,
        error: true,
        text:
          cause instanceof Error
            ? cause.message
            : 'Saved flight + transfer inputs could not be loaded.',
      });
    }
  }
  return (
    <section className="arrival-recovery-options" aria-label="Review saved after-flight inputs">
      <strong>Saved flight + transfer setup</strong>
      <p className="field-hint">
        Review the separately saved pickup window, processing allowance and bag totals. Loading
        fills this form without searching or changing your call limit. Past dates stay exact;
        current readiness, deadline and source rules must be checked again before a new journey can
        be proposed.
      </p>
      <button
        type="button"
        className="secondary-button"
        disabled={disabled || busy || !groupIds.length}
        onClick={() => void read()}
      >
        {busy ? 'Reading saved flight + transfer inputs…' : 'Review saved flight + transfer inputs'}
      </button>
      {visibleReview && (
        <SavedAfterFlightInputOptions
          {...props}
          response={visibleReview.response}
          disabled={disabled || busy}
          onLoad={load}
        />
      )}
      {visibleNotice && (
        <p
          role={visibleNotice.error ? 'alert' : 'status'}
          className={visibleNotice.error ? 'form-error' : 'field-hint'}
        >
          {visibleNotice.text}
        </p>
      )}
    </section>
  );
}
