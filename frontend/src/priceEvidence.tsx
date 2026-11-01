import { formatCost } from './api';
import { transferFxReference } from './transferQuoteData';
import type { CostEvidence, Itinerary, Leg, Plan, PriceEvidence, Resource } from './types';
import { nativeAirportCabLeg } from './airportCabPresentation';
import './priceEvidence.css';

const exactAmount = /^(?:0|[1-9]\d{0,12})(?:\.\d{1,12})?$/;
const centsAmount = /^(?:0|[1-9]\d{0,12})\.\d{2}$/;

function money(amount: unknown, currency: 'GBP' | 'EUR', cents = false): string | null {
  if (typeof amount !== 'string' || !(cents ? centsAmount : exactAmount).test(amount)) return null;
  const [whole, fraction = ''] = amount.split('.');
  return `${currency === 'GBP' ? '£' : '€'}${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}

export function privateTransfer(
  item: Pick<Resource | Leg, 'price_evidence' | 'transport_mode'>,
): boolean {
  return (
    item.transport_mode === 'private_transfer' || item.price_evidence?.kind === 'apl_ecb_reference'
  );
}

function referenceFor(evidence: PriceEvidence | null | undefined) {
  const reference = evidence?.fx_reference;
  if (!reference) return null;
  return transferFxReference({
    state: 'ready',
    source: reference.source,
    source_url: reference.source_url,
    reference,
    cache: null,
    settlement_guaranteed: false,
    revalidation_required: true,
    message: '',
  });
}

function priceEstimate(evidence: PriceEvidence | null | undefined): string | null {
  const reference = referenceFor(evidence);
  return evidence?.kind === 'apl_ecb_reference' &&
    evidence.price_scope === 'per_vehicle' &&
    evidence.policy_id === 'ecb_reference_v1' &&
    evidence.settlement_guaranteed === false &&
    evidence.revalidation_required === true &&
    evidence.original?.currency === 'GBP' &&
    money(evidence.original.amount, 'GBP') !== null &&
    evidence.estimated_eur?.currency === 'EUR' &&
    reference !== null &&
    typeof reference.source_units_per_eur === 'string' &&
    exactAmount.test(reference.source_units_per_eur) &&
    /[1-9]/.test(reference.source_units_per_eur)
    ? money(evidence.estimated_eur.amount, 'EUR', true)
    : null;
}

function validSummary(evidence: CostEvidence | null | undefined, total: number): boolean {
  return (
    evidence?.kind === 'includes_ecb_reference' &&
    evidence.policy_id === 'ecb_reference_v1' &&
    evidence.settlement_guaranteed === false &&
    evidence.revalidation_required === true &&
    money(evidence.total_eur, 'EUR', true) !== null &&
    money(evidence.estimated_eur, 'EUR', true) !== null &&
    money(evidence.original_gbp, 'GBP') !== null &&
    Number.isFinite(total) &&
    Number(evidence.total_eur) === total
  );
}

export function costPresentation(value: Itinerary | Plan | undefined, resources: Resource[] = []) {
  const legs = value
    ? 'legs' in value
      ? value.legs
      : value.allocations.flatMap((allocation) => allocation.itinerary.legs)
    : [];
  const estimated =
    value?.cost_evidence != null ||
    legs.some(
      (leg) =>
        leg.price_evidence != null ||
        (privateTransfer(leg) && !nativeAirportCabLeg(leg, resources)),
    );
  const nativeCab = legs.some((leg) => nativeAirportCabLeg(leg, resources));
  const summary = value?.cost_evidence;
  return {
    estimated,
    amount: !estimated
      ? nativeCab
        ? new Intl.NumberFormat('en-GB', {
            style: 'currency',
            currency: 'EUR',
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
          }).format(value?.total_cost ?? 0)
        : formatCost(value?.total_cost ?? 0)
      : validSummary(summary, value!.total_cost)
        ? `≈ ${money(summary!.total_eur, 'EUR', true)}`
        : 'Estimated total unavailable',
  };
}

export function PriceEvidenceDetails({ evidence }: { evidence: PriceEvidence | null | undefined }) {
  if (evidence == null) return null;
  const original =
    evidence.original?.currency === 'GBP' ? money(evidence.original.amount, 'GBP') : null;
  const estimate = priceEstimate(evidence);
  const reference = referenceFor(evidence);
  return (
    <div className="price-evidence" aria-label="Private transfer price evidence">
      <strong>{original ? `${original} per vehicle` : 'Supplier GBP price unavailable'}</strong>
      <span>
        {estimate
          ? `Approximately ${estimate} · EUR reference estimate`
          : 'EUR reference estimate unavailable'}
      </span>
      {reference && (
        <span className="price-evidence-source">
          <a href={reference.source_url} target="_blank" rel="noreferrer">
            European Central Bank
          </a>
          {' · '}rate dated {reference.rate_date}
          {typeof reference.source_units_per_eur === 'string' &&
          exactAmount.test(reference.source_units_per_eur)
            ? ` · 1 EUR = ${reference.source_units_per_eur} GBP`
            : ''}
        </span>
      )}
      <p>EUR settlement is not guaranteed. Revalidate before booking.</p>
    </div>
  );
}

export function EstimatedCostNote({
  value,
  resources = [],
}: {
  value: Itinerary | Plan;
  resources?: Resource[];
}) {
  const { estimated } = costPresentation(value, resources);
  if (!estimated) {
    const legs =
      'legs' in value
        ? value.legs
        : value.allocations.flatMap((allocation) => allocation.itinerary.legs);
    return legs.some((leg) => nativeAirportCabLeg(leg, resources)) ? (
      <p className="estimated-cost-note">
        Includes a supplier-calculated vehicle fare in EUR, cash without extras. The quote does not
        confirm vehicle availability or create a reservation.
      </p>
    ) : null;
  }
  const summary = value.cost_evidence;
  return (
    <p className="estimated-cost-note">
      {validSummary(summary, value.total_cost)
        ? `Includes ${money(summary!.original_gbp, 'GBP')} in private-transfer charges, valued at approximately ${money(summary!.estimated_eur, 'EUR', true)} using ECB reference rates. `
        : 'The estimate breakdown is incomplete. '}
      This EUR planning total is not a guaranteed settlement amount. Revalidate before booking.
    </p>
  );
}
