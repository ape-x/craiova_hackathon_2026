import { request } from './api';
import { bucharestDate } from './realData';
import { transferFxReady, transferFxReference } from './transferQuoteData';
import type { TransferFx } from './transferQuoteData';

export interface CoachFareRequest {
  origin_airport: string;
  destination_airport: string;
  travel_date: string;
  adults: number;
}
export interface CoachFareStatus {
  provider: 'BILETERIA';
  configured: boolean;
  booking_supported: false;
  inventory_verified: false;
  adult_limit: number;
  max_advance_days?: number;
  source_url: string;
  supported_routes: {
    origin_airport: string;
    origin_name: string;
    destination_airport: string;
    destination_name: string;
  }[];
}
export interface CoachFareForm {
  origin_airport: string;
  destination_airport: string;
  travel_date: string;
  adults: string;
}
export interface CoachFareOption {
  id: string;
  operator: string;
  departure_date: string;
  arrival_date: string;
  departure_time: string;
  arrival_time: string;
  duration_text: string;
  departure_stop: { name: string; locality: string; source_url: string };
  arrival_stop: { name: string; locality: string; source_url: string };
  price: { amount: string; currency: 'RON' };
  price_scope: 'requested_party';
  adults: number;
  seat_availability: null;
  supplier_valid_until: null;
  purchase_action_enabled: boolean;
  source_url: string;
  notices: string[];
  eur_reference_estimate?: {
    amount: string;
    currency: 'EUR';
    kind: 'ecb_reference_estimate';
    rounding: 'half_even_to_cent';
  } | null;
}
export type CoachFareFx = TransferFx & { source_currency: 'RON' };
export interface CoachFareResult {
  provider: 'BILETERIA';
  source_url: string;
  received_at: string;
  request: CoachFareRequest;
  options: CoachFareOption[];
  rejected_count: number;
  notices: string[];
  booking_supported: false;
  fx?: CoachFareFx;
}
export interface GroundFareReference {
  group_ids: string[];
  result: CoachFareResult;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function text(value: unknown, limit = 500): value is string {
  return (
    typeof value === 'string' &&
    value.trim().length > 0 &&
    value.length <= limit &&
    !/[\u0000-\u001f]/.test(value)
  );
}
function date(value: unknown): value is string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const instant = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(instant) && new Date(instant).toISOString().slice(0, 10) === value;
}
function timestamp(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}
function money(value: unknown): value is string {
  return typeof value === 'string' && /^\d{1,7}(?:\.\d{1,12})?$/.test(value);
}
function notices(value: unknown): value is string[] {
  return Array.isArray(value) && value.length <= 30 && value.every((item) => text(item, 2000));
}
function requestShape(value: unknown): value is CoachFareRequest {
  const item = record(value);
  return Boolean(
    item &&
    item.origin_airport === 'CRA' &&
    item.destination_airport === 'OTP' &&
    date(item.travel_date) &&
    Number.isInteger(item.adults) &&
    Number(item.adults) >= 1 &&
    Number(item.adults) <= 8,
  );
}

/** Only public dated searches are navigable; never a checkout or an arbitrary supplier URL. */
export function coachSourceUrl(value: unknown, query?: CoachFareRequest): string | null {
  if (typeof value !== 'string' || value.length > 2000 || /[\u0000-\u0020]/.test(value))
    return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== 'https:' ||
      !['www.bileteria.ro', 'bileteria.ro'].includes(url.hostname) ||
      url.port ||
      url.username ||
      url.password ||
      url.hash ||
      url.pathname !== '/Transport/AeroportCraiova-Otopeni'
    )
      return null;
    if ([...url.searchParams.keys()].some((key) => !['zi', 'pas', 'lang'].includes(key)))
      return null;
    if (
      url.searchParams.getAll('zi').length !== 1 ||
      url.searchParams.getAll('pas').length !== 1 ||
      url.searchParams.getAll('lang').length > 1
    )
      return null;
    if (
      query &&
      (url.searchParams.get('zi') !== query.travel_date ||
        url.searchParams.get('pas') !== `${query.adults}-0-0-0-0`)
    )
      return null;
    return value;
  } catch {
    return null;
  }
}

export function coachRequest(
  form: CoachFareForm,
  status: CoachFareStatus,
  today = bucharestDate(),
): CoachFareRequest {
  if (
    !status.configured ||
    status.provider !== 'BILETERIA' ||
    status.booking_supported !== false ||
    status.inventory_verified !== false
  )
    throw new Error('The coach fare source is unavailable.');
  const body = { ...form, adults: /^\d+$/.test(form.adults) ? Number(form.adults) : NaN };
  if (!requestShape(body) || body.adults > status.adult_limit)
    throw new Error(
      'Choose a supported route, valid date and an explicit adult count from 1 to 8.',
    );
  if (
    !status.supported_routes.some(
      (route) =>
        route.origin_airport === body.origin_airport &&
        route.destination_airport === body.destination_airport,
    )
  )
    throw new Error('This airport route is not supported by the connected fare source.');
  const days =
    (Date.parse(`${body.travel_date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000;
  const maximum = status.max_advance_days ?? 31;
  if (!Number.isInteger(days) || days < 0 || days > maximum)
    throw new Error(`Choose a departure date from today through the next ${maximum} days.`);
  return body;
}

export function verifyCoachFareStatus(value: unknown): CoachFareStatus {
  const item = record(value);
  if (
    !item ||
    item.provider !== 'BILETERIA' ||
    typeof item.configured !== 'boolean' ||
    item.booking_supported !== false ||
    item.inventory_verified !== false ||
    !Number.isInteger(item.adult_limit) ||
    Number(item.adult_limit) < 1 ||
    Number(item.adult_limit) > 8 ||
    (item.max_advance_days !== undefined &&
      (!Number.isInteger(item.max_advance_days) ||
        Number(item.max_advance_days) < 1 ||
        Number(item.max_advance_days) > 31)) ||
    !Array.isArray(item.supported_routes) ||
    item.supported_routes.length !== 1
  )
    throw new Error('Coach fare source status is inconsistent.');
  const route = record(item.supported_routes[0]);
  if (
    !route ||
    route.origin_airport !== 'CRA' ||
    route.destination_airport !== 'OTP' ||
    !text(route.origin_name) ||
    !text(route.destination_name)
  )
    throw new Error('Coach fare source route is inconsistent.');
  return value as CoachFareStatus;
}

export function verifyCoachFareResult(
  value: unknown,
  expected?: CoachFareRequest,
): CoachFareResult {
  const item = record(value);
  const query = item?.request;
  const fail = () => {
    throw new Error(
      'The source response did not preserve the requested date, party or fare evidence. No transport was added.',
    );
  };
  if (
    !item ||
    item.provider !== 'BILETERIA' ||
    !requestShape(query) ||
    item.booking_supported !== false ||
    !timestamp(item.received_at) ||
    !notices(item.notices) ||
    !Number.isInteger(item.rejected_count) ||
    Number(item.rejected_count) < 0 ||
    !Array.isArray(item.options) ||
    item.options.length > 30 ||
    !coachSourceUrl(item.source_url, query)
  )
    return fail();
  if (
    expected &&
    (Object.keys(expected) as (keyof CoachFareRequest)[]).some(
      (key) => query[key] !== expected[key],
    )
  )
    return fail();
  const ids = new Set<string>();
  for (const raw of item.options) {
    const option = record(raw),
      price = record(option?.price);
    if (
      !option ||
      !text(option.id, 200) ||
      ids.has(option.id) ||
      !text(option.operator) ||
      !text(option.duration_text, 100) ||
      !date(option.departure_date) ||
      option.departure_date !== query.travel_date ||
      !date(option.arrival_date) ||
      !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(String(option.departure_time)) ||
      !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(String(option.arrival_time)) ||
      !price ||
      price.currency !== 'RON' ||
      !money(price.amount) ||
      option.price_scope !== 'requested_party' ||
      option.adults !== query.adults ||
      option.seat_availability !== null ||
      option.supplier_valid_until !== null ||
      typeof option.purchase_action_enabled !== 'boolean' ||
      !notices(option.notices) ||
      !coachSourceUrl(option.source_url, query)
    )
      return fail();
    for (const stop of [option.departure_stop, option.arrival_stop]) {
      const point = record(stop);
      if (!point || !text(point.name) || !text(point.locality) || !text(point.source_url, 2000))
        return fail();
    }
    // Compare wall-clock components only; these are not source-supplied UTC instants.
    if (
      Date.parse(`${option.arrival_date}T${option.arrival_time}:00Z`) <=
      Date.parse(`${option.departure_date}T${option.departure_time}:00Z`)
    )
      return fail();
    ids.add(option.id);
  }
  return value as CoachFareResult;
}

export function coachMoney(amount: string): string {
  if (!money(amount)) return 'Price unavailable';
  const [whole, fraction = ''] = amount.split('.');
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}.${fraction.padEnd(2, '0')}`;
}
export function coachFxReference(fx: CoachFareFx | undefined) {
  return fx?.source_currency === 'RON' ? transferFxReference(fx) : null;
}
export function coachEuroEstimate(
  option: CoachFareOption,
  fx: CoachFareFx | undefined,
): string | null {
  const estimate = option.eur_reference_estimate;
  if (
    option.price.currency !== 'RON' ||
    fx?.source_currency !== 'RON' ||
    !transferFxReady(fx) ||
    !estimate ||
    estimate.currency !== 'EUR' ||
    estimate.kind !== 'ecb_reference_estimate' ||
    estimate.rounding !== 'half_even_to_cent' ||
    !/^\d{1,7}\.\d{2}$/.test(estimate.amount)
  )
    return null;
  return `€${coachMoney(estimate.amount)}`;
}
export function coachObservedAt(value: string): string {
  return timestamp(value)
    ? new Intl.DateTimeFormat('en-GB', {
        dateStyle: 'medium',
        timeStyle: 'medium',
        timeZone: 'UTC',
      }).format(new Date(value)) + ' UTC'
    : 'Observation time unavailable';
}

/** One explicit search at a time; invalidation cannot publish a late response. */
export class CoachFareSearch {
  private active: AbortController | null = null;
  get pending() {
    return this.active !== null;
  }
  invalidate() {
    this.active?.abort();
    this.active = null;
  }
  async search(query: CoachFareRequest): Promise<CoachFareResult | null> {
    if (this.active) throw new Error('A coach fare search is already running.');
    if (!requestShape(query)) throw new Error('Invalid coach fare request.');
    const controller = new AbortController();
    this.active = controller;
    const expected = { ...query };
    try {
      const result = await request<unknown>(
        '/coaches/search',
        expected,
        AbortSignal.any([controller.signal, AbortSignal.timeout(25000)]),
      );
      if (this.active !== controller || controller.signal.aborted) return null;
      return verifyCoachFareResult(result, expected);
    } catch (cause) {
      if (this.active !== controller || controller.signal.aborted) return null;
      throw cause;
    } finally {
      if (this.active === controller) this.active = null;
    }
  }
}
