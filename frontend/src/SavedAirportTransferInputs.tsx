import { useEffect, useRef, useState } from 'react';
import { api, TransportError } from './api';
import type { AirportTransferProfiles, RecoveryIncident, World } from './types';
import type { AirportTransferDraft } from './airportTransferRecovery';
import { airportTransferGroupIssue } from './airportTransferRecovery';
import { freshnessTime, sourceInstant } from './resourceFreshness';
import {
  AirportTransferProfileReadGate,
  airportTransferProfileReason,
  airportTransferProfileScope,
  loadSavedAirportTransferInput,
  verifyAirportTransferProfiles,
} from './savedAirportTransferData';

type Props = {
  world: World;
  incident: RecoveryIncident;
  groupIds: string[];
  value: AirportTransferDraft[];
  onChange: (value: AirportTransferDraft[]) => void;
  disabled: boolean;
};
type Review = { scope: string; response: AirportTransferProfiles };

export function SavedAirportTransferInputOptions({
  world,
  groupIds,
  value,
  response,
  disabled,
  onLoad,
}: Pick<Props, 'world' | 'groupIds' | 'value' | 'disabled'> & {
  response: AirportTransferProfiles;
  onLoad: (groupId: string) => void;
}) {
  return (
    <div aria-label="Saved airport-transfer inputs for selected groups">
      {groupIds.map((id) => {
        const profile = response.profiles.find((item) => item.group_id === id);
        const name = world.groups.find((group) => group.id === id)?.name ?? id;
        const issue = airportTransferGroupIssue(world, id);
        return (
          <div className="case-job-result" key={id}>
            <strong>
              {name} · {id}
            </strong>
            {profile?.configured ? (
              <>
                <p>
                  OTP → CRA · pickup {freshnessTime(sourceInstant(profile.inputs.pickup_at)!)}
                  <br />
                  {profile.inputs.suitcases} suitcases · {profile.inputs.handbags} handbags
                </p>
                <p className="field-hint">
                  Saved {freshnessTime(sourceInstant(profile.saved_at)!)} · source attempt{' '}
                  {profile.source_job_id}. Saved inputs are not a fare or a reservation.
                </p>
                {issue && <p className="field-hint">{issue}</p>}
                <button
                  type="button"
                  className="secondary-button"
                  disabled={disabled || Boolean(issue)}
                  onClick={() => onLoad(id)}
                >
                  {value.some((item) => item.group_id === id)
                    ? 'Replace this group’s transfer inputs with saved values'
                    : 'Load this group’s saved inputs for review'}
                </button>
              </>
            ) : (
              <p className="field-hint">
                {airportTransferProfileReason(profile?.reason ?? 'missing')}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function SavedAirportTransferInputs(props: Props) {
  const { world, incident, groupIds, value, disabled } = props;
  const scope = airportTransferProfileScope(world, incident, groupIds);
  const latest = useRef({ ...props, scope });
  latest.current = { ...props, scope };
  const gate = useRef(new AirportTransferProfileReadGate());
  const controller = useRef<AbortController | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busyScope, setBusyScope] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ scope: string; text: string; error: boolean } | null>(
    null,
  );
  useEffect(() => {
    return () => {
      gate.current.invalidate();
      controller.current?.abort();
    };
  }, [scope]);
  const busy = busyScope === scope;
  const visibleReview = review?.scope === scope ? review : null;
  const visibleNotice = notice?.scope === scope ? notice : null;
  async function read() {
    if (disabled || busy) return;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    const token = gate.current.begin();
    setBusyScope(scope);
    setNotice(null);
    setReview(null);
    try {
      const response = verifyAirportTransferProfiles(
        await api.airportTransferProfiles(incident.id, current.signal),
      );
      if (current.signal.aborted || !gate.current.accepts(token, scope, latest.current.scope))
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
            ? 'Saved inputs could not be read. Try reviewing again; no provider search was started.'
            : cause instanceof Error
              ? cause.message
              : 'Saved inputs could not be read.',
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
      const drafts = loadSavedAirportTransferInput({
        profile,
        responseVersion: visibleReview.response.world_version,
        reviewedScope: visibleReview.scope,
        world: current.world,
        incident: current.incident,
        groupIds: current.groupIds,
        drafts: current.value,
      });
      current.onChange(drafts);
      setNotice({
        scope: current.scope,
        error: false,
        text: 'Saved inputs loaded into the form above. Pickup and bags are unchanged; review them, then submit explicitly. No search started and your call limit is unchanged.',
      });
    } catch (cause) {
      setNotice({
        scope: current.scope,
        error: true,
        text: cause instanceof Error ? cause.message : 'Saved inputs could not be loaded.',
      });
    }
  }
  return (
    <section className="arrival-recovery-options" aria-label="Review saved airport-transfer inputs">
      <strong>Earlier Bucharest.CAB configuration</strong>
      <p className="field-hint">
        Review saved pickup and bag totals for selected groups. Loading fills the form without
        searching, changing the selection or increasing the call limit. Past dates stay exactly as
        saved; timing and source eligibility are checked again when you submit.
      </p>
      <button
        type="button"
        className="secondary-button"
        disabled={disabled || busy}
        onClick={() => void read()}
      >
        {busy ? 'Reading saved inputs…' : 'Review saved transfer inputs'}
      </button>
      {visibleReview && (
        <SavedAirportTransferInputOptions
          world={world}
          groupIds={groupIds}
          value={value}
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
