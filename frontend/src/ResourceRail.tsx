import { useCallback, useDeferredValue, useState } from 'react';
import {
  ArrowUpRight,
  BedDouble,
  BusFront,
  ChevronRight,
  Plane,
  Search,
  SlidersHorizontal,
  Users,
  Accessibility,
  Globe2,
} from 'lucide-react';
import { activeOffer, availableUnits, formatWorldTime } from './api';
import type { World, ResourceKind, SceneMode } from './types';
import { Dot, Tag } from './ui';
import { observationInfo } from './observation';
import RealDataDialog from './RealDataDialog';
import RecoveryCases from './RecoveryCases';
import { isAirportNode, locationKindLabel } from './locationNodes';

const tabs = [
  { id: 'flight', name: 'Flights', Icon: Plane },
  { id: 'bus', name: 'Transfers', Icon: BusFront },
  { id: 'groups', name: 'Passengers', Icon: Users },
  { id: 'hotel', name: 'Hotels', Icon: BedDouble },
] as const;
export default function ResourceRail({
  world,
  selectedId,
  onSelect,
  onMode,
  onScenario,
  onLiveData,
  onRecoveryCase,
}: {
  world: World;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onMode: (mode: SceneMode) => void;
  onScenario: () => void;
  onLiveData: () => void;
  onRecoveryCase?: (id: string | null) => void;
}) {
  const [tab, setTab] = useState<ResourceKind | 'groups'>('flight');
  const [query, setQuery] = useState('');
  const [showRealData, setShowRealData] = useState(false);
  const closeRealData = useCallback(() => setShowRealData(false), []);
  const search = useDeferredValue(query.toLowerCase());
  const resources = world.resources.filter(
    (item) =>
      item.kind === tab &&
      `${item.id} ${item.name} ${item.origin} ${item.destination}`.toLowerCase().includes(search),
  );
  const groups = world.groups.filter((item) =>
    `${item.id} ${item.name} ${item.destination}`.toLowerCase().includes(search),
  );
  const counts = world.groups.reduce<Record<string, number>>(
    (acc, item) => ({ ...acc, [item.status]: (acc[item.status] ?? 0) + item.size }),
    {},
  );
  return (
    <aside className="resource-rail" aria-label="Resources">
      <div className="rail-title">
        <h2>Your network</h2>
        <button
          className="icon-button"
          title={world.mode === 'live' ? 'Manage operation data' : 'Change scenario conditions'}
          aria-label={
            world.mode === 'live' ? 'Manage operation data' : 'Change scenario conditions'
          }
          onClick={world.mode === 'live' ? onLiveData : onScenario}
        >
          <SlidersHorizontal size={16} />
        </button>
      </div>
      {world.mode === 'live' && onRecoveryCase && (
        <RecoveryCases world={world} onOpen={onRecoveryCase} />
      )}
      <div className="airports-list">
        {world.airports
          .filter(
            (airport) =>
              world.mode === 'live' ||
              !world.resources.length ||
              world.resources.some(
                (resource) => resource.origin === airport.id || resource.destination === airport.id,
              ),
          )
          .map((airport) => (
            <button
              key={airport.id}
              title={`${locationKindLabel(airport)} · ${airport.code} · ${airport.name}`}
              className={`airport-row ${selectedId === airport.id || (!selectedId && airport.code === 'CRA') ? 'selected' : ''}`}
              onClick={() => {
                onSelect(airport.id);
                onMode(airport.kind === 'hotel' || airport.code === 'CRA' ? 'airport' : 'region');
              }}
            >
              {airport.kind === 'hotel' ? (
                <BedDouble size={17} />
              ) : airport.kind === 'station' ? (
                <BusFront size={17} />
              ) : (
                <Plane
                  size={17}
                  className={
                    (world.mode !== 'live' || airport.operational_status !== 'unknown') &&
                    !airport.available
                      ? 'text-red'
                      : ''
                  }
                />
              )}
              <span>
                <strong>{airport.kind === 'hotel' ? airport.name : airport.code}</strong>
                {airport.kind !== 'hotel' && <span className="muted"> / {airport.name}</span>}
              </span>
              {airport.kind === 'hotel' ? (
                <Tag>HOTEL</Tag>
              ) : airport.kind === 'station' ? (
                <Tag>STATION</Tag>
              ) : world.mode === 'live' && airport.operational_status === 'unknown' ? (
                <Tag>UNKNOWN</Tag>
              ) : airport.available ? (
                <ChevronRight size={14} />
              ) : (
                <Dot tone="red" />
              )}
            </button>
          ))}
      </div>
      {world.mode === 'live' && (
        <button className="real-data-launch" onClick={onLiveData}>
          <Users size={16} aria-hidden="true" />
          <span>Passenger requirements &amp; services</span>
          <ArrowUpRight size={13} aria-hidden="true" />
        </button>
      )}
      <button className="real-data-launch" onClick={() => setShowRealData(true)}>
        <Globe2 size={16} aria-hidden="true" />
        <span>Real hotels &amp; routes</span>
        <ArrowUpRight size={13} aria-hidden="true" />
      </button>
      {showRealData && (
        <RealDataDialog
          airports={world.airports.filter(isAirportNode)}
          selectedId={selectedId}
          onClose={closeRealData}
        />
      )}
      <div className="resource-tabs" role="group" aria-label="Resource type">
        {tabs.map(({ id, name, Icon }) => (
          <button
            key={id}
            aria-pressed={tab === id}
            title={name}
            aria-label={name}
            className={tab === id ? 'active' : ''}
            onClick={() => {
              setTab(id);
              setQuery('');
            }}
          >
            <Icon size={16} />
            <span>{name}</span>
          </button>
        ))}
      </div>
      <div className="rail-search">
        <Search size={13} />
        <input
          aria-label="Search resources"
          placeholder="Find a resource…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <div className="rail-scroll">
        {world.mode === 'live' && (!world.groups.length || !world.resources.length) && (
          <div className="rail-secondary">
            <p className="field-hint">
              {!world.groups.length
                ? 'Import a passenger manifest or enter planning assumptions.'
                : 'Add real supplier quotes or verified transport to plan recovery.'}
            </p>
            <button className="secondary-button" onClick={onLiveData}>
              Set up operation
            </button>
          </div>
        )}
        <div className="section-label">
          <span>
            {tab === 'groups'
              ? 'Passenger groups'
              : tab === 'bus'
                ? 'Transfer vehicles'
                : tab === 'flight'
                  ? world.mode === 'live'
                    ? 'Verified flights'
                    : 'Scenario flights'
                  : world.mode === 'live'
                    ? 'Current hotel quotes'
                    : 'Hotel inventory'}
          </span>
          <span>{tab === 'groups' ? groups.length : resources.length}</span>
        </div>
        {tab === 'groups'
          ? groups.map((group) => (
              <button
                key={group.id}
                onClick={() => {
                  onSelect(group.id);
                  onMode('passenger');
                }}
                className={`resource-row group-row ${selectedId === group.id ? 'selected' : ''}`}
              >
                <Users
                  size={17}
                  className={group.status === 'confirmed' ? 'text-mint' : 'text-lilac'}
                />
                <span className="resource-name">
                  <strong>{group.name}</strong>
                  <small>
                    {group.id} · {group.size} {group.size === 1 ? 'passenger' : 'passengers'} ·{' '}
                    {group.destination}
                    {group.source === 'synthetic' ? ' · Planning assumptions' : ''}
                  </small>
                </span>
                {group.accessible ? (
                  <Accessibility size={13} className="muted" />
                ) : (
                  <Dot
                    tone={
                      group.status === 'confirmed'
                        ? 'mint'
                        : activeOffer(world, group.id)
                          ? 'cyan'
                          : 'amber'
                    }
                  />
                )}
              </button>
            ))
          : resources.map((resource) => {
              const count = availableUnits(world, resource.id);
              const Icon = tabs.find((t) => t.id === resource.kind)!.Icon;
              return (
                <button
                  key={resource.id}
                  onClick={() => {
                    onSelect(resource.id);
                    onMode(resource.kind === 'flight' ? 'region' : 'airport');
                  }}
                  className={`resource-row ${selectedId === resource.id ? 'selected' : ''}`}
                >
                  <Icon
                    size={18}
                    className={!resource.available ? 'text-red' : count ? 'text-mint' : 'muted'}
                  />
                  <span className="resource-name">
                    <strong>{resource.id}</strong>
                    <small>
                      {resource.origin} <span aria-hidden="true">→</span> {resource.destination} ·{' '}
                      {formatWorldTime(world, resource.departure)}
                    </small>
                  </span>
                  <span
                    className={!resource.available ? 'resource-value text-red' : 'resource-value'}
                  >
                    {!resource.available
                      ? 'Unavailable'
                      : resource.capacity_scope === 'quoted_request'
                        ? 'Group quote'
                        : `${count} ${resource.kind === 'hotel' ? 'rooms' : 'seats'}`}
                  </span>
                  <ChevronRight size={12} />
                </button>
              );
            })}
        {((tab === 'groups' && !groups.length) || (tab !== 'groups' && !resources.length)) && (
          <p className="empty-search">No matching resources.</p>
        )}
        <div className="rail-secondary">
          <div className="section-label">
            <span>Passenger recovery</span>
            <span>{world.groups.reduce((n, group) => n + group.size, 0)}</span>
          </div>
          {[
            { label: 'Confirmed', value: counts.confirmed ?? 0, tone: 'mint' },
            { label: 'Offer available', value: counts.offered ?? 0, tone: 'cyan' },
            {
              label: 'Awaiting solution',
              value: (counts.waiting ?? 0) + (counts.affected ?? 0) + (counts.exception ?? 0),
              tone: 'amber',
            },
          ].map((item) => (
            <div className="status-line" key={item.label}>
              <Dot tone={item.tone} />
              <span>{item.label}</span>
              <strong>{item.value}</strong>
            </div>
          ))}
        </div>
        <div className="rail-secondary">
          <div className="section-label">
            <span>Observed air traffic</span>
            <span>{world.observations.length}</span>
          </div>
          {world.observations.length ? (
            world.observations.slice(0, 5).map((flight, index) => {
              const info = observationInfo(flight);
              return (
                <button
                  className="resource-row compact"
                  key={String(flight.fr24_id ?? flight.id ?? index)}
                  onClick={() => {
                    onSelect(String(flight.fr24_id ?? flight.id));
                    onMode('region');
                  }}
                >
                  <Plane size={14} className="text-cyan" />
                  <span>{String(flight.callsign ?? flight.fr24_id ?? 'Aircraft')}</span>
                  <Tag tone={info.fresh ? 'cyan' : 'amber'}>{info.label}</Tag>
                </button>
              );
            })
          ) : (
            <div className="data-note">
              <Dot tone="slate" />
              <p>
                No live feed connected.
                <br />
                {world.mode === 'live'
                  ? 'Start the flight feed in Connections.'
                  : 'Scenario traffic is synthetic.'}
              </p>
            </div>
          )}
        </div>
      </div>
      {world.mode !== 'live' && (
        <button className="rail-footer" onClick={onScenario}>
          <SlidersHorizontal size={15} />
          <span>Scenario controls</span>
          <ArrowUpRight size={14} />
        </button>
      )}
    </aside>
  );
}
