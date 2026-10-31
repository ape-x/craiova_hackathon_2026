import {
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  PlaneLanding,
  PlaneTakeoff,
  Sun,
  Wind,
} from 'lucide-react';
import type { Airport, World } from './types';
import { airportWeatherState, weatherAirports } from './weather';
import './weather.css';

const icons = {
  clear: Sun,
  fog: CloudFog,
  storm: CloudLightning,
  snow: CloudSnow,
  wind: Wind,
  rain: CloudRain,
};

export function AirportWeatherStatus({ airport }: { airport: Airport }) {
  if (!airport.weather) return null;
  const state = airportWeatherState(airport);
  const Icon = icons[airport.weather.condition];
  return (
    <section
      className={`airport-weather-card ${state.restricted ? 'restricted' : ''}`}
      aria-label={`${airport.code} simulated weather`}
    >
      <div className="weather-card-heading">
        <Icon size={25} aria-hidden="true" />
        <div>
          <span>SIMULATED WEATHER</span>
          <strong>{state.condition}</strong>
        </div>
      </div>
      {state.metrics.length > 0 && <p className="weather-metrics">{state.metrics.join(' · ')}</p>}
      <div className="weather-operations">
        <span className={state.landingAllowed ? 'allowed' : 'blocked'}>
          <PlaneLanding size={14} aria-hidden="true" /> Landings{' '}
          <b>{state.landingAllowed ? 'Allowed' : 'Blocked'}</b>
        </span>
        <span className={state.departureAllowed ? 'allowed' : 'blocked'}>
          <PlaneTakeoff size={14} aria-hidden="true" /> Departures{' '}
          <b>{state.departureAllowed ? 'Allowed' : 'Blocked'}</b>
        </span>
      </div>
      <small>Scenario constraints · no live weather feed</small>
    </section>
  );
}

export function WeatherNetworkOverlay({
  world,
  onSelect,
}: {
  world: World;
  onSelect: (id: string) => void;
}) {
  const airports = weatherAirports(world);
  if (!airports.length) return null;
  return (
    <section className="weather-network" aria-label="Simulated airport weather">
      <header>
        <span className="weather-indicator" /> SIMULATED WEATHER{' '}
        <span>
          {airports.length} {airports.length === 1 ? 'AIRPORT' : 'AIRPORTS'}
        </span>
      </header>
      <div className="weather-network-list">
        {airports.map((airport) => {
          const state = airportWeatherState(airport);
          const Icon = icons[airport.weather!.condition];
          return (
            <button
              key={airport.id}
              className={state.restricted ? 'restricted' : ''}
              onClick={() => onSelect(airport.id)}
              aria-label={`Inspect ${airport.code} weather: ${state.condition}; landings ${state.landingAllowed ? 'allowed' : 'blocked'}, departures ${state.departureAllowed ? 'allowed' : 'blocked'}`}
            >
              <Icon size={22} aria-hidden="true" />
              <div>
                <strong>
                  {airport.code} <span>{state.condition}</span>
                </strong>
                <small>
                  {!state.landingAllowed && !state.departureAllowed
                    ? 'Landings & departures blocked'
                    : !state.landingAllowed
                      ? 'Landings blocked'
                      : !state.departureAllowed
                        ? 'Departures blocked'
                        : 'Operations allowed'}
                </small>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
