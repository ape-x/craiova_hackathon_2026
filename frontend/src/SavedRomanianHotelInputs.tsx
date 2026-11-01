import { useEffect, useRef, useState } from 'react';
import { api, TransportError } from './api';
import type { RomanianHotelRecoveryProfile } from './types';
import {
  romanianHotelRecoveryInput,
  type RomanianHotelRecoveryForm,
} from './romanianHotelRecovery';
import { freshnessTime, sourceInstant } from './resourceFreshness';
import {
  loadSavedRomanianHotelInput,
  romanianHotelDraftHasValues,
  romanianHotelProfileLoadIssue,
  romanianHotelProfileReason,
  romanianHotelProfileScope,
  RomanianHotelProfileReadGate,
  verifyRomanianHotelProfile,
  type RomanianHotelProfileLoadScope,
} from './savedRomanianHotelData';

type Props = RomanianHotelProfileLoadScope & {
  disabled: boolean;
  onChange: (draft: RomanianHotelRecoveryForm) => void;
};
type Review = { scope: string; profile: RomanianHotelRecoveryProfile };

export function SavedRomanianHotelInputReview(
  props: Omit<Props, 'onChange'> & {
    profile: RomanianHotelRecoveryProfile;
    replacementConfirmed: boolean;
    onReplacementChange: (confirmed: boolean) => void;
    onLoad: () => void;
  },
) {
  const {
    profile,
    world,
    groupIds,
    allowance,
    draft,
    disabled,
    replacementConfirmed,
    onReplacementChange,
    onLoad,
  } = props;
  let issue = romanianHotelProfileLoadIssue(props),
    validation = '';
  if (profile.configured && !issue) {
    let saved: RomanianHotelRecoveryForm | undefined;
    try {
      saved = loadSavedRomanianHotelInput({
        ...props,
        reviewedScope: romanianHotelProfileScope(props),
        replacementConfirmed: true,
        disabled: false,
      });
    } catch (cause) {
      issue = cause instanceof Error ? cause.message : 'Review the saved input scope.';
    }
    if (saved) {
      try {
        romanianHotelRecoveryInput(saved, groupIds, world, allowance);
      } catch (cause) {
        validation =
          cause instanceof Error
            ? cause.message
            : 'Review current dates, readiness, party and allowance.';
      }
    }
  }
  const replacing = romanianHotelDraftHasValues(draft);
  return (
    <div className="case-job-result" aria-label="Saved Romania hotel input receipt">
      <strong>
        {world.groups.find((group) => group.id === profile.group_id)?.name ?? profile.group_id} ·{' '}
        {profile.group_id}
      </strong>
      {profile.configured ? (
        <>
          <p>Hotel Aro Palace · Transfero / Direct Aeroport · OTP return and onward flight.</p>
          <p>
            Check-in {freshnessTime(sourceInstant(profile.inputs.checkin_at)!)} → checkout{' '}
            {freshnessTime(sourceInstant(profile.inputs.checkout_at)!)}.
          </p>
          <p>
            {profile.inputs.rooms} rooms · {profile.inputs.minimum_rest_minutes} minutes minimum
            rest · {profile.inputs.suitcases} hold bags · {profile.inputs.handbags} hand bags.
          </p>
          <p>
            Hotel margin {profile.inputs.hotel_connection_minutes} min · OTP margin{' '}
            {profile.inputs.airport_processing_minutes} min · standard baggage declaration saved.
          </p>
          <p className="field-hint">
            Saved {freshnessTime(sourceInstant(profile.saved_at)!)} · source attempt{' '}
            {profile.source_job_id}. These are operator inputs from a complete successful proposal,
            not current fares, seats, rooms or a reservation.
          </p>
          {issue && <p className="case-source-warning">{issue}</p>}
          {validation && (
            <p className="case-source-warning">
              Review before searching: {validation} Loading preserves the saved values for editing.
            </p>
          )}
          {replacing && (
            <label className="hotel-recovery-confirm">
              <input
                type="checkbox"
                checked={replacementConfirmed}
                disabled={disabled || Boolean(issue)}
                onChange={(event) => onReplacementChange(event.target.checked)}
              />
              <span>Replace the current Romania inputs with these reviewed saved values.</span>
            </label>
          )}
          <button
            type="button"
            className="secondary-button"
            disabled={disabled || Boolean(issue) || (replacing && !replacementConfirmed)}
            onClick={onLoad}
          >
            {replacing
              ? 'Replace Romania inputs for review'
              : 'Load saved Romania inputs for review'}
          </button>
        </>
      ) : (
        <p className="field-hint">{romanianHotelProfileReason(profile.reason)}</p>
      )}
    </div>
  );
}

export default function SavedRomanianHotelInputs(props: Props) {
  const { world, incident, groupIds, disabled } = props;
  const scope = romanianHotelProfileScope(props);
  const latest = useRef({ ...props, scope });
  latest.current = { ...props, scope };
  const gate = useRef(new RomanianHotelProfileReadGate());
  const controller = useRef<AbortController | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busyScope, setBusyScope] = useState<string | null>(null);
  const [replacementScope, setReplacementScope] = useState<string | null>(null);
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
  const busy = busyScope === scope,
    visibleReview = review?.scope === scope ? review : null,
    visibleNotice = notice?.scope === scope ? notice : null;
  const issue = romanianHotelProfileLoadIssue(props);
  async function read() {
    if (disabled || busy || groupIds.length !== 1 || !props.packageSelected) return;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const token = gate.current.begin();
    setBusyScope(scope);
    setReview(null);
    setNotice(null);
    setReplacementScope(null);
    try {
      const profile = verifyRomanianHotelProfile(
        await api.romanianHotelRecoveryProfile(incident.id, groupIds[0], request.signal),
      );
      if (
        request.signal.aborted ||
        latest.current.disabled ||
        !gate.current.accepts(token, scope, latest.current.scope)
      )
        return;
      if (
        profile.world_version !== world.version ||
        profile.incident_id !== incident.id ||
        profile.group_id !== groupIds[0]
      )
        throw new Error(
          'The operation version, case or group differs from this saved response. Refresh the case and review again.',
        );
      setReview({ scope, profile });
    } catch (cause) {
      if (request.signal.aborted || !gate.current.accepts(token, scope, latest.current.scope))
        return;
      setNotice({
        scope,
        error: true,
        text:
          cause instanceof TransportError
            ? 'Saved Romania inputs could not be read. Review again when connected; no supplier search was started.'
            : cause instanceof Error
              ? cause.message
              : 'Saved Romania inputs could not be read.',
      });
    } finally {
      if (gate.current.accepts(token, scope, latest.current.scope)) setBusyScope(null);
    }
  }
  function load() {
    if (!visibleReview) return;
    const current = latest.current;
    try {
      const next = loadSavedRomanianHotelInput({
        ...current,
        profile: visibleReview.profile,
        reviewedScope: visibleReview.scope,
        replacementConfirmed: replacementScope === current.scope,
      });
      const nextScope = romanianHotelProfileScope({ ...current, draft: next });
      setReview(null);
      setReplacementScope(null);
      current.onChange(next);
      setNotice({
        scope: nextScope,
        error: false,
        text: 'Saved Romania inputs loaded for review. Dates, rooms, bags and margins are unchanged. Your selection, services and call limit are unchanged. Review the form, then submit explicitly; no search started.',
      });
    } catch (cause) {
      setNotice({
        scope: current.scope,
        error: true,
        text: cause instanceof Error ? cause.message : 'Saved Romania inputs could not be loaded.',
      });
    }
  }
  return (
    <section className="hotel-recovery-saved" aria-label="Review saved Romania hotel inputs">
      <strong>Saved Romania hotel setup</strong>
      <p>
        Review the operator inputs saved after a complete successful Romania proposal. Loading fills
        this form only; it does not reuse fares or availability and does not start a search.
      </p>
      <p className="field-hint">
        Past dates stay exact. Review current party requirements, readiness and deadline; hotel
        policy, transport schedules and source availability are checked again during an explicit new
        search.
      </p>
      {issue && !visibleReview && <p className="field-hint">{issue}</p>}
      <button
        type="button"
        className="secondary-button"
        disabled={disabled || busy || groupIds.length !== 1 || !props.packageSelected}
        onClick={() => void read()}
      >
        {busy ? 'Reading saved Romania inputs…' : 'Review saved Romania inputs'}
      </button>
      {visibleReview && (
        <SavedRomanianHotelInputReview
          {...props}
          profile={visibleReview.profile}
          disabled={disabled || busy}
          replacementConfirmed={replacementScope === scope}
          onReplacementChange={(confirmed) => setReplacementScope(confirmed ? scope : null)}
          onLoad={load}
        />
      )}
      {visibleNotice && (
        <p
          role={visibleNotice.error ? 'alert' : 'status'}
          className={visibleNotice.error ? 'case-source-warning' : 'field-hint'}
        >
          {visibleNotice.text}
        </p>
      )}
    </section>
  );
}
