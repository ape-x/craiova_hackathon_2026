export interface TransferQuoteForm {
  origin: string;
  destination: string;
  date: string;
  time: string;
  passengers: string;
  suitcases: string;
}

export interface TransferQuoteRequest {
  origin: string;
  destination: string;
  pickup_at: string;
  passengers: number;
  suitcases: number;
}

export interface TransferStatus {
  provider: string;
  configured: boolean;
  timezone: 'Europe/London';
  currency: 'GBP';
  booking_supported: false;
}

export interface TransferQuoteResult {
  provider: string;
  source_url: string;
  received_at: string;
  source_quoted_at: string | null;
  expires_at: string | null;
  request: TransferQuoteRequest & {
    transfer_date: string;
    transfer_time: string;
    timezone: 'Europe/London';
  };
  resolved_route: {
    pickup: string | null;
    dropoff: string | null;
    hub: string | null;
    direction: 'from_hub' | 'to_hub' | null;
    resolved_address: string | null;
    pickup_terminal: string | null;
  };
  options: {
    id: string;
    car_type: string;
    price: { amount: string; currency: 'GBP' };
    price_scope: 'per_vehicle';
    max_passengers: number;
    max_suitcases: number;
    fits_request: boolean;
    duration_seconds: null;
    arrival_at: null;
    availability_status: 'quoted_not_reserved';
    accessibility_confirmed: false;
  }[];
  provider_duration_text: string | null;
  message: string | null;
}

const londonParts = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/London',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

function wallClock(timestamp: number): number {
  const parts = Object.fromEntries(
    londonParts.formatToParts(timestamp).map(({ type, value }) => [type, value]),
  );
  return Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
  );
}

/** Resolve London wall time using timezone data, independent of browser timezone.
 * Repeated autumn times and missing spring times cannot be represented safely by
 * APL's local-date/local-time API and are rejected rather than guessed.
 */
export function londonPickup(date: string, time: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))
    throw new Error('Enter a pickup date and time in Europe/London.');
  const wall = Date.parse(`${date}T${time}:00Z`);
  if (!Number.isFinite(wall) || new Date(wall).toISOString().slice(0, 16) !== `${date}T${time}`)
    throw new Error('Enter a valid pickup date.');
  const offsets = new Set(
    [-36, 0, 36].map((hours) => {
      const sample = wall + hours * 3_600_000;
      return wallClock(sample) - sample;
    }),
  );
  const matches = [...offsets]
    .map((offset) => wall - offset)
    .filter((value) => wallClock(value) === wall);
  if (!matches.length)
    throw new Error(
      'That London time does not exist because the clocks move forward. Choose another time.',
    );
  if (matches.length !== 1)
    throw new Error(
      'That London time occurs twice when the clocks go back. Choose an unambiguous time outside that hour.',
    );
  return new Date(matches[0]).toISOString();
}

export function transferRequest(form: TransferQuoteForm, now = Date.now()): TransferQuoteRequest {
  const origin = form.origin.trim(),
    destination = form.destination.trim();
  if (
    [origin, destination].some(
      (value) => value.length < 2 || value.length > 500 || /[\u0000-\u001f]/.test(value),
    )
  )
    throw new Error('Enter a UK airport, full address or postcode for both locations.');
  if (origin.toLowerCase() === destination.toLowerCase())
    throw new Error('Pickup and dropoff must be different locations.');
  const count = (value: string, min: number, max: number, label: string) => {
    if (!/^\d+$/.test(value) || Number(value) < min || Number(value) > max)
      throw new Error(`Enter an explicit whole-number ${label} count (${min}–${max}).`);
    return Number(value);
  };
  const passengers = count(form.passengers, 1, 100, 'passenger');
  const suitcases = count(form.suitcases, 0, 200, 'suitcase');
  const pickup_at = londonPickup(form.date, form.time);
  if (Date.parse(pickup_at) <= now)
    throw new Error('Choose a future pickup time in Europe/London.');
  return { origin, destination, passengers, suitcases, pickup_at };
}

export function transferPrice(amount: string): string {
  if (!/^\d+(\.\d+)?$/.test(amount)) return 'Price unavailable';
  const [whole, fraction = ''] = amount.split('.');
  return `£${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}

export function londonTimestamp(value: string): string {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return 'time unavailable';
  return (
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/London',
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(time) + ' London time'
  );
}
