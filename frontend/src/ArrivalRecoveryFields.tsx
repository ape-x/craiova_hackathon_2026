import type { MutationResult, World } from './types';
import AirportTransferRecoveryFields from './AirportTransferRecoveryFields';
import AfterFlightTransferFields from './AfterFlightTransferFields';
import { arrivalRecoveryReceipt } from './arrivalRecoveryData';
import type { ArrivalRecoveryForm } from './arrivalRecoveryData';

export function ArrivalRecoveryFields({
  value,
  onChange,
  legend = 'Optional recalculation from the reported arrival',
  transferContext,
}: {
  value: ArrivalRecoveryForm;
  onChange: (value: ArrivalRecoveryForm) => void;
  legend?: string;
  transferContext?: {
    world: World;
    groupIds: string[];
    scopeKey?: string;
    reportedArrivalGroupIds?: string[];
  } | null;
}) {
  return (
    <fieldset className="arrival-recovery-options">
      <legend>{legend}</legend>
      <div className="case-form-grid">
        <label className="field">
          Recovery objective
          <select
            value={value.objective}
            onChange={(event) =>
              onChange({
                ...value,
                objective: event.target.value as ArrivalRecoveryForm['objective'],
              })
            }
          >
            <option value="balanced">Balanced</option>
            <option value="fastest">Earliest arrival</option>
            <option value="lowest_cost">Lowest cost</option>
          </select>
        </label>
        <label className="field">
          Maximum recovery calls · 3–26
          <input
            inputMode="numeric"
            type="text"
            value={value.maxProviderCalls}
            maxLength={2}
            onChange={(event) => onChange({ ...value, maxProviderCalls: event.target.value })}
          />
        </label>
      </div>
      <fieldset className="case-job-modes">
        <legend>Services to consider</legend>
        {(['flight', 'bus', 'hotel'] as const).map((mode) => (
          <label key={mode}>
            <input
              type="checkbox"
              checked={value.modes.includes(mode)}
              onChange={(event) =>
                onChange({
                  ...value,
                  modes: event.target.checked
                    ? [...value.modes, mode]
                    : value.modes.filter((item) => item !== mode),
                })
              }
            />
            {mode === 'flight'
              ? 'Flights'
              : mode === 'bus'
                ? 'Ground transport'
                : 'Hotels · fresh setup required'}
          </label>
        ))}
      </fieldset>
      <p className="field-hint">
        Only the selected services are requested. Ground transport uses verified links and available
        source searches; fare references do not establish seats or new transport inventory. No
        interpretation call is added to this direct request.
      </p>
      {transferContext &&
        ((value.modes.includes('bus') && !value.afterFlightTransfer) ||
          Boolean(value.airportTransfers?.length)) && (
          <AirportTransferRecoveryFields
            {...transferContext}
            value={value.airportTransfers ?? []}
            onChange={(airportTransfers) => onChange({ ...value, airportTransfers })}
            modes={value.modes}
            allowance={Number(value.maxProviderCalls)}
            hotelRecovery={value.modes.includes('hotel')}
          />
        )}
      {transferContext === null && Boolean(value.airportTransfers?.length) && (
        <p className="case-source-warning">
          The reported airport or readiness is incomplete. Airport-transfer setup cannot be reused
          until reviewed.
        </p>
      )}
      {transferContext && (value.modes.includes('bus') || Boolean(value.afterFlightTransfer)) && (
        <AfterFlightTransferFields
          {...transferContext}
          value={value.afterFlightTransfer}
          onChange={(afterFlightTransfer) => onChange({ ...value, afterFlightTransfer })}
          modes={value.modes}
          allowance={Number(value.maxProviderCalls)}
          incompatibleSetup={
            value.modes.includes('hotel') || Boolean(value.airportTransfers?.length)
          }
        />
      )}
      {transferContext === null && value.afterFlightTransfer && (
        <p className="case-source-warning">
          The reported airport or readiness is incomplete. Review the after-flight setup before
          recalculating.
        </p>
      )}
    </fieldset>
  );
}

export function ArrivalRecoveryReceipt({ result }: { result: MutationResult }) {
  const receipt = arrivalRecoveryReceipt(result);
  if (!receipt) return null;
  return (
    <section
      className={`arrival-recovery-receipt ${receipt.warning ? 'warning' : ''}`}
      aria-label="Recorded arrival recovery outcome"
    >
      <h4>{receipt.title}</h4>
      <p>{receipt.detail}</p>
      {result.replayed && (
        <p className="field-hint">Recovered original response · no new submission was made.</p>
      )}
    </section>
  );
}
