import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import {
  ArrowUpRight,
  BedDouble,
  Clock3,
  Copy,
  Loader2,
  MapPin,
  Route,
  Search,
} from 'lucide-react';
import { request } from './api';
import type { Airport, MutationResult, World } from './types';
import { isAirportNode } from './locationNodes';
import { Modal, Tag } from './ui';
import HotelOfferPrice from './HotelOfferPrice';
import TransferQuotes from './TransferQuotes';
import CoachFares from './CoachFares';
import AirportCabPanel from './AirportCabPanel';
import {
  addCalendarDays,
  bucharestDate,
  formatRetrievedAt,
  hotelSearchDetails,
  liveHotelOffers,
  MOMONDO_HOTELS_URL,
  safeExternalUrl,
  validateRealDataQuery,
} from './realData';
import type { RealDataQuery, RealDataResult, RealDataSection } from './realData';
import './realData.css';

function ExternalLink({ href, children }: { href: string | null; children: ReactNode }) {
  const safe = safeExternalUrl(href);
  return safe ? (
    <a href={safe} target="_blank" rel="noopener noreferrer">
      {children} <ArrowUpRight size={12} aria-hidden="true" />
    </a>
  ) : null;
}
function SourceStatus({ section }: { section: RealDataSection<unknown> }) {
  const retrieved = formatRetrievedAt(section.fetched_at);
  const testAccess = section.state === 'sandbox' || section.mode === 'sandbox';
  return (
    <div className="real-source-status">
      <span>{section.source}</span>
      {section.source.startsWith('Google') && (
        <strong className="real-google-credit">Google Maps</strong>
      )}
      {retrieved && (
        <span>
          {section.cached ? 'Cached' : 'Retrieved'} {retrieved}
        </span>
      )}
      <Tag tone={section.state === 'ready' && !testAccess ? 'cyan' : 'amber'}>
        {testAccess
          ? 'NON-PRODUCTION SOURCE'
          : section.state === 'ready'
            ? 'SOURCE DATA'
            : section.state === 'not_configured'
              ? 'NOT CONNECTED'
              : 'UNAVAILABLE'}
      </Tag>
    </div>
  );
}
export default function RealDataDialog({
  airports: nodes,
  selectedId,
  onClose,
  world,
  onApplied,
}: {
  airports: Airport[];
  selectedId: string | null;
  onClose: () => void;
  world?: World;
  onApplied?: (result: MutationResult) => void;
}) {
  const airports = nodes.filter(isAirportNode);
  const today = bucharestDate();
  const [query, setQuery] = useState<RealDataQuery>(() => {
    const origin =
      airports.find((airport) => airport.id === selectedId) ??
      airports.find((airport) => airport.code === 'CRA') ??
      airports[0];
    const destination =
      airports.find((airport) => airport.code === 'OTP' && airport.code !== origin?.code) ??
      airports.find((airport) => airport.code !== origin?.code);
    return {
      airport_code: origin?.code ?? '',
      destination_code: destination?.code ?? '',
      checkin: today,
      checkout: addCalendarDays(today, 1),
      adults: 2,
      rooms: 1,
    };
  });
  const [result, setResult] = useState<RealDataResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [showTransfers, setShowTransfers] = useState(false);
  const [showCoaches, setShowCoaches] = useState(false);
  const [showAirportCab, setShowAirportCab] = useState(false);
  const [airportCabPending, setAirportCabPending] = useState(false);
  const [copyState, setCopyState] = useState<'idle' | 'success' | 'error'>('idle');
  const copyGeneration = useRef(0);
  const currentRequest = useRef<AbortController | null>(null);
  const searchDetails = hotelSearchDetails(
    query,
    airports.find((airport) => airport.code === query.airport_code)?.name ?? query.airport_code,
  );
  const offers = result ? liveHotelOffers(result.hotel_offers) : [];
  useEffect(
    () => () => {
      currentRequest.current?.abort();
      currentRequest.current = null;
      copyGeneration.current += 1;
    },
    [],
  );
  function change<K extends keyof RealDataQuery>(key: K, value: RealDataQuery[K]) {
    setQuery((previous) => {
      const next = { ...previous, [key]: value };
      if (key === 'airport_code' && next.airport_code === next.destination_code)
        next.destination_code =
          airports.find((airport) => airport.code !== next.airport_code)?.code ?? '';
      return next;
    });
    setResult(null);
    setError('');
    copyGeneration.current += 1;
    setCopyState('idle');
  }
  async function copySearchDetails() {
    const generation = ++copyGeneration.current;
    try {
      await navigator.clipboard.writeText(searchDetails);
      if (copyGeneration.current === generation) setCopyState('success');
    } catch {
      if (copyGeneration.current === generation) setCopyState('error');
    }
  }
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const invalid = validateRealDataQuery(query);
    if (invalid) {
      setError(invalid);
      return;
    }
    if (
      ![query.airport_code, query.destination_code].every((code) =>
        airports.some((airport) => airport.code === code),
      )
    ) {
      setError('The operation locations changed. Choose the current airports again.');
      return;
    }
    currentRequest.current?.abort();
    const controller = new AbortController();
    currentRequest.current = controller;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const next = await request<RealDataResult>(
        '/real-data/search',
        { ...query },
        controller.signal,
      );
      if (currentRequest.current === controller && !controller.signal.aborted) setResult(next);
    } catch (cause) {
      if (currentRequest.current === controller && !controller.signal.aborted)
        setError(
          cause instanceof Error ? cause.message : 'The real-data search failed. Try again.',
        );
    } finally {
      if (currentRequest.current === controller) {
        currentRequest.current = null;
        setBusy(false);
      }
    }
  }
  return createPortal(
    <Modal
      title="Real hotels & routes"
      onClose={() => {
        if (!airportCabPending) onClose();
      }}
      wide
    >
      <div className="real-data-intro">
        <Tag tone="cyan">EXTERNAL SOURCES</Tag>
        <p>
          Find nearby hotels and a driving route between airports. Room offers appear when a booking
          provider is connected.
        </p>
      </div>
      {world?.mode === 'live' && onApplied && (
        <>
          <button
            type="button"
            className="secondary-button transfer-toggle"
            aria-expanded={showAirportCab}
            aria-controls="airport-cab-panel"
            disabled={airportCabPending}
            onClick={() => setShowAirportCab((visible) => !visible)}
          >
            {showAirportCab ? 'Hide OTP → CRA vehicle fares' : 'Get OTP → CRA vehicle fares'}
          </button>
          {showAirportCab && (
            <div id="airport-cab-panel">
              <AirportCabPanel
                world={world}
                onApplied={onApplied}
                onMutationPending={setAirportCabPending}
              />
            </div>
          )}
          {airportCabPending && (
            <p role="status" className="field-hint">
              Resolve the airport-transfer request before closing this dialog.
            </p>
          )}
        </>
      )}
      <button
        type="button"
        className="secondary-button transfer-toggle"
        aria-expanded={showTransfers}
        aria-controls="uk-transfer-panel"
        onClick={() => setShowTransfers((visible) => !visible)}
      >
        {showTransfers ? 'Hide UK transfer quotes' : 'Check UK transfer prices'}
      </button>
      {showTransfers && (
        <div id="uk-transfer-panel">
          <TransferQuotes />
        </div>
      )}
      <button
        type="button"
        className="secondary-button transfer-toggle"
        aria-expanded={showCoaches}
        aria-controls="romanian-coach-panel"
        onClick={() => setShowCoaches((visible) => !visible)}
      >
        {showCoaches ? 'Hide Romanian coach fares' : 'Check Romanian coach fares'}
      </button>
      {showCoaches && (
        <div id="romanian-coach-panel">
          <CoachFares />
        </div>
      )}
      <form
        className="real-data-form"
        onSubmit={(event) => {
          void search(event);
        }}
      >
        <fieldset disabled={busy}>
          <legend className="real-sr-only">Search real accommodation and transport data</legend>
          <label className="field">
            Hotel / origin airport
            <select
              value={query.airport_code}
              onChange={(event) => change('airport_code', event.target.value)}
              required
            >
              {airports.map((airport) => (
                <option key={airport.id} value={airport.code}>
                  {airport.code} · {airport.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            Route destination
            <select
              value={query.destination_code}
              onChange={(event) => change('destination_code', event.target.value)}
              required
            >
              <option value="" disabled>
                Choose airport
              </option>
              {airports
                .filter((airport) => airport.code !== query.airport_code)
                .map((airport) => (
                  <option key={airport.id} value={airport.code}>
                    {airport.code} · {airport.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            Check-in
            <input
              type="date"
              value={query.checkin}
              min={today}
              max={addCalendarDays(today, 499)}
              onChange={(event) => change('checkin', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Check-out
            <input
              type="date"
              value={query.checkout}
              min={query.checkin ? addCalendarDays(query.checkin, 1) : today}
              max={addCalendarDays(today, 500)}
              onChange={(event) => change('checkout', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Adults
            <input
              type="number"
              min={1}
              max={30}
              step={1}
              value={query.adults}
              onChange={(event) => change('adults', Number(event.target.value))}
              required
            />
          </label>
          <label className="field">
            Rooms
            <input
              type="number"
              min={1}
              max={query.adults}
              step={1}
              value={query.rooms}
              onChange={(event) => change('rooms', Number(event.target.value))}
              required
            />
          </label>
        </fieldset>
        <div className="real-data-submit">
          <p className="field-hint">
            A search contacts the configured sources. Results do not reserve rooms or vehicles.
          </p>
          <button className="primary-button" type="submit" disabled={busy || airports.length < 2}>
            {busy ? (
              <Loader2 className="real-data-spinner" size={15} aria-hidden="true" />
            ) : (
              <Search size={15} aria-hidden="true" />
            )}
            {busy ? 'Searching sources…' : 'Search real data'}
          </button>
        </div>
      </form>
      <section className="real-comparison" aria-label="Compare hotel prices">
        <div>
          <h3>Compare hotel prices</h3>
          <p>Opens Momondo. Enter your search details there.</p>
        </div>
        <pre className="real-comparison-details" aria-label="Search details">
          {searchDetails}
        </pre>
        <div className="real-comparison-actions">
          <ExternalLink href={MOMONDO_HOTELS_URL}>Open Momondo</ExternalLink>
          <button
            className="secondary-button"
            type="button"
            onClick={() => {
              void copySearchDetails();
            }}
          >
            <Copy size={14} aria-hidden="true" /> Copy search details
          </button>
        </div>
        <p className="real-copy-status" role="status">
          {copyState === 'success'
            ? 'Search details copied.'
            : copyState === 'error'
              ? 'Could not access the clipboard. Select and copy the details above.'
              : ''}
        </p>
      </section>
      <div aria-live="polite" aria-atomic="true">
        {busy && (
          <p className="real-data-progress" role="status">
            Looking up accommodation and route data…
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      {result && (
        <div className="real-data-results">
          <div className="real-data-result-heading">
            <h3>
              {result.airport.code} → {result.destination.code}
            </h3>
            <p>
              {result.criteria.checkin} – {result.criteria.checkout} · {result.criteria.adults}{' '}
              {result.criteria.adults === 1 ? 'adult' : 'adults'} · {result.criteria.rooms}{' '}
              {result.criteria.rooms === 1 ? 'room' : 'rooms'}
            </p>
          </div>
          <section className="real-data-section" aria-label="Driving route">
            <h3>
              <Route size={17} aria-hidden="true" /> Airport transfer route
            </h3>
            <SourceStatus section={result.transport} />
            {result.transport.message && <p className="field-hint">{result.transport.message}</p>}
            {result.transport.state === 'ready' && !result.transport.items.length && (
              <p className="field-hint">No driving route was returned for these airports.</p>
            )}
            {result.transport.items.map((route) => (
              <article className="real-route-card" key={route.id}>
                <div>
                  <strong>
                    {route.origin} → {route.destination}
                  </strong>
                  <span>
                    {route.distance_km.toLocaleString('en-GB', { maximumFractionDigits: 1 })} km by
                    road
                  </span>
                </div>
                <div>
                  <Clock3 size={15} aria-hidden="true" />
                  <strong>{Math.ceil(route.duration_minutes)} min</strong>
                  <span>
                    Estimated driving time
                    {route.traffic === 'none' ? ' · no live traffic' : ' · traffic-aware'}
                  </span>
                </div>
                <ExternalLink href={route.source_url}>View route</ExternalLink>
              </article>
            ))}
            <p className="real-data-boundary">
              Driving estimates do not confirm a coach service, fare, vehicle suitability or
              available seats.
            </p>
          </section>
          <section className="real-data-section" aria-label="Nearby hotels">
            <h3>
              <MapPin size={17} aria-hidden="true" /> Hotels near {result.airport.code}
            </h3>
            <SourceStatus section={result.hotels} />
            {result.hotels.message && <p className="field-hint">{result.hotels.message}</p>}
            {result.hotels.state === 'ready' && !result.hotels.items.length && (
              <p className="field-hint">No hotels were returned in this search area.</p>
            )}
            <div className="real-hotel-grid">
              {result.hotels.items.map((hotel) => (
                <article className="real-hotel-card" key={hotel.id}>
                  <strong>{hotel.name}</strong>
                  <p>{hotel.address ?? 'Address not provided by source'}</p>
                  <span className="real-hotel-distance">
                    {hotel.distance_km.toLocaleString('en-GB', { maximumFractionDigits: 1 })} km
                    from airport · straight line
                  </span>
                  {hotel.phone && <p className="real-hotel-phone">{hotel.phone}</p>}
                  {hotel.wheelchair && (
                    <span className="real-hotel-access">
                      Wheelchair access reported: {hotel.wheelchair}
                    </span>
                  )}
                  <div className="real-hotel-links">
                    <ExternalLink href={hotel.source_url}>Source</ExternalLink>
                    <ExternalLink href={hotel.website}>Website</ExternalLink>
                  </div>
                  {Boolean(hotel.attributions?.length) && (
                    <div className="real-hotel-credits">
                      <span>Source credits:</span>
                      {hotel.attributions?.map((credit, index) => (
                        <span key={`${credit.display_name}-${index}`}>
                          {safeExternalUrl(credit.uri) ? (
                            <ExternalLink href={credit.uri}>{credit.display_name}</ExternalLink>
                          ) : (
                            credit.display_name
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </article>
              ))}
            </div>
            <p className="real-data-boundary">
              These are place records. Room availability, rates and accessibility must be confirmed
              with the provider.
            </p>
          </section>
          <section className="real-data-section" aria-label="Hotel room offers">
            <h3>
              <BedDouble size={17} aria-hidden="true" /> Hotel room offers
            </h3>
            <SourceStatus section={result.hotel_offers} />
            {result.hotel_offers.message && (
              <p className="field-hint">{result.hotel_offers.message}</p>
            )}
            {result.hotel_offers.state === 'not_configured' && (
              <p className="field-hint">
                Connect a hotel supplier to check real room prices and availability for these dates.
              </p>
            )}
            {(result.hotel_offers.state === 'sandbox' ||
              result.hotel_offers.mode === 'sandbox') && (
              <p className="field-hint">
                This supplier connection does not provide production inventory. Live room prices and
                availability are unavailable through this connection.
              </p>
            )}
            {result.hotel_offers.state === 'ready' &&
              result.hotel_offers.mode !== 'sandbox' &&
              !offers.length && (
                <p className="field-hint">
                  No room offers were returned for these dates and guests.
                </p>
              )}
            {offers.map((offer) => (
              <article className="real-offer-card" key={offer.id}>
                <div>
                  <strong>{offer.name}</strong>
                  <span>
                    Whole stay · {result.criteria.adults}{' '}
                    {result.criteria.adults === 1 ? 'adult' : 'adults'} · {result.criteria.rooms}{' '}
                    {result.criteria.rooms === 1 ? 'room' : 'rooms'}
                  </span>
                </div>
                <HotelOfferPrice offer={offer} />
                <ExternalLink href={offer.url}>View offer</ExternalLink>
              </article>
            ))}
            {offers.length > 0 && (
              <p className="real-data-boundary">
                Quotes cover the requested stay and rooms. Mandatory fees are shown separately when
                disclosed by the supplier. Unresolved charges prevent a complete planning total.
                Availability and final booking conditions require supplier confirmation.
              </p>
            )}
          </section>
          <p className="real-data-footnote">
            Place listings and road routes are reference information, not confirmed availability.
            Supplier quotes must be imported into the operation before planning. This search does
            not change inventory or make bookings.
          </p>
          {(result.hotels.source.includes('OpenStreetMap') ||
            result.transport.source.includes('OSRM')) && (
            <p className="real-data-attribution">
              Map data ©{' '}
              <ExternalLink href="https://www.openstreetmap.org/copyright">
                OpenStreetMap contributors
              </ExternalLink>
            </p>
          )}
          {(result.hotels.source.includes('Google') ||
            result.transport.source.includes('Google')) && (
            <p className="real-data-attribution">Google Maps</p>
          )}
        </div>
      )}
    </Modal>,
    document.body,
  );
}
