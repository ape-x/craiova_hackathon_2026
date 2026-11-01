import type { Itinerary, Leg, Resource } from './types';
import { sourceInstant } from './resourceFreshness';

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
export function isAirportCab(resource: Resource | undefined): boolean {
  return (
    resource?.source === 'Bucharest.CAB' &&
    resource.kind === 'bus' &&
    resource.transport_mode === 'private_transfer'
  );
}

/** Uses the immutable native-EUR source option, never an inferred FX conversion. */
export function airportCabFare(resource: Resource | undefined, leg?: Leg) {
  if (
    !resource ||
    !isAirportCab(resource) ||
    resource.capacity_scope !== 'private_vehicle_quote' ||
    resource.price_evidence != null ||
    resource.budget_total_cost != null ||
    resource.origin !== 'OTP' ||
    resource.destination !== 'CRA' ||
    resource.eligible_group_ids?.length !== 1
  )
    return null;
  const quote = object(resource.provenance?.airport_cab_quote);
  const query = object(quote?.request);
  const optionId = resource.provenance?.airport_cab_option_id;
  const options = Array.isArray(quote?.options) ? quote.options.filter(object) : [];
  const option = options.find((item) => item.id === optionId);
  const price = object(option?.price);
  if (
    quote?.provider !== 'Bucharest.CAB' ||
    quote.source_url !== 'https://www.bucharest.cab/en/book' ||
    quote.booking_supported !== false ||
    quote.price_basis !== 'supplier_client_formula_cash_no_extras' ||
    sourceInstant(quote.received_at) === null ||
    query?.origin_airport !== resource.origin ||
    query.destination_airport !== resource.destination ||
    query.passengers !== resource.capacity ||
    option?.price_scope !== 'per_vehicle' ||
    option.fits_request !== true ||
    price?.currency !== 'EUR' ||
    typeof price.amount !== 'string' ||
    !/^(?:0|[1-9]\d{0,6})\.\d{2}$/.test(price.amount) ||
    Number(price.amount) <= 0 ||
    Number(price.amount) !== resource.quote_total_cost
  )
    return null;
  if (
    leg &&
    (leg.resource_id !== resource.id ||
      leg.kind !== 'bus' ||
      leg.transport_mode !== 'private_transfer' ||
      leg.price_evidence != null ||
      leg.origin !== resource.origin ||
      leg.destination !== resource.destination ||
      leg.departure !== resource.departure ||
      leg.arrival !== resource.arrival ||
      leg.cost !== resource.quote_total_cost)
  )
    return null;
  return {
    amount: price.amount,
    display: `€${price.amount}`,
    receivedAt: quote.received_at as string,
  };
}
export function nativeAirportCabLeg(leg: Leg, resources: Resource[]) {
  return airportCabFare(
    resources.find((resource) => resource.id === leg.resource_id),
    leg,
  );
}
export function airportCabFinalArrival(itinerary: Itinerary, resources: Resource[]): boolean {
  const leg = itinerary.legs.at(-1);
  return Boolean(
    leg && isAirportCab(resources.find((resource) => resource.id === leg.resource_id)),
  );
}
export function AirportCabFareDetails({ resource, leg }: { resource: Resource; leg?: Leg }) {
  const fare = airportCabFare(resource, leg);
  if (!isAirportCab(resource)) return null;
  return (
    <div className="price-evidence" aria-label="Native EUR vehicle fare">
      <strong>
        {fare
          ? `${fare.display} per vehicle · cash, no extras`
          : 'Native EUR fare evidence unavailable'}
      </strong>
      <span>
        Bucharest.CAB supplier-calculated fare. Payment-link and optional-extra fees are excluded.
      </span>
      <p>Vehicle availability is unconfirmed. This fare quote does not create a reservation.</p>
    </div>
  );
}
