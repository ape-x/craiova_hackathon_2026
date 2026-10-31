import { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import {
  Accessibility,
  ArrowRight,
  BedDouble,
  BusFront,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Clock3,
  Copy,
  ExternalLink,
  Plane,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { activeOffer, availableUnits, formatCost, formatTime, formatWorldTime } from './api';
import type { AppConfig, Decision, Offer, PassengerGroup, Resource, World } from './types';
import { Dot, Tag } from './ui';
import { observationInfo } from './observation';
import { AirportWeatherStatus } from './WeatherStatus';
import { airportWeatherState } from './weather';

export function ItineraryView({
  offer,
  world,
}: {
  offer: Offer;
  world?: Pick<World, 'mode' | 'epoch'>;
}) {
  return (
    <div className="itinerary">
      {offer.itinerary.legs.map((leg, index) => {
        const Icon = leg.kind === 'bus' ? BusFront : leg.kind === 'hotel' ? BedDouble : Plane;
        return (
          <div className="itinerary-leg" key={`${leg.resource_id}-${index}`}>
            <div className="itinerary-icon">
              <Icon size={16} />
            </div>
            <div>
              <strong>
                {leg.kind === 'hotel'
                  ? `Overnight · ${leg.origin}`
                  : `${leg.origin} → ${leg.destination}`}
              </strong>
              <span>
                {leg.resource_id} <b>·</b>{' '}
                {world ? formatWorldTime(world, leg.departure) : formatTime(leg.departure)}–
                {world ? formatWorldTime(world, leg.arrival) : formatTime(leg.arrival)}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ShareOffer({ group, config }: { group: PassengerGroup; config: AppConfig }) {
  const [copied, setCopied] = useState(false);
  const origin = config.public_base_url || location.origin;
  const url = `${origin.replace(/\/$/, '')}/boarding#${encodeURIComponent(group.token)}`;
  return (
    <div className="share-offer">
      <div className="qr">
        <QRCodeSVG
          value={url}
          size={92}
          bgColor="#ffffff"
          fgColor="#202321"
          level="M"
          marginSize={1}
        />
      </div>
      <div>
        <strong>Continue on your phone</strong>
        <p>Scan to review this group's offer.</p>
        <div className="share-actions">
          <button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(url);
                setCopied(true);
              } catch {
                setCopied(false);
              }
            }}
          >
            {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy link'}
          </button>
          <a href={url} target="_blank" rel="noreferrer" aria-label="Open passenger offer">
            <ExternalLink size={14} />
          </a>
        </div>
      </div>
    </div>
  );
}

export function deriveResourceImpact(world: World, resource: Resource) {
  if (resource.available) return null;
  const incident = [...world.events]
    .reverse()
    .find(
      (event) =>
        event.target_id === resource.id &&
        [
          'resource_unavailable',
          'flight_cancelled',
          'resource_capacity',
          'resource_accessibility',
        ].includes(event.type),
    );
  if (!incident) return null;
  const oldOffers = new Map(world.offers.map((offer) => [offer.id, offer]));
  const affectedIds = new Set<string>();
  for (const event of world.events) {
    if (
      event.version !== incident.version ||
      !['offer_invalidated', 'confirmation_affected'].includes(event.type)
    )
      continue;
    const oldOffer = event.target_id ? oldOffers.get(event.target_id) : undefined;
    const groupId = event.data.group_id;
    if (
      typeof groupId === 'string' &&
      oldOffer?.group_id === groupId &&
      oldOffer.itinerary.legs.some((leg) => leg.resource_id === resource.id)
    )
      affectedIds.add(groupId);
  }
  const affectedGroups = world.groups.filter((group) => affectedIds.has(group.id));
  if (!affectedGroups.length) return null;
  const routes = new Map<
    string,
    {
      ids: string[];
      kind: Resource['kind'];
      passengers: number;
      confirmed: number;
    }
  >();
  let offered = 0,
    confirmed = 0;
  for (const group of affectedGroups) {
    const replacement = activeOffer(world, group.id);
    if (!replacement || replacement.itinerary.legs.some((leg) => leg.resource_id === resource.id))
      continue;
    if (replacement.status === 'confirmed') confirmed += group.size;
    else offered += group.size;
    const ids = replacement.itinerary.legs.map((leg) => leg.resource_id);
    const key = ids.join('|');
    const route = routes.get(key) ?? {
      ids,
      kind: replacement.itinerary.legs[0]?.kind ?? resource.kind,
      passengers: 0,
      confirmed: 0,
    };
    route.passengers += group.size;
    if (replacement.status === 'confirmed') route.confirmed += group.size;
    routes.set(key, route);
  }
  return {
    incident,
    affectedGroups,
    affected: affectedGroups.reduce((sum, group) => sum + group.size, 0),
    offered,
    confirmed,
    routes: [...routes.values()].sort((a, b) => b.passengers - a.passengers),
  };
}

function ResourceImpactSummary({
  impact,
  onSelect,
}: {
  impact: NonNullable<ReturnType<typeof deriveResourceImpact>>;
  onSelect: (id: string) => void;
}) {
  const covered = impact.offered + impact.confirmed;
  return (
    <section
      className={`resource-impact ${covered < impact.affected ? 'partial' : ''}`}
      aria-label="Incident passenger impact"
    >
      <div className="resource-impact-heading">
        <Users size={20} />
        <div>
          <strong>{impact.affected}</strong>
          <span>passengers affected</span>
        </div>
        <span className="resource-impact-groups">{impact.affectedGroups.length} groups</span>
      </div>
      <div className="resource-impact-status">
        <span>
          {covered === impact.affected ? <CheckCircle2 size={14} /> : <Clock3 size={14} />}
          <strong>
            {covered} / {impact.affected}
          </strong>{' '}
          with a replacement journey
        </span>
        <small>
          {impact.confirmed} confirmed · {impact.offered} awaiting acceptance
        </small>
      </div>
      <div className="resource-impact-progress" aria-hidden="true">
        <i style={{ width: `${(impact.confirmed / impact.affected) * 100}%` }} />
        <i className="held" style={{ width: `${(impact.offered / impact.affected) * 100}%` }} />
      </div>
      {impact.routes.length > 0 ? (
        <div className="impact-routes">
          <span className="eyebrow">CURRENT REPLACEMENT ROUTES</span>
          {impact.routes.map((route) => {
            const Icon =
              route.kind === 'bus' ? BusFront : route.kind === 'hotel' ? BedDouble : Plane;
            return (
              <button
                className="impact-route"
                key={route.ids.join('|')}
                onClick={() => onSelect(route.ids[0])}
                aria-label={`Inspect replacement ${route.ids.join(' then ')}, ${route.passengers} passengers`}
              >
                <Icon size={15} />
                <span>
                  {route.ids.map((id, index) => (
                    <span key={`${id}-${index}`}>
                      {index > 0 && <ArrowRight size={10} />}
                      <b>{id}</b>
                    </span>
                  ))}
                </span>
                <small>{route.passengers} pax</small>
              </button>
            );
          })}
        </div>
      ) : (
        <p className="impact-awaiting">
          No active replacement offers. Replan to recover these journeys.
        </p>
      )}
      {covered < impact.affected && (
        <p className="impact-unresolved">
          {impact.affected - covered}{' '}
          {impact.affected - covered === 1 ? 'passenger still needs' : 'passengers still need'} an
          active offer.
        </p>
      )}
    </section>
  );
}

function ResourceFacts({
  world,
  resource,
  onScenario,
}: {
  world: World;
  resource: Resource;
  onScenario: () => void;
}) {
  return (
    <>
      <div className="destination-line">
        <strong>{resource.origin}</strong>
        <ArrowRight size={20} />
        <strong>{resource.destination}</strong>
      </div>
      <div className="fact-list">
        <span>
          Departure <b>{formatWorldTime(world, resource.departure)}</b>
        </span>
        <span>
          Arrival <b>{formatWorldTime(world, resource.arrival)}</b>
        </span>
        <span>
          {resource.capacity_scope === 'quoted_request' ? 'Quote scope' : 'Available / total'}{' '}
          {resource.kind === 'hotel' ? 'rooms' : 'seats'}{' '}
          <b>
            {resource.capacity_scope === 'quoted_request'
              ? `${resource.capacity} · requested group only`
              : `${availableUnits(world, resource.id)} / ${resource.capacity}`}
          </b>
        </span>
        <span>
          Accessibility <b>{resource.accessible ? 'Supported' : 'Not supported'}</b>
        </span>
        <span>
          {resource.quote_total_cost != null
            ? 'Group quote total'
            : `Cost per ${resource.kind === 'hotel' ? 'room' : 'person'}`}{' '}
          <b>{formatCost(resource.quote_total_cost ?? resource.cost_per_person)}</b>
        </span>
      </div>
      {world.mode === 'live' && (
        <div className="fact-list">
          <span>
            Source <b>{String(resource.provenance?.source_name ?? resource.source)}</b>
          </span>
          <span>
            Reference{' '}
            <b>
              {String(
                resource.provenance?.source_reference ??
                  resource.external_offer_id ??
                  'Recorded by operations',
              )}
            </b>
          </span>
          {resource.valid_until && (
            <span>
              Valid until{' '}
              <b>
                {new Date(resource.valid_until).toLocaleString('en-GB', {
                  timeZone: 'Europe/Bucharest',
                })}
              </b>
            </span>
          )}
          <span>
            Supplier booking <b>{resource.booking_status ?? 'Unbooked'}</b>
          </span>
        </div>
      )}
      <button className="secondary-button full" onClick={onScenario}>
        {world.mode === 'live' ? 'Manage verified data' : 'Change resource conditions'}{' '}
        <ArrowRight size={14} />
      </button>
    </>
  );
}

function DecisionDetails({ decision, world }: { decision: Decision; world: World }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const selected = decision.candidates.find((plan) => plan.id === decision.selected_plan_id);
  const historical = world.decisions.at(-1)?.id !== decision.id;
  const activeCount = world.offers.filter(
    (offer) =>
      offer.created_version === decision.applied_version &&
      ['held', 'proposed', 'accepted', 'confirmed'].includes(offer.status),
  ).length;
  const needsRefresh = !historical && (selected?.allocations.length ?? 0) > activeCount;
  return (
    <>
      <div className="decision-summary">
        <div className="decision-icon">
          <ShieldCheck size={20} />
        </div>
        <div>
          <span className="eyebrow">
            {decision.mode === 'ai' ? 'AI selected plan' : 'Solver selected plan'}
          </span>
          <h3>{selected?.strategy.label ?? 'Recovery plan'}</h3>
        </div>
        <Tag tone={needsRefresh ? 'amber' : historical ? '' : 'mint'}>
          {needsRefresh ? 'REFRESH' : historical ? 'PAST PLAN' : 'VALIDATED'}
        </Tag>
      </div>
      {needsRefresh && (
        <div className="plan-expired-note">
          <Clock3 size={15} />
          <span>
            Some offers have expired or changed. Replan to refresh the proposed resources.
          </span>
        </div>
      )}
      <p className="decision-explanation">
        {decision.mode === 'ai'
          ? decision.explanation
          : `${selected?.recovered_passengers ?? 0} passengers in this plan have a complete journey. ${selected?.allocations.length ?? 0} groups kept together. ${selected?.exceptions.length ? `${selected.exceptions.length} groups still need a solution.` : 'All affected groups covered.'}`}
      </p>
      <div className="decision-metrics">
        <div>
          <strong>
            {selected?.recovered_passengers ?? 0}
            <small>pax</small>
          </strong>
          <span>planned passengers</span>
        </div>
        <div>
          <strong>{formatCost(selected?.total_cost ?? 0)}</strong>
          <span>plan cost</span>
        </div>
        <div>
          <strong>
            {decision.latency_ms >= 1000
              ? (decision.latency_ms / 1000).toFixed(2)
              : Math.round(decision.latency_ms)}
            <small>{decision.latency_ms >= 1000 ? 's' : 'ms'}</small>
          </strong>
          <span>measured</span>
        </div>
      </div>
      <div className="inspector-section">
        <div className="section-label">
          <span>Alternatives evaluated</span>
          <span>{decision.candidates.length}</span>
        </div>
        <div className="alternatives">
          {decision.candidates.map((plan) => (
            <button
              key={plan.id}
              className={`alternative ${plan.id === decision.selected_plan_id ? 'chosen' : ''}`}
              onClick={() => setExpanded((previous) => (previous === plan.id ? null : plan.id))}
              aria-expanded={expanded === plan.id}
            >
              <span
                className={`radio-mark ${plan.id === decision.selected_plan_id ? 'checked' : ''}`}
              />
              <div>
                <strong>{plan.strategy.label}</strong>
                <span>
                  {plan.recovered_passengers} pax <b>·</b> {formatCost(plan.total_cost)}
                </span>
                {expanded === plan.id && (
                  <p>
                    {plan.allocations.length} complete itineraries. {plan.exceptions.length} groups
                    need attention.
                    <br />
                    {plan.optimality}
                  </p>
                )}
              </div>
              <ChevronDown size={13} />
            </button>
          ))}
        </div>
      </div>
      <div className="validation-list">
        <span>
          <CheckCircle2 size={15} /> Capacity & family integrity
        </span>
        <span>
          <CheckCircle2 size={15} /> Complete, connected itineraries
        </span>
        <span>
          <CheckCircle2 size={15} />{' '}
          {world.mode === 'live'
            ? 'Proposal allocations checked together'
            : 'Resources held atomically'}
        </span>
      </div>
      {(selected?.exceptions.length ?? 0) > 0 && (
        <div className="exception-note">
          <strong>{selected!.exceptions.length} groups still need a solution</strong>
          <p>
            {String(
              selected!.exceptions[0].reason ??
                'Available inventory does not satisfy all constraints.',
            )}
          </p>
        </div>
      )}
      <div className="decision-meta">
        <span>{decision.provider}</span>
        <span>
          v{decision.source_version} <ArrowRight size={10} /> v{decision.applied_version}
        </span>
      </div>
      {decision.timings_ms && (
        <details className="timing-breakdown">
          <summary>
            <Clock3 size={13} />
            Measured server timings
            <ChevronDown size={12} />
          </summary>
          <dl>
            {Object.entries(decision.timings_ms)
              .filter(([, value]) => value > 0)
              .map(([stage, value]) => (
                <div key={stage}>
                  <dt>{stage.replace(/_ms$/, '').replaceAll('_', ' ')}</dt>
                  <dd>{value.toFixed(1)} ms</dd>
                </div>
              ))}
          </dl>
          <p>Server measurements. Browser round trip is shown after each operation.</p>
        </details>
      )}
    </>
  );
}

function LatencySummary({ decisions }: { decisions: Decision[] }) {
  return (
    <div className="latency-summary">
      {(['ai', 'solver'] as const).map((mode) => {
        const values = decisions
          .filter((item) => item.mode === mode)
          .map((item) => item.latency_ms)
          .sort((a, b) => a - b);
        if (!values.length) return null;
        const middle = Math.floor(values.length / 2);
        const median =
          values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
        const p95 = values[Math.ceil(values.length * 0.95) - 1];
        return (
          <div key={mode}>
            <div className="section-label">
              <span>{mode === 'ai' ? 'Live AI' : 'Local solver'} latency</span>
              <span>{values.length} samples</span>
            </div>
            <div className="latency-statistics">
              <span>
                <strong>
                  {median.toFixed(0)}
                  <small>ms</small>
                </strong>
                median
              </span>
              <span>
                <strong>
                  {p95.toFixed(0)}
                  <small>ms</small>
                </strong>
                p95
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Inspector({
  world,
  selectedId,
  onSelect,
  onClose,
  onReplan,
  onScenario,
  busy,
  config,
  showLog,
}: {
  world: World;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
  onReplan: () => void;
  onScenario: () => void;
  busy: boolean;
  config: AppConfig;
  showLog: boolean;
}) {
  const group = world.groups.find((item) => item.id === selectedId);
  const resource = world.resources.find((item) => item.id === selectedId);
  const airport = world.airports.find((item) => item.id === selectedId);
  const observation = world.observations.find(
    (item) => String(item.id ?? item.fr24_id) === selectedId,
  );
  const provenance = observation ? observationInfo(observation) : undefined;
  const offer = group ? activeOffer(world, group.id) : undefined;
  const resourceImpact = resource ? deriveResourceImpact(world, resource) : null;
  const dependentGroups = resource
    ? world.groups.filter((item) =>
        world.offers.some(
          (current) =>
            current.group_id === item.id &&
            ['held', 'proposed', 'accepted', 'confirmed'].includes(current.status) &&
            current.itinerary.legs.some((leg) => leg.resource_id === resource.id),
        ),
      )
    : [];
  const latest = world.decisions.at(-1);
  const total = world.groups.reduce((sum, item) => sum + item.size, 0);
  const confirmed = world.groups
    .filter((item) => item.status === 'confirmed')
    .reduce((sum, item) => sum + item.size, 0);
  const offered = world.groups
    .filter((item) => item.status === 'offered')
    .reduce((sum, item) => sum + item.size, 0);
  const unavailable =
    world.resources.filter((item) => !item.available).length +
    world.airports.filter((item) =>
      world.mode === 'live'
        ? item.kind !== 'station' && item.operational_status === 'closed'
        : !item.available,
    ).length;
  const title = showLog
    ? 'Decision log'
    : observation
      ? 'Observed aircraft'
      : group
        ? 'Passenger journey'
        : resource
          ? resource.kind === 'bus'
            ? 'Transfer operations'
            : resource.kind === 'hotel'
              ? 'Accommodation'
              : 'Flight operations'
          : airport
            ? `${airport.code} operations`
            : latest
              ? 'Recovery plan'
              : 'Overview';
  return (
    <aside className="inspector" aria-label="Operations inspector">
      <div className="inspector-heading">
        <h2>{title}</h2>
        {selectedId && (
          <button className="icon-button" aria-label="Clear selection" onClick={onClose}>
            <X size={16} />
          </button>
        )}
      </div>
      <div className="inspector-scroll">
        {!showLog && group ? (
          <>
            <div className="entity-heading">
              <div className="entity-icon lilac">
                <Users size={23} />
              </div>
              <div>
                <span className="eyebrow">
                  {group.id} ·{' '}
                  {group.source === 'manifest_import'
                    ? 'imported passengers'
                    : 'synthetic test passengers'}
                </span>
                <h3>{group.name}</h3>
                <span>{group.size} people travelling together</span>
              </div>
            </div>
            <div className="destination-line">
              <strong>{group.origin}</strong>
              <ArrowRight size={20} />
              <strong>{group.destination}</strong>
            </div>
            <div className="fact-list">
              <span>
                <Clock3 size={14} /> Arrive by <b>{formatWorldTime(world, group.deadline)}</b>
              </span>
              <span>
                <Users size={14} /> Keep group together <b>Required</b>
              </span>
              {group.accessible && (
                <span>
                  <Accessibility size={14} /> Accessible transport <b>Required</b>
                </span>
              )}
              <span>
                Group budget <b>{formatCost(group.budget)}</b>
              </span>
            </div>
            {offer ? (
              <>
                <div className="offer-status">
                  <Dot tone={offer.status === 'confirmed' ? 'mint' : 'cyan'} />
                  <strong>
                    {offer.status === 'confirmed'
                      ? world.mode === 'live'
                        ? 'Supplier bookings confirmed'
                        : 'Itinerary confirmed in simulation'
                      : offer.status === 'accepted'
                        ? 'Passenger accepted · bookings pending'
                        : 'Your recovery proposal is ready'}
                  </strong>
                </div>
                <ItineraryView offer={offer} world={world} />
                <div className="offer-total">
                  <span>Complete itinerary</span>
                  <strong>{formatCost(offer.itinerary.total_cost)}</strong>
                </div>
              </>
            ) : (
              <div className="empty-detail">
                <ClipboardList size={30} />
                <h3>Finding a way forward</h3>
                <p>
                  {group.status === 'exception'
                    ? 'No complete itinerary was allocated. Add verified inventory or review the constraints.'
                    : 'Run the recovery planner to create a complete itinerary.'}
                </p>
                <button className="primary-button" disabled={busy} onClick={onReplan}>
                  Calculate recovery <ArrowRight size={15} />
                </button>
              </div>
            )}
            <ShareOffer group={group} config={config} />
          </>
        ) : null}
        {!showLog && resource ? (
          <>
            <div className="entity-heading">
              <div className={`entity-icon ${resource.available ? 'mint' : 'red'}`}>
                {resource.kind === 'bus' ? (
                  <BusFront size={23} />
                ) : resource.kind === 'flight' ? (
                  <Plane size={23} />
                ) : (
                  <BedDouble size={23} />
                )}
              </div>
              <div>
                <span className="eyebrow">
                  {world.mode === 'live'
                    ? resource.capacity_scope === 'quoted_request'
                      ? 'LIVE GROUP QUOTE'
                      : 'OPERATOR-VERIFIED RESOURCE'
                    : 'SIMULATED RESOURCE'}
                </span>
                <h3>
                  {resource.id}{' '}
                  <span className={resource.available ? 'text-mint' : 'text-red'}>
                    {resource.available
                      ? world.mode === 'simulation'
                        ? 'Available'
                        : resource.capacity_scope === 'quoted_request'
                          ? 'Unbooked quote'
                          : 'Verified'
                      : 'Unavailable'}
                  </span>
                </h3>
                <span>{resource.name}</span>
              </div>
            </div>
            {resourceImpact ? (
              <>
                <ResourceImpactSummary impact={resourceImpact} onSelect={onSelect} />
                {latest && <DecisionDetails decision={latest} world={world} />}
                <details className="resource-facts-details">
                  <summary>
                    Resource schedule & conditions
                    <ChevronDown size={14} />
                  </summary>
                  <ResourceFacts world={world} resource={resource} onScenario={onScenario} />
                </details>
              </>
            ) : (
              <ResourceFacts world={world} resource={resource} onScenario={onScenario} />
            )}
            {dependentGroups.length > 0 && (
              <div className="inspector-section">
                <div className="section-label">
                  <span>Dependent groups</span>
                </div>
                {dependentGroups.slice(0, 6).map((item) => (
                  <button
                    className="dependent-group"
                    key={item.id}
                    onClick={() => onSelect(item.id)}
                  >
                    <Users size={15} />
                    <span>{item.name}</span>
                    <span>{item.size} pax</span>
                    <ArrowRight size={13} />
                  </button>
                ))}
              </div>
            )}
          </>
        ) : null}
        {!showLog && airport ? (
          <>
            <div className="entity-heading">
              <div
                className={`entity-icon ${airport.kind === 'station' || (world.mode === 'live' && airport.operational_status === 'unknown') || airport.available ? 'cyan' : 'red'}`}
              >
                {airport.kind === 'station' ? <BusFront size={22} /> : <Plane size={22} />}
              </div>
              <div>
                <span className="eyebrow">
                  {airport.kind === 'station' ? 'COACH STATION' : 'AIRPORT'} ·{' '}
                  {world.mode === 'live' ? 'REFERENCE LOCATION' : 'SCENARIO STATUS'}
                </span>
                <h3>
                  {airport.code}{' '}
                  <span
                    className={
                      airport.kind === 'station' ||
                      (world.mode === 'live' && airport.operational_status === 'unknown')
                        ? 'muted'
                        : airport.available && !airportWeatherState(airport).restricted
                          ? 'text-mint'
                          : 'text-red'
                    }
                  >
                    {airport.kind === 'station'
                      ? 'Supplier stop'
                      : world.mode === 'live' && airport.operational_status === 'unknown'
                        ? 'Status unknown'
                        : airport.available
                          ? airportWeatherState(airport).restricted
                            ? 'Weather restricted'
                            : 'Operational'
                          : 'Unavailable'}
                  </span>
                </h3>
                <span>{airport.name}</span>
              </div>
            </div>
            <div className="fact-list">
              <span>
                Coordinates{' '}
                <b>
                  {Math.abs(airport.lat).toFixed(3)}° {airport.lat >= 0 ? 'N' : 'S'} /{' '}
                  {Math.abs(airport.lon).toFixed(3)}° {airport.lon >= 0 ? 'E' : 'W'}
                </b>
              </span>
              {world.mode === 'simulation' && airport.kind !== 'station' && (
                <span>
                  Diversion eligibility{' '}
                  <b>{airport.diversion_eligible ? 'Eligible in scenario' : 'Not eligible'}</b>
                </span>
              )}
            </div>
            {world.mode === 'simulation' && <AirportWeatherStatus airport={airport} />}
            <p className="muted small">
              {airport.kind === 'station'
                ? 'This is the supplier’s actual coach stop. Transfer to or from an airport must be verified separately.'
                : world.mode === 'live'
                  ? 'Airport geography is reference data. Operational availability requires a verified source.'
                  : 'Airport conditions belong to this simulation. Observed flight positions remain unchanged.'}
            </p>
            <button className="secondary-button full" onClick={onScenario}>
              {world.mode === 'live' ? 'Manage verified node data' : 'Change airport availability'}{' '}
              <ArrowRight size={14} />
            </button>
          </>
        ) : null}
        {!showLog && observation && provenance && (
          <>
            <div className="entity-heading">
              <div className="entity-icon cyan">
                <Plane size={23} />
              </div>
              <div>
                <span className="eyebrow">{provenance.source}</span>
                <h3>{observation.callsign ?? observation.id ?? observation.fr24_id}</h3>
                <Tag tone={provenance.fresh ? 'cyan' : 'amber'}>{provenance.label}</Tag>
              </div>
            </div>
            <div className="fact-list">
              <span>
                Source observation time{' '}
                <b>
                  {provenance.knownTime
                    ? new Date(provenance.milliseconds).toLocaleTimeString('en-GB')
                    : 'Unknown'}
                </b>
              </span>
              <span>
                Age{' '}
                <b>{provenance.ageSeconds === null ? 'Unknown' : `${provenance.ageSeconds}s`}</b>
              </span>
              <span>
                Latitude <b>{Number(observation.lat ?? observation.latitude).toFixed(4)}°</b>
              </span>
              <span>
                Longitude <b>{Number(observation.lon ?? observation.longitude).toFixed(4)}°</b>
              </span>
              <span>
                Altitude{' '}
                <b>
                  {Number.isFinite(Number(observation.altitude ?? observation.alt))
                    ? `${Number(observation.altitude ?? observation.alt).toLocaleString('en-GB')} ft`
                    : 'Unknown'}
                </b>
              </span>
              <span>
                Heading{' '}
                <b>
                  {Number.isFinite(Number(observation.heading ?? observation.track))
                    ? `${Number(observation.heading ?? observation.track)}°`
                    : 'Unknown'}
                </b>
              </span>
            </div>
            <p className="muted small">
              {provenance.label === 'SANDBOX'
                ? 'Static provider sample used for integration testing.'
                : provenance.label === 'STALE'
                  ? 'This position is old. Current aircraft location is unverified.'
                  : 'Last received aircraft position from the connected feed.'}
            </p>
            <div className="inspector-section">
              <p className="muted small">
                {world.mode === 'live'
                  ? 'Aircraft observations do not provide bookable seats. Recovery uses supplier quotes and operator-verified inventory.'
                  : 'Passenger capacity and recovery conditions belong to the simulator. Aircraft observations do not change that inventory.'}
              </p>
            </div>
          </>
        )}
        {!showLog && !group && !resource && !airport && !observation && (
          <details className="network-overview" open={!latest}>
            <summary>
              Network at a glance
              <ChevronDown size={14} />
            </summary>
            <div className={`network-kpis ${latest ? 'compact-kpis' : ''}`}>
              <div>
                <Plane size={19} />
                <span>Observed flights</span>
                <strong>{String(world.observations.length).padStart(2, '0')}</strong>
              </div>
              <div>
                <BusFront size={19} />
                <span>Transfer vehicles</span>
                <strong>
                  {String(world.resources.filter((item) => item.kind === 'bus').length).padStart(
                    2,
                    '0',
                  )}
                </strong>
              </div>
              <div>
                <Users size={19} />
                <span>
                  {world.mode === 'live' ? 'Passengers in operation' : 'Passengers in scenario'}
                </span>
                <strong>{total}</strong>
              </div>
              <div>
                <span className="incident-symbol">!</span>
                <span>Unavailable resources</span>
                <strong>{String(unavailable).padStart(2, '0')}</strong>
              </div>
            </div>
            <div className="recovery-coverage">
              <div className="section-label">
                <span>Recovery coverage</span>
                <span>
                  {confirmed + offered} / {total}
                </span>
              </div>
              <div className="coverage-track">
                <i style={{ width: `${total ? (confirmed / total) * 100 : 0}%` }} />
                <i
                  className="offered"
                  style={{ width: `${total ? (offered / total) * 100 : 0}%` }}
                />
              </div>
              <div className="coverage-key">
                <span>
                  <Dot />
                  Confirmed
                </span>
                <span>
                  <Dot tone="cyan" />
                  Offered
                </span>
              </div>
            </div>
          </details>
        )}
        {!showLog && !group && !observation && !resourceImpact && latest && (
          <DecisionDetails decision={latest} world={world} />
        )}
        {!showLog && !group && !observation && !latest && (
          <div className="awaiting-event">
            <div className="welcome-orbit">
              <Plane size={24} strokeWidth={1.4} />
            </div>
            <h3>
              {world.groups.some((group) => group.status === 'waiting')
                ? 'Let’s get everyone moving.'
                : 'Ready for the unexpected.'}
            </h3>
            <p>
              {world.groups
                .filter((group) => group.status === 'waiting')
                .reduce((n, group) => n + group.size, 0)}{' '}
              passengers awaiting recovery.
              <br />
              Find a complete journey with the resources available.
            </p>
            <button className="primary-button" disabled={busy} onClick={onReplan}>
              Calculate recovery <ArrowRight size={14} />
            </button>
            <button className="secondary-button" onClick={onScenario}>
              {world.mode === 'live' ? 'Manage operation data' : 'Adjust the scenario'}{' '}
              <ArrowRight size={14} />
            </button>
          </div>
        )}
        {showLog && (
          <>
            <LatencySummary decisions={world.decisions} />
            <div className="audit-intro">
              <ShieldCheck size={18} />
              <p>
                Every change, evaluated alternative and applied result — from the authoritative{' '}
                {world.mode === 'live' ? 'operation record' : 'simulation'}.
              </p>
            </div>
            {world.decisions.length === 0 && (
              <p className="empty-search">No decisions recorded yet.</p>
            )}
            {[...world.decisions].reverse().map((decision) => (
              <details
                className="audit-decision"
                key={decision.id}
                open={decision.id === latest?.id}
              >
                <summary>
                  <Dot tone={decision.mode === 'ai' ? 'cyan' : 'amber'} />
                  <span>
                    {decision.mode === 'ai' ? 'Live AI decision' : 'Deterministic solver'}
                    <small>
                      {new Date(decision.timestamp).toLocaleTimeString('en-GB')} · v
                      {decision.applied_version}
                    </small>
                  </span>
                  <ChevronDown size={15} />
                </summary>
                <DecisionDetails decision={decision} world={world} />
              </details>
            ))}
            <div className="section-label">
              <span>State changes</span>
              <span>{world.events.length}</span>
            </div>
            <div className="event-log">
              {[...world.events]
                .reverse()
                .slice(0, 40)
                .map((event) => (
                  <div className="event-item" key={event.id}>
                    <span className="event-marker" />
                    <div>
                      <strong>{event.description}</strong>
                      <span>
                        {new Date(event.timestamp).toLocaleTimeString('en-GB')} · v{event.version}
                      </span>
                    </div>
                  </div>
                ))}
            </div>
          </>
        )}
      </div>
      <div className="inspector-footer">
        <ShieldCheck size={13} />
        <span>Versioned state</span>
        <code>v{world.version.toString().padStart(3, '0')}</code>
      </div>
    </aside>
  );
}
