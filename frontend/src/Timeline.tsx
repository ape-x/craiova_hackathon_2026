import { useEffect, useMemo, useRef, useState } from 'react';
import { BedDouble, BusFront, ChevronLeft, ChevronRight, Plane, Minus, Plus } from 'lucide-react';
import type { World } from './types';
import { formatWorldTime } from './api';
import { layoutTimelineLane, TIMELINE_BAR_HEIGHT, TIMELINE_TICK_HEIGHT } from './timelineLayout';
export default function Timeline({
  world,
  onSelect,
  navigation,
}: {
  world: World;
  onSelect: (id: string) => void;
  navigation?: { request: number; action: 'earlier' | 'later' | 'now' | 'zoom_in' | 'zoom_out' };
}) {
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState(0);
  const start = world.sim_time + offset,
    span = 720 / zoom;
  const consumedNavigation = useRef<number | null>(null);
  useEffect(() => {
    if (!navigation || consumedNavigation.current === navigation.request) return;
    consumedNavigation.current = navigation.request;
    if (navigation.action === 'now') setOffset(0);
    else if (navigation.action === 'earlier')
      setOffset((value) => Math.max(-world.sim_time, value - span / 2));
    else if (navigation.action === 'later') setOffset((value) => value + span / 2);
    else if (navigation.action === 'zoom_in') setZoom((value) => Math.min(4, value * 2));
    else setZoom((value) => Math.max(0.5, value / 2));
  }, [navigation, span, world.sim_time]);
  const lanes = useMemo(
    () =>
      ['flight', 'bus', 'hotel'].map((kind) => {
        const resources = world.resources
          .filter(
            (resource) =>
              resource.kind === kind &&
              resource.available &&
              world.reservations.some(
                (reservation) =>
                  reservation.resource_id === resource.id && reservation.status !== 'released',
              ),
          )
          .slice(0, 10);
        return { kind, resources, ...layoutTimelineLane(resources, start, span) };
      }),
    [world.resources, world.reservations, start, span],
  );
  return (
    <section className="timeline" aria-label="Resource schedule">
      <div className="timeline-top">
        <span className="section-label">
          Resource schedule{' '}
          <span className="muted">
            {world.mode === 'live' ? 'Live proposals' : 'Scenario time'}
          </span>
        </span>
        <div>
          <button
            className="icon-button"
            aria-label="Earlier schedule"
            onClick={() => setOffset((value) => Math.max(-world.sim_time, value - span / 2))}
          >
            <ChevronLeft size={13} />
          </button>
          <button className="timeline-now" onClick={() => setOffset(0)}>
            NOW {formatWorldTime(world, world.sim_time)}
          </button>
          <button
            className="icon-button"
            aria-label="Later schedule"
            onClick={() => setOffset((value) => value + span / 2)}
          >
            <ChevronRight size={13} />
          </button>
          <span className="divider" />
          <button
            className="icon-button"
            disabled={zoom <= 0.5}
            aria-label="Zoom out timeline"
            onClick={() => setZoom((value) => value / 2)}
          >
            <Minus size={13} />
          </button>
          <button
            className="icon-button"
            disabled={zoom >= 4}
            aria-label="Zoom in timeline"
            onClick={() => setZoom((value) => value * 2)}
          >
            <Plus size={13} />
          </button>
        </div>
      </div>
      <div className="timeline-grid">
        <div
          className="timeline-labels"
          style={{
            gridTemplateRows: `${TIMELINE_TICK_HEIGHT}px ${lanes.map((lane) => `${lane.height}px`).join(' ')}`,
          }}
        >
          <span />
          <span>
            <Plane size={12} />
            Flights
          </span>
          <span>
            <BusFront size={12} />
            Transfers
          </span>
          <span>
            <BedDouble size={12} />
            Hotels
          </span>
        </div>
        <div className="timeline-chart">
          <div className="timeline-ticks" style={{ height: TIMELINE_TICK_HEIGHT }}>
            {Array.from({ length: 9 }, (_, index) => (
              <span key={index} style={{ left: `${index * 12.5}%` }}>
                {formatWorldTime(world, start + (span * index) / 8)}
              </span>
            ))}
          </div>
          {lanes.map((lane) => (
            <div
              key={lane.kind}
              className={`timeline-lane ${lane.kind}`}
              style={{ height: lane.height }}
            >
              {lane.bars.map(({ resource, left, width, top }) => (
                <button
                  key={resource.id}
                  title={`${resource.id}: ${resource.origin} → ${resource.destination}, ${formatWorldTime(world, resource.departure)}–${formatWorldTime(world, resource.arrival)}`}
                  aria-label={`Select ${resource.id}`}
                  className="schedule-bar"
                  style={{
                    left: `${left}%`,
                    width: `${width}%`,
                    top,
                    height: TIMELINE_BAR_HEIGHT,
                  }}
                  onClick={() => onSelect(resource.id)}
                >
                  {width > 10 ? resource.id : ''}
                </button>
              ))}
              {!lane.bars.length && (
                <span className="timeline-empty">
                  {lane.resources.length ? 'No ' : 'No allocated '}
                  {lane.kind === 'bus' ? 'transfers' : lane.kind === 'hotel' ? 'rooms' : 'flights'}
                  {lane.resources.length ? ' in this time window' : ''}
                </span>
              )}
            </div>
          ))}
          {world.sim_time >= start && world.sim_time <= start + span && (
            <div
              className="time-cursor"
              style={{ left: `${((world.sim_time - start) / span) * 100}%` }}
            >
              <i />
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
