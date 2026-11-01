import HotelOfferPrice from './HotelOfferPrice';
import type { HotelPricing, SupplierMoney } from './realData';
import type { Resource } from './types';

const object = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
const money = (value: unknown): value is SupplierMoney => {
  const item = object(value);
  return (
    item?.currency === 'EUR' &&
    typeof item.amount === 'string' &&
    /^(0|[1-9]\d{0,10})(\.\d{1,12})?$/.test(item.amount)
  );
};
export const isLiteApiHotel = (resource: Resource) =>
  resource.kind === 'hotel' && resource.source === 'Nuitee / LiteAPI';
const exact = (amount: string) => {
  const [whole, fraction = ''] = amount.split('.');
  return BigInt(whole) * 1_000_000_000_000n + BigInt(fraction.padEnd(12, '0'));
};

export default function HotelResourcePrice({ resource }: { resource: Resource }) {
  if (!isLiteApiHotel(resource)) return null;
  const pricing = object(resource.provenance?.pricing);
  const valid =
    pricing?.status === 'complete' &&
    money(pricing.booking_total) &&
    money(pricing.mandatory_total) &&
    Array.isArray(pricing.excluded_fee_totals) &&
    pricing.excluded_fee_totals.length <= 1 &&
    pricing.excluded_fee_totals.every(money) &&
    Array.isArray(pricing.unresolved_reasons) &&
    pricing.unresolved_reasons.length === 0 &&
    Number(pricing.booking_total.amount) === resource.quote_total_cost &&
    exact(pricing.mandatory_total.amount) ===
      exact(pricing.booking_total.amount) +
        pricing.excluded_fee_totals.reduce((sum, fee) => sum + exact(fee.amount), 0n) &&
    Number((exact(pricing.mandatory_total.amount) + 9_999_999_999n) / 10_000_000_000n) / 100 ===
      resource.budget_total_cost;
  if (!valid)
    return (
      <p className="field-hint">
        Recorded hotel mandatory total is incomplete or inconsistent. Review the full source price
        before a new plan.
      </p>
    );
  return (
    <section className="price-evidence" aria-label="Recorded hotel price including mandatory fees">
      <HotelOfferPrice
        offer={{
          id: resource.id,
          name: resource.name,
          total_price: (pricing.booking_total as SupplierMoney).amount,
          currency: 'EUR',
          url: null,
          pricing: pricing as unknown as HotelPricing,
        }}
      />
      <p>
        Entire requested stay and party · {resource.quote_rooms ?? resource.capacity} requested
        rooms. Recorded source price; no booking is established.
      </p>
    </section>
  );
}
