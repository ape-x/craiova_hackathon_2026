export interface RealDataQuery {
  airport_code: string;
  destination_code: string;
  checkin: string;
  checkout: string;
  adults: number;
  rooms: number;
}
export interface RealDataSection<T> {
  state: 'ready' | 'error' | 'not_configured' | 'sandbox';
  mode?: 'live' | 'sandbox';
  source: string;
  fetched_at: string | null;
  cached: boolean;
  message: string | null;
  items: T[];
}
export interface RealHotel {
  id: string;
  name: string;
  latitude: number;
  longitude: number;
  address: string | null;
  website: string | null;
  phone: string | null;
  distance_km: number;
  source_url: string;
  wheelchair: string | null;
  attributions?: { display_name: string; uri: string | null }[];
}
export interface GroundRoute {
  id: string;
  origin: string;
  destination: string;
  distance_km: number;
  duration_minutes: number;
  source_url: string;
  traffic: 'live' | 'none';
  vehicle_profile: 'driving';
}
export interface SupplierMoney {
  amount: string;
  currency: string;
}
export interface HotelPricing {
  booking_total: SupplierMoney;
  excluded_fee_totals: SupplierMoney[];
  mandatory_total: SupplierMoney | null;
  status: 'complete' | 'incomplete';
  unresolved_reasons: string[];
  payment_timing: 'unconfirmed';
}
export interface RealHotelOffer {
  id: string;
  name: string;
  total_price: string;
  currency: string;
  url: string | null;
  pricing?: HotelPricing | null;
}
export interface RealDataResult {
  airport: { code: string; name: string; latitude: number; longitude: number };
  destination: { code: string; name: string; latitude: number; longitude: number };
  criteria: RealDataQuery;
  hotels: RealDataSection<RealHotel>;
  transport: RealDataSection<GroundRoute>;
  hotel_offers: RealDataSection<RealHotelOffer>;
}
export const MOMONDO_HOTELS_URL = 'https://www.momondo.com/hotels';

export function hotelSearchDetails(query: RealDataQuery, airportName: string): string {
  return [
    `Hotels near ${airportName} (${query.airport_code})`,
    `Check-in: ${query.checkin}`,
    `Check-out: ${query.checkout}`,
    `Guests: ${query.adults} ${query.adults === 1 ? 'adult' : 'adults'}`,
    `Rooms: ${query.rooms}`,
  ].join('\n');
}

export function liveHotelOffers(section: RealDataSection<RealHotelOffer>): RealHotelOffer[] {
  return section.state === 'ready' && section.mode !== 'sandbox' ? section.items : [];
}

export function bucharestDate(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Bucharest',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now);
  return ['year', 'month', 'day']
    .map((type) => parts.find((part) => part.type === type)!.value)
    .join('-');
}
export function addCalendarDays(day: string, days: number): string {
  if (!validDate(day) || !Number.isInteger(days)) return '';
  const date = new Date(`${day}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}
function validDate(day: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return false;
  const parsed = new Date(`${day}T12:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === day;
}
export function validateRealDataQuery(
  query: RealDataQuery,
  today = bucharestDate(),
): string | null {
  if (!query.airport_code || !query.destination_code) return 'Choose both airports.';
  if (query.airport_code === query.destination_code) return 'Choose two different airports.';
  if (!validDate(query.checkin) || !validDate(query.checkout))
    return 'Enter valid check-in and check-out dates.';
  if (query.checkin < today) return 'Check-in cannot be before today.';
  if (query.checkout <= query.checkin) return 'Check-out must be after check-in.';
  if (query.checkout > addCalendarDays(query.checkin, 30))
    return 'Choose a stay of 30 nights or fewer.';
  if (query.checkout > addCalendarDays(today, 500)) return 'Choose dates within the next 500 days.';
  if (!Number.isInteger(query.adults) || query.adults < 1 || query.adults > 30)
    return 'Choose between 1 and 30 adults.';
  if (!Number.isInteger(query.rooms) || query.rooms < 1 || query.rooms > query.adults)
    return 'Choose at least one room and no more rooms than adults.';
  return null;
}
export function safeExternalUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
export function formatRetrievedAt(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Europe/Bucharest',
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  }).format(date);
}
export function formatOfferPrice(amount: string, currency: string): string {
  const value = Number(amount);
  if (!/^\d+(?:\.\d+)?$/.test(amount) || !Number.isFinite(value) || !/^[A-Z]{3}$/.test(currency))
    return 'Price unavailable';
  try {
    return new Intl.NumberFormat('en-GB', { style: 'currency', currency }).format(value);
  } catch {
    return 'Price unavailable';
  }
}
