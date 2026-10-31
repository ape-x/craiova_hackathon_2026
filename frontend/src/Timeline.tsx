import { useMemo, useState } from 'react';
import { BedDouble, BusFront, ChevronLeft, ChevronRight, Plane, Minus, Plus } from 'lucide-react';
import type { World } from './types';
import { formatTime } from './api';
export default function Timeline({
  world,
  onSelect,
}: {
  world: World;
  onSelect: (id: string) => void;
}) {
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState(0);
  const start = world.sim_time + offset,
    span = 720 / zoom;
  const lanes = useMemo(
    () =>
      ['flight', 'bus', 'hotel'].map((kind) => ({
        kind,
        resources: world.resources
          .filter(
            (resource) =>
              resource.kind === kind &&
              resource.available &&
              world.reservations.some(
                (reservation) =>
                  reservation.resource_id === resource.id && reservation.status !== 'released',
              ),
          )
          .slice(0, 10),
      })),
    [world.resources, world.reservations],
  );
  return (
    <section className="timeline" aria-label="Resource schedule">
      <div className="timeline-top">
        <span className="section-label">
          Resource schedule <span className="muted">Scenario time</span>
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
            NOW {formatTime(world.sim_time)}
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
        <div className="timeline-labels">
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
          <div className="timeline-ticks">
            {Array.from({ length: 9 }, (_, index) => (
              <span key={index} style={{ left: `${index * 12.5}%` }}>
                {formatTime(start + (span * index) / 8)}
              </span>
            ))}
          </div>
          {lanes.map((lane) => (
            <div key={lane.kind} className={`timeline-lane ${lane.kind}`}>
              {lane.resources.map((resource, index) => {
                const left = Math.max(0, ((resource.departure - start) / span) * 100),
                  right = Math.min(100, ((resource.arrival - start) / span) * 100);
                if (right <= left) return null;
                return (
                  <button
                    key={resource.id}
                    title={`${resource.id}: ${resource.origin} → ${resource.destination}, ${formatTime(resource.departure)}–${formatTime(resource.arrival)}`}
                    aria-label={`Select ${resource.id}`}
                    className="schedule-bar"
                    style={{
                      left: `${left}%`,
                      width: `${right - left}%`,
                      top: `${5 + (index % 3) * 3}px`,
                      opacity: 1 - (index % 3) * 0.15,
                    }}
                    onClick={() => onSelect(resource.id)}
                  >
                    {right - left > 10 ? resource.id : ''}
                  </button>
                );
              })}
              {!lane.resources.length && (
                <span className="timeline-empty">
                  No allocated{' '}
                  {lane.kind === 'bus' ? 'transfers' : lane.kind === 'hotel' ? 'rooms' : 'flights'}
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
