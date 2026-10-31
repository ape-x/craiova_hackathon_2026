import { formatOfferPrice } from './realData';
import type { RealHotelOffer } from './realData';

const pricingReason: Record<string, string> = {
  fees_not_disclosed: 'The supplier has not disclosed whether additional fees apply.',
  excluded_fee_amount_missing: 'The amount of an additional mandatory fee is missing.',
  excluded_fee_currency_missing: 'The currency of an additional mandatory fee is missing.',
  unsupported_budget_currency:
    'A sourced currency conversion is required to compare the full cost.',
};

export default function HotelOfferPrice({ offer }: { offer: RealHotelOffer }) {
  const pricing = offer.pricing;
  const total = pricing?.status === 'complete' ? pricing.mandatory_total : null;
  return (
    <div>
      <strong className="real-offer-price">
        {formatOfferPrice(total?.amount ?? offer.total_price, total?.currency ?? offer.currency)}
      </strong>
      <span>{total ? 'Total including mandatory fees' : 'Supplier quote'}</span>
      {pricing && pricing.excluded_fee_totals.length > 0 && (
        <>
          <span>
            Quoted stay:{' '}
            {formatOfferPrice(pricing.booking_total.amount, pricing.booking_total.currency)}
          </span>
          {pricing.excluded_fee_totals.map((fee) => (
            <span key={fee.currency}>
              Additional mandatory fees: {formatOfferPrice(fee.amount, fee.currency)}
            </span>
          ))}
        </>
      )}
      {!total && <span>Mandatory total requires verification.</span>}
      {pricing && !total && pricing.unresolved_reasons.length > 0 && (
        <span>
          {[
            ...new Set(
              pricing.unresolved_reasons.map(
                (reason) => pricingReason[reason] ?? 'Supplier fee details require verification.',
              ),
            ),
          ].join(' ')}
        </span>
      )}
      <span>Payment timing is unconfirmed.</span>
    </div>
  );
}
