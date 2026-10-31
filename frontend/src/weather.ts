import type { Airport, AirportWeather, World } from './types';
import { isAirportNode } from './locationNodes';

export const WEATHER_LABELS: Record<AirportWeather['condition'], string> = {
  clear: 'Clear',
  fog: 'Fog',
  storm: 'Storm',
  snow: 'Snow',
  wind: 'Strong wind',
  rain: 'Rain',
};

/** Operational flags come from the simulator; the UI never derives aviation limits. */
export function airportWeatherState(airport: Airport) {
  const weather = airport.weather;
  const landingAllowed = airport.available && weather?.landing_allowed !== false;
  const departureAllowed = airport.available && weather?.departure_allowed !== false;
  return {
    condition: weather ? WEATHER_LABELS[weather.condition] : null,
    landingAllowed,
    departureAllowed,
    restricted: !landingAllowed || !departureAllowed,
    weatherRestricted: weather?.landing_allowed === false || weather?.departure_allowed === false,
    metrics: weather
      ? [
          weather.visibility_m !== null
            ? `${weather.visibility_m.toLocaleString('en-GB')} m visibility`
            : null,
          weather.wind_kts !== null ? `${weather.wind_kts} kt wind` : null,
        ].filter((value): value is string => value !== null)
      : [],
  };
}

export function weatherAirports(world: Pick<World, 'mode' | 'airports'>): Airport[] {
  if (world.mode !== 'simulation') return [];
  return world.airports
    .filter((airport) => isAirportNode(airport) && airport.weather != null)
    .sort((a, b) => {
      const difference =
        Number(airportWeatherState(b).restricted) - Number(airportWeatherState(a).restricted);
      return difference || a.code.localeCompare(b.code);
    });
}
