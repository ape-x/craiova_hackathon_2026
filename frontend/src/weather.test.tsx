import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AirportWeatherStatus, WeatherNetworkOverlay } from './WeatherStatus';
import { airportWeatherState, weatherAirports } from './weather';
import { buildOverlays, cameraFor, COLORS } from './maps/overlays';
import type { Airport, AirportWeather, World } from './types';

const weather: AirportWeather = {
  condition: 'storm',
  visibility_m: 0,
  wind_kts: 0,
  landing_allowed: false,
  departure_allowed: true,
};
const airport = (overrides: Partial<Airport> = {}): Airport => ({
  id: 'LIS',
  code: 'LIS',
  name: 'Lisbon',
  lat: 38.77,
  lon: -9.13,
  available: true,
  diversion_eligible: true,
  ...overrides,
});
const world = (airports: Airport[], mode: 'simulation' | 'live' = 'simulation') =>
  ({
    airports,
    mode,
    resources: [],
    groups: [],
    offers: [],
    reservations: [],
    observations: [],
  }) as unknown as World;

describe('operational weather display', () => {
  it('uses explicit operation flags rather than deriving restrictions from condition names or measurements', () => {
    expect(airportWeatherState(airport({ weather }))).toMatchObject({
      landingAllowed: false,
      departureAllowed: true,
      restricted: true,
      metrics: ['0 m visibility', '0 kt wind'],
    });
    expect(
      airportWeatherState(
        airport({ weather: { ...weather, landing_allowed: true, visibility_m: 1, wind_kts: 200 } }),
      ),
    ).toMatchObject({ landingAllowed: true, departureAllowed: true, restricted: false });
  });

  it('keeps an explicit airport closure blocked after weather clears', () => {
    expect(
      airportWeatherState(
        airport({
          available: false,
          weather: { ...weather, condition: 'clear', landing_allowed: true },
        }),
      ),
    ).toMatchObject({
      landingAllowed: false,
      departureAllowed: false,
      restricted: true,
      weatherRestricted: false,
    });
  });

  it('does not manufacture weather measurements or a live feed', () => {
    expect(airportWeatherState(airport()).metrics).toEqual([]);
    expect(weatherAirports(world([airport()]))).toEqual([]);
    expect(weatherAirports(world([airport({ weather })], 'live'))).toEqual([]);
    expect(
      renderToStaticMarkup(
        <WeatherNetworkOverlay world={world([airport({ weather })], 'live')} onSelect={() => {}} />,
      ),
    ).toBe('');
    const html = renderToStaticMarkup(
      <AirportWeatherStatus
        airport={airport({ weather: { ...weather, visibility_m: null, wind_kts: null } })}
      />,
    );
    expect(html).toContain('SIMULATED WEATHER');
    expect(html).toContain('no live weather feed');
    expect(html).not.toContain('visibility');
    expect(html).not.toContain('kt wind');
  });

  it('puts restricted airports first without mutating the authoritative world array', () => {
    const clear = airport({
      id: 'FAO',
      code: 'FAO',
      weather: { ...weather, condition: 'clear', landing_allowed: true },
    });
    const storm = airport({ weather });
    const input = world([clear, storm]);
    expect(weatherAirports(input).map((a) => a.id)).toEqual(['LIS', 'FAO']);
    expect(input.airports.map((a) => a.id)).toEqual(['FAO', 'LIS']);
  });

  it('makes Google markers reflect weather and distinguishes arrivals from departures', () => {
    const marker = buildOverlays(world([airport({ weather })]), null, new Map()).markers[0];
    expect(marker.label).toContain('WEATHER RESTRICTED');
    expect(marker.color).toBe(COLORS.unavailable);
    expect(marker.title).toContain('Landings blocked; departures allowed');
    expect(marker.observed).toBe(false);
  });

  it('supports an empty reset world without inventing operational markers', () => {
    const empty = world([]);
    expect(buildOverlays(empty, null, new Map())).toEqual({ markers: [], routes: [] });
    expect(Number.isFinite(cameraFor(empty, 'region', null).center.lat)).toBe(true);
  });
});
