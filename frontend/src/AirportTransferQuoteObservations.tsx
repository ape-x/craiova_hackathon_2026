import type { AirportTransferQuoteSummary, World } from './types';
import { freshnessTime, sourceInstant } from './resourceFreshness';

function timestamp(value: string) {
  const instant = sourceInstant(value);
  return instant === null ? 'Not recorded' : freshnessTime(instant);
}
function nativeFare(price: AirportTransferQuoteSummary['options'][number]['price']) {
  return price?.currency === 'EUR' &&
    typeof price.amount === 'string' &&
    /^(?:0|[1-9]\d{0,6})\.\d{2}$/.test(price.amount) &&
    Number(price.amount) > 0
    ? `€${price.amount}`
    : 'Native EUR fare unavailable';
}
const flag = (value: boolean) => (value === true ? 'Yes' : value === false ? 'No' : 'Not recorded');

export default function AirportTransferQuoteObservations({
  quotes,
  world,
}: {
  quotes?: AirportTransferQuoteSummary[];
  world: World;
}) {
  if (!quotes?.length) return null;
  return (
    <section className="case-job-result" aria-label="Historical airport-transfer quotes">
      <h4>Recorded Bucharest.CAB fare observations</h4>
      <p className="field-hint">
        Historical native EUR prices per vehicle, cash without extras. Driving arrival is estimated.
        Fleet availability is unknown; these quotes made no reservation and do not establish a
        complete journey.
      </p>
      {quotes.map((quote) => (
        <div key={quote.quote_id}>
          <strong>
            {world.groups.find((group) => group.id === quote.group_id)?.name ?? quote.group_id} ·
            OTP → CRA
          </strong>
          <p className="field-hint">
            Requested pickup {timestamp(quote.pickup_at)}
            <br />
            App planning cutoff recorded as {timestamp(quote.usable_until)}. This is historical
            application eligibility, not supplier expiry or current availability.
          </p>
          <ul>
            {quote.options.map((option) => (
              <li key={option.id}>
                <strong>
                  {option.car_type} · {nativeFare(option.price)} per vehicle
                </strong>
                <br />
                <span>
                  Party and bag fit at receipt: {flag(option.fits_request)} · Planning eligibility
                  at receipt: {flag(option.importable)}
                </span>
              </li>
            ))}
          </ul>
          {!quote.options.length && <p className="field-hint">No vehicle categories recorded.</p>}
          {quote.blockers.length > 0 && (
            <ul>
              {quote.blockers.map((blocker, index) => (
                <li key={`${blocker.code}-${index}`}>{blocker.message}</li>
              ))}
            </ul>
          )}
          <details className="record-references">
            <summary>Recorded source limits and references</summary>
            <p>
              Source state at receipt: {quote.state} · {quote.http_request_count} source HTTP
              requests recorded.
            </p>
            <p>
              Quote {quote.quote_id} · sequence {quote.quote_sequence} · group {quote.group_id}.
            </p>
            <p>
              Driving arrival: {quote.arrival_estimated === true ? 'estimated' : 'not verified'} ·
              Supplier booking at receipt:{' '}
              {quote.supplier_booked === false ? 'none' : 'not verified'}.
            </p>
            <ul>
              {quote.options.map((option) => (
                <li key={option.id}>
                  {option.car_type} vehicle limits: {option.max_passengers} passengers ·{' '}
                  {option.max_suitcases} suitcases · {option.max_handbags} handbags. These are
                  category limits, not remaining fleet.
                </li>
              ))}
            </ul>
          </details>
        </div>
      ))}
    </section>
  );
}
