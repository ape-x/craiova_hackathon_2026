import { useState } from 'react';
import { createPortal } from 'react-dom';
import { QRCodeSVG } from 'qrcode.react';
import {
  Accessibility,
  ArrowRight,
  BedDouble,
  BusFront,
  CarFront,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Clock3,
  Copy,
  ExternalLink,
  MapPin,
  Plane,
  ShieldCheck,
  Users,
  X,
} from 'lucide-react';
import { activeOffer, availableUnits, formatCost, formatTime, formatWorldTime } from './api';
import type {
  AppConfig,
  Decision,
  MutationResult,
  Offer,
  PassengerGroup,
  Resource,
  World,
} from './types';
import { Dot, Tag } from './ui';
import { observationInfo } from './observation';
import { AirportWeatherStatus } from './WeatherStatus';
import { airportWeatherState } from './weather';
import { passengerSourceLabel } from './liveData';
import { isAirportNode, locationKindLabel } from './locationNodes';
import { flightSegments, waitingTime } from './journeyPresentation';
import { locationName, resourceTitle } from './resourceLabels';
import { airportArrivalEvidence, resourceMeetingEvidence } from './airportArrival';
import { OperatorReportNote, ResourceUnavailabilityDialog } from './OperationalReportForms';
import { unavailabilityIssue } from './operationalReports';
import { AirportArrivalDetails, SupplierMeetingInstructions } from './TransferEvidence';
import { AirportCabFareDetails, isAirportCab } from './airportCabPresentation';
import PostflightConnectionDetails from './PostflightConnectionDetails';
import { groupRecoveryEntry, type GroupRecoveryIntent } from './groupRecoveryEntry';
import { resolveRecoveryItinerary, type RecoveryItineraryIntent } from './recoveryItineraryReview';
import {
  costPresentation,
  EstimatedCostNote,
  PriceEvidenceDetails,
  privateTransfer,
} from './priceEvidence';
import {
  freshnessTime,
  liveResourceStatus,
  recordedBooking,
  resourceFreshness,
  useFreshnessClock,
  type ResourceFreshness,
  sourceInstant,
} from './resourceFreshness';

export function ItineraryView({
  offer,
  world,
}: {
  offer: Offer;
  world?: Pick<World, 'mode' | 'epoch'> & Partial<Pick<World, 'resources' | 'airports'>>;
}) {
  return (
    <div className="itinerary">
      {offer.itinerary.legs.map((leg, index) => {
        const transfer = privateTransfer(leg);
        const resource = world?.resources?.find((item) => item.id === leg.resource_id);
        const cab = isAirportCab(resource);
        const names = { airports: world?.airports ?? [] };
        const epoch = sourceInstant(world?.epoch);
        const Icon = transfer
          ? CarFront
          : leg.kind === 'bus'
            ? BusFront
            : leg.kind === 'hotel'
              ? BedDouble
              : Plane;
        const segments = world?.mode === 'live' ? flightSegments(resource, world.epoch, leg) : [];
        if (segments.length)
          return (
            <div className="sourced-flight-journey" key={`${leg.resource_id}-${index}`}>
              {segments.map((segment, position) => (
                <div key={`${segment.origin}-${position}`}>
                  {segment.waitBeforeMinutes > 0 && (
                    <div className="itinerary-connection">
                      <Clock3 size={14} aria-hidden="true" />
                      <span>
                        {waitingTime(segment.waitBeforeMinutes)} connection at {segment.origin}
                      </span>
                    </div>
                  )}
                  <div className="itinerary-leg">
                    <div className="itinerary-icon">
                      <Plane size={16} />
                    </div>
                    <div>
                      <strong>
                        {segment.origin} → {segment.destination}
                      </strong>
                      {(segment.carrier || segment.flightNumber) && (
                        <span>
                          {segment.carrier}
                          {segment.carrier && segment.flightNumber ? ' · ' : ''}
                          {segment.flightNumber ? `Flight ${segment.flightNumber}` : ''}
                        </span>
                      )}
                      <span>Departs {segment.departure.label}</span>
                      <span>Arrives {segment.arrival.label}</span>
                      {Boolean(segment.technicalStops) && (
                        <span>
                          {segment.technicalStops} technical stop(s) reported by the supplier
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          );
        return (
          <div className="itinerary-leg" key={`${leg.resource_id}-${index}`}>
            <div className="itinerary-icon">
              <Icon size={16} />
            </div>
            <div>
              <strong>
                {leg.kind === 'hotel'
                  ? `Overnight · ${leg.origin}`
                  : transfer
                    ? `${locationName(names, leg.origin)} → ${locationName(names, leg.destination)}`
                    : `${leg.origin} → ${leg.destination}`}
              </strong>
              {transfer && <span>Private transfer</span>}
              {leg.initial_position_assumption?.source === 'operator_declared' && (
                <span>
                  Initial meeting point: {leg.initial_position_assumption.label} · reported by
                  operations; no connection from an arriving flight is verified.
                </span>
              )}
              <AirportArrivalDetails assumption={leg.airport_arrival_assumption} />
              <PostflightConnectionDetails
                value={leg.postflight_connection}
                required={resource?.provenance?.direction === 'after_flight_airport_to_airport'}
                incomingResourceId={offer.itinerary.legs[index - 1]?.resource_id ?? null}
                departureSchedule={{ epoch: world?.epoch, departure: leg.departure }}
              />
              {transfer && !cab && (
                <SupplierMeetingInstructions
                  value={resourceMeetingEvidence(resource?.provenance)}
                  context={
                    airportArrivalEvidence(leg.airport_arrival_assumption)
                      ? 'hotel_to_airport'
                      : undefined
                  }
                />
              )}
              <span>
                {transfer ? (resource?.name ?? 'Private transfer') : leg.resource_id} <b>·</b>{' '}
                {transfer && epoch !== null ? (
                  `${freshnessTime(epoch + leg.departure * 60_000)} – ${freshnessTime(epoch + leg.arrival * 60_000)}`
                ) : (
                  <>
                    {world ? formatWorldTime(world, leg.departure) : formatTime(leg.departure)}–
                    {world ? formatWorldTime(world, leg.arrival) : formatTime(leg.arrival)}
                    {world?.mode === 'live' ? ' · Europe/Bucharest operations time' : ''}
                  </>
                )}
              </span>
              {cab && (
                <span>
                  Road arrival is estimated from supplier duration without departure-time traffic.
                  Airport-level endpoints; terminal and curb access are unverified.
                </span>
              )}
              {transfer && (
                <details className="record-references">
                  <summary>Transfer record</summary>
                  <span>{leg.resource_id}</span>
                </details>
              )}
              {!cab && <PriceEvidenceDetails evidence={leg.price_evidence} />}
              {cab && resource && <AirportCabFareDetails resource={resource} leg={leg} />}
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
          'operator_resource_unavailability',
        ].includes(event.type),
    );
  if (!incident) return null;
  const oldOffers = new Map(world.offers.map((offer) => [offer.id, offer]));
  const affectedIds = new Set<string>();
  if (
    incident.type === 'operator_resource_unavailability' &&
    Array.isArray(incident.data.affected_offer_ids)
  ) {
    const reportedGroups = new Set(
      Array.isArray(incident.data.affected_group_ids) ? incident.data.affected_group_ids : [],
    );
    for (const id of incident.data.affected_offer_ids) {
      if (typeof id !== 'string') continue;
      const offer = oldOffers.get(id);
      if (
        offer &&
        reportedGroups.has(offer.group_id) &&
        offer.itinerary.legs.some((leg) => leg.resource_id === resource.id)
      )
        affectedIds.add(offer.group_id);
    }
  }
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
  freshness,
  booked,
}: {
  world: World;
  resource: Resource;
  onScenario: () => void;
  freshness: ResourceFreshness | null;
  booked: boolean;
}) {
  const planningCurrent =
    !freshness || liveResourceStatus(resource, freshness, booked).planningCurrent;
  const transfer = privateTransfer(resource);
  const cab = isAirportCab(resource);
  const arrival = airportArrivalEvidence(resource.airport_arrival_assumption);
  const route = resource.provenance?.google_route as Record<string, unknown> | undefined;
  const routeReceived = route?.source === 'Google Routes' ? sourceInstant(route.fetched_at) : null;
  const operationEpoch = sourceInstant(world.epoch);
  return (
    <>
      <div className={`destination-line${transfer ? ' named-destination' : ''}`}>
        <strong>{transfer ? locationName(world, resource.origin) : resource.origin}</strong>
        <ArrowRight size={20} />
        <strong>
          {arrival
            ? `${arrival.dropoff.label} · ${arrival.airport_id}`
            : transfer
              ? locationName(world, resource.destination)
              : resource.destination}
        </strong>
      </div>
      {transfer && (
        <>
          <p className="field-hint">
            {booked
              ? 'A supplier booking is recorded separately.'
              : 'Unbooked planning quote. Check the group’s current proposal for complete-journey status.'}{' '}
            {cab
              ? 'Pickup and drop-off are airport-level references; terminal and curb access are unverified.'
              : arrival
                ? 'The airport drop-off is reported by operations.'
                : 'The pickup point is reported by operations.'}{' '}
            No vehicle position is observed.
          </p>
          <details className="record-references">
            <summary>Record identifiers</summary>
            <dl>
              <dt>Transfer record</dt>
              <dd>{resource.id}</dd>
              <dt>
                {cab ? 'Origin airport reference' : arrival ? 'Hotel property' : 'Meeting point'}
              </dt>
              <dd>{resource.origin}</dd>
              <dt>
                {cab
                  ? 'Destination airport reference'
                  : arrival
                    ? 'Airport reference'
                    : 'Hotel property'}
              </dt>
              <dd>{resource.destination}</dd>
            </dl>
          </details>
        </>
      )}
      <AirportArrivalDetails assumption={resource.airport_arrival_assumption} />
      <PostflightConnectionDetails
        value={resource.provenance?.postflight_connection}
        required={resource.provenance?.direction === 'after_flight_airport_to_airport'}
        departureSchedule={{ epoch: world.epoch, departure: resource.departure }}
      />
      {transfer && !cab && (
        <SupplierMeetingInstructions
          value={resourceMeetingEvidence(resource.provenance)}
          context={arrival ? 'hotel_to_airport' : undefined}
        />
      )}
      <div className="fact-list">
        <span>
          {resource.provenance?.direction === 'after_flight_airport_to_airport'
            ? 'Planning departure'
            : transfer
              ? 'Requested pickup'
              : 'Departure'}{' '}
          <b>
            {transfer && operationEpoch !== null
              ? freshnessTime(operationEpoch + resource.departure * 60_000)
              : formatWorldTime(world, resource.departure)}
          </b>
        </span>
        <span>
          {cab ? 'Estimated road arrival' : transfer ? 'Predicted road arrival' : 'Arrival'}{' '}
          <b>
            {transfer && operationEpoch !== null
              ? freshnessTime(operationEpoch + resource.arrival * 60_000)
              : formatWorldTime(world, resource.arrival)}
          </b>
        </span>
        {transfer && !cab && (
          <span>
            Road prediction received{' '}
            <b>
              {routeReceived !== null ? freshnessTime(routeReceived) : 'Source time unavailable'}
            </b>
          </span>
        )}
        <span>
          {resource.capacity_scope === 'private_vehicle_quote'
            ? planningCurrent
              ? 'Quoted group'
              : 'Recorded quoted group'
            : resource.capacity_scope === 'quoted_request'
              ? planningCurrent
                ? 'Quote scope'
                : 'Recorded quote scope'
              : planningCurrent
                ? 'Available / total'
                : 'Recorded capacity'}{' '}
          {resource.capacity_scope === 'private_vehicle_quote'
            ? ''
            : resource.kind === 'hotel'
              ? 'rooms'
              : 'seats'}{' '}
          <b>
            {resource.capacity_scope === 'private_vehicle_quote'
              ? `${resource.capacity} passengers · one vehicle quote`
              : resource.capacity_scope === 'quoted_request'
                ? `${resource.capacity} · requested group only`
                : planningCurrent
                  ? `${availableUnits(world, resource.id)} / ${resource.capacity}`
                  : resource.capacity}
          </b>
        </span>
        <span>
          Accessibility{' '}
          <b>{cab ? 'Unverified' : resource.accessible ? 'Supported' : 'Not supported'}</b>
        </span>
        {resource.price_evidence == null && !privateTransfer(resource) && (
          <span>
            {resource.quote_total_cost != null
              ? 'Group quote total'
              : `Cost per ${resource.kind === 'hotel' ? 'room' : 'person'}`}{' '}
            <b>{formatCost(resource.quote_total_cost ?? resource.cost_per_person)}</b>
          </span>
        )}
      </div>
      {!cab && <PriceEvidenceDetails evidence={resource.price_evidence} />}
      {cab && (
        <>
          <p className="field-hint">
            Supplier driving duration estimate without departure-time traffic. Arrival is not
            guaranteed; vehicle limits do not confirm remaining fleet.
          </p>
          <AirportCabFareDetails resource={resource} />
        </>
      )}
      {privateTransfer(resource) && !cab && resource.price_evidence == null && (
        <p className="field-hint">Price evidence unavailable. Revalidation is required.</p>
      )}
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
                  (transfer ? 'Supplier reference not provided' : 'Recorded by operations'),
              )}
            </b>
          </span>
          {freshness && (
            <span>
              Quote freshness{' '}
              <b className={freshness.state === 'current' ? 'text-cyan' : 'text-amber'}>
                {freshness.label}
              </b>
            </span>
          )}
          {freshness?.quotedAt != null && (
            <span>
              Source quoted at <b>{freshnessTime(freshness.quotedAt)}</b>
            </span>
          )}
          {freshness?.receivedAt != null && (
            <span>
              {resource.airport_arrival_assumption != null
                ? 'APL response received by app'
                : 'Received by app'}{' '}
              <b>{freshnessTime(freshness.receivedAt)}</b>
            </span>
          )}
          {freshness?.incomingQuotedAt != null && (
            <span>
              Bound incoming flight quoted at <b>{freshnessTime(freshness.incomingQuotedAt)}</b>
            </span>
          )}
          {freshness?.incomingValidUntil != null && (
            <span>
              Incoming supplier validity until <b>{freshnessTime(freshness.incomingValidUntil)}</b>
            </span>
          )}
          {resource.valid_until ? (
            <span>
              Recorded validity until{' '}
              <b>
                {freshness?.cutoff != null ? freshnessTime(freshness.cutoff) : 'Unusable timestamp'}
              </b>
            </span>
          ) : (freshness?.basis === 'quoted_at' || freshness?.basis === 'app_receipt') &&
            freshness.cutoff !== null ? (
            <span>
              {freshness.incomingResourceId
                ? 'Connection planning freshness through'
                : resource.airport_arrival_assumption != null
                  ? 'Return planning freshness through'
                  : 'Local freshness through'}{' '}
              <b>{freshnessTime(freshness.cutoff)}</b>
            </span>
          ) : null}
          <span>
            Supplier booking{' '}
            <b>
              {booked
                ? 'Confirmation recorded by operations'
                : (resource.booking_status ?? 'Unbooked')}
            </b>
          </span>
          {freshness && (
            <p className="field-hint">
              {freshness.detail}
              {booked ? ' Quote age does not cancel the recorded booking.' : ''}
            </p>
          )}
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
  const selectedCost = costPresentation(selected, world.resources);
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
            {world.mode === 'live'
              ? 'Some proposals have expired or changed. Open the recovery case and start a new provider search when fresh quotes are needed. Replanning alone does not refresh supplier quotes.'
              : 'Some offers have expired or changed. Replan to refresh the proposed resources.'}
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
          <strong>{selectedCost.amount}</strong>
          <span>{selectedCost.estimated ? 'estimated plan cost' : 'plan cost'}</span>
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
      {selected && <EstimatedCostNote value={selected} resources={world.resources} />}
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
                  {plan.recovered_passengers} pax <b>·</b>{' '}
                  {costPresentation(plan, world.resources).amount}
                  {costPresentation(plan, world.resources).estimated ? ' estimated' : ''}
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

export function GroupRecoveryAction({
  world,
  groupId,
  busy,
  onGroupRecovery,
}: {
  world: World;
  groupId: string;
  busy: boolean;
  onGroupRecovery?: (intent: GroupRecoveryIntent) => void;
}) {
  const entry = groupRecoveryEntry(world, groupId);
  if (!entry) return null;
  return (
    <div className="inspector-section" aria-label="Passenger group recovery">
      <p className="field-hint">{entry.detail}</p>
      <button
        type="button"
        className="primary-button"
        disabled={busy || !onGroupRecovery}
        onClick={() => {
          if (!busy) onGroupRecovery?.(entry.intent);
        }}
      >
        {entry.label} <ArrowRight size={15} />
      </button>
      {!onGroupRecovery && (
        <p className="field-hint">Open Recovery cases in the network panel to continue.</p>
      )}
    </div>
  );
}

export default function Inspector({
  world,
  selectedId,
  onSelect,
  onClose,
  onReplan,
  onGroupRecovery,
  itineraryReview,
  onReturnToRecoveryCase,
  reviewReturnDisabled = false,
  onScenario,
  onApplied,
  onRefresh,
  busy,
  config,
  showLog,
}: {
  world: World;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onClose: () => void;
  onReplan: () => void;
  onGroupRecovery?: (intent: GroupRecoveryIntent) => void;
  itineraryReview?: RecoveryItineraryIntent;
  onReturnToRecoveryCase?: () => void;
  reviewReturnDisabled?: boolean;
  onScenario: () => void;
  onApplied?: (result: MutationResult) => void;
  onRefresh?: () => Promise<unknown>;
  busy: boolean;
  config: AppConfig;
  showLog: boolean;
}) {
  const [reportResourceId, setReportResourceId] = useState<string | null>(null);
  const now = useFreshnessClock(world.mode === 'live');
  const group = world.groups.find((item) => item.id === selectedId);
  const resource = world.resources.find((item) => item.id === selectedId);
  const freshness =
    resource && world.mode === 'live' ? resourceFreshness(resource, now, world) : null;
  const booked = Boolean(resource && world.mode === 'live' && recordedBooking(world, resource));
  const liveStatus = resource && freshness ? liveResourceStatus(resource, freshness, booked) : null;
  const airport = world.airports.find((item) => item.id === selectedId);
  const observation = world.observations.find(
    (item) => String(item.id ?? item.fr24_id) === selectedId,
  );
  const provenance = observation ? observationInfo(observation) : undefined;
  const exactReview =
    itineraryReview && selectedId === itineraryReview.groupId
      ? resolveRecoveryItinerary(world, itineraryReview, now)
      : null;
  const offer = exactReview
    ? exactReview.kind === 'ready'
      ? exactReview.offer
      : undefined
    : group
      ? activeOffer(world, group.id)
      : undefined;
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
        ? isAirportNode(item) && item.operational_status === 'closed'
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
    <aside className="inspector" aria-label="Operations inspector" tabIndex={-1}>
      <div className="inspector-heading">
        <h2>{title}</h2>
        {selectedId && (
          <button className="icon-button" aria-label="Clear selection" onClick={onClose}>
            <X size={16} />
          </button>
        )}
      </div>
      <div className="inspector-scroll">
        {itineraryReview && onReturnToRecoveryCase && (
          <section className="inspector-section" aria-label="Recorded itinerary review">
            <p className="field-hint">Recovery result · {itineraryReview.offerId}</p>
            {exactReview?.kind === 'unavailable' && (
              <p className="case-source-warning" role="status">
                {exactReview.reason}
              </p>
            )}
            <button
              type="button"
              className="secondary-button full"
              disabled={reviewReturnDisabled}
              onClick={() => {
                if (!reviewReturnDisabled) onReturnToRecoveryCase();
              }}
            >
              Return to recovery case
            </button>
            <p className="field-hint">Your unsent search selection and inputs are retained.</p>
          </section>
        )}
        {!showLog && group ? (
          <>
            <div className="entity-heading">
              <div className="entity-icon lilac">
                <Users size={23} />
              </div>
              <div>
                <span className="eyebrow">
                  {group.id} · {passengerSourceLabel(group.source)}
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
                <div
                  className={`offer-total${costPresentation(offer.itinerary, world.resources).estimated ? ' estimated-total' : ''}`}
                >
                  <span>
                    {costPresentation(offer.itinerary, world.resources).estimated
                      ? 'Estimated itinerary total'
                      : 'Complete itinerary'}
                  </span>
                  <strong>{costPresentation(offer.itinerary, world.resources).amount}</strong>
                </div>
                <EstimatedCostNote value={offer.itinerary} resources={world.resources} />
              </>
            ) : (
              <div className="empty-detail">
                <ClipboardList size={30} />
                <h3>
                  {world.mode === 'live' ? 'No current recovery proposal' : 'Finding a way forward'}
                </h3>
                <p>
                  {world.mode === 'live'
                    ? 'Review this group’s case and requirements to find a complete journey using current supplier quotes.'
                    : group.status === 'exception'
                      ? 'No complete itinerary was allocated. Add verified inventory or review the constraints.'
                      : 'Run the recovery planner to create a complete itinerary.'}
                </p>
                {world.mode !== 'live' && (
                  <button
                    type="button"
                    className="primary-button"
                    disabled={busy}
                    onClick={onReplan}
                  >
                    Calculate recovery <ArrowRight size={15} />
                  </button>
                )}
              </div>
            )}
            {world.mode === 'live' && !itineraryReview && (
              <GroupRecoveryAction
                world={world}
                groupId={group.id}
                busy={busy}
                onGroupRecovery={onGroupRecovery}
              />
            )}
            {exactReview?.kind !== 'unavailable' && <ShareOffer group={group} config={config} />}
          </>
        ) : null}
        {!showLog && resource ? (
          <>
            <div className="entity-heading">
              <div
                className={`entity-icon ${liveStatus ? `text-${liveStatus.tone}` : resource.available ? 'mint' : 'red'}`}
              >
                {privateTransfer(resource) ? (
                  <CarFront size={23} />
                ) : resource.kind === 'bus' ? (
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
                    ? resource.capacity_scope === 'private_vehicle_quote'
                      ? 'PRIVATE-TRANSFER QUOTE RECORD'
                      : resource.capacity_scope === 'quoted_request'
                        ? 'GROUP QUOTE RECORD'
                        : 'OPERATOR-VERIFIED RESOURCE'
                    : 'SIMULATED RESOURCE'}
                </span>
                <h3>
                  {resourceTitle(resource)}{' '}
                  <span
                    className={
                      liveStatus
                        ? `text-${liveStatus.tone}`
                        : resource.available
                          ? 'text-mint'
                          : 'text-red'
                    }
                  >
                    {liveStatus
                      ? liveStatus.label
                      : resource.available
                        ? 'Available'
                        : 'Unavailable'}
                  </span>
                </h3>
                <span>{privateTransfer(resource) ? resource.source : resource.name}</span>
              </div>
            </div>
            {world.mode === 'live' && (
              <>
                <OperatorReportNote
                  provenance={resource.provenance}
                  field="unavailability_reports"
                />
                {onApplied && onRefresh && (
                  <div className="inspector-section">
                    <button
                      className="secondary-button full"
                      disabled={busy || Boolean(unavailabilityIssue(world, resource))}
                      onClick={() => setReportResourceId(resource.id)}
                    >
                      Report service unavailable
                    </button>
                    {unavailabilityIssue(world, resource) && (
                      <p className="field-hint">{unavailabilityIssue(world, resource)}</p>
                    )}
                  </div>
                )}
              </>
            )}
            {resourceImpact ? (
              <>
                <ResourceImpactSummary impact={resourceImpact} onSelect={onSelect} />
                {latest && <DecisionDetails decision={latest} world={world} />}
                <details className="resource-facts-details">
                  <summary>
                    Resource schedule & conditions
                    <ChevronDown size={14} />
                  </summary>
                  <ResourceFacts
                    world={world}
                    resource={resource}
                    onScenario={onScenario}
                    freshness={freshness}
                    booked={booked}
                  />
                </details>
              </>
            ) : (
              <ResourceFacts
                world={world}
                resource={resource}
                onScenario={onScenario}
                freshness={freshness}
                booked={booked}
              />
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
                className={`entity-icon ${!isAirportNode(airport) || (world.mode === 'live' && airport.operational_status === 'unknown') || airport.available ? 'cyan' : 'red'}`}
              >
                {airport.kind === 'meeting_point' ? (
                  <MapPin size={22} />
                ) : airport.kind === 'hotel' ? (
                  <BedDouble size={22} />
                ) : airport.kind === 'station' ? (
                  <BusFront size={22} />
                ) : (
                  <Plane size={22} />
                )}
              </div>
              <div>
                <span className="eyebrow">
                  {locationKindLabel(airport).toUpperCase()} ·{' '}
                  {world.mode === 'live' ? 'REFERENCE LOCATION' : 'SCENARIO STATUS'}
                </span>
                <h3>
                  {airport.kind === 'hotel' || airport.kind === 'meeting_point'
                    ? airport.name
                    : airport.code}{' '}
                  <span
                    className={
                      !isAirportNode(airport) ||
                      (world.mode === 'live' && airport.operational_status === 'unknown')
                        ? 'muted'
                        : airport.available && !airportWeatherState(airport).restricted
                          ? 'text-mint'
                          : 'text-red'
                    }
                  >
                    {airport.kind === 'meeting_point'
                      ? 'Operator-declared initial position'
                      : airport.kind === 'hotel'
                        ? 'Property location'
                        : airport.kind === 'station'
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
                <span>
                  {airport.kind === 'hotel' || airport.kind === 'meeting_point'
                    ? airport.code
                    : airport.name}
                </span>
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
              {world.mode === 'simulation' && isAirportNode(airport) && (
                <span>
                  Diversion eligibility{' '}
                  <b>{airport.diversion_eligible ? 'Eligible in scenario' : 'Not eligible'}</b>
                </span>
              )}
            </div>
            {world.mode === 'simulation' && isAirportNode(airport) && (
              <AirportWeatherStatus airport={airport} />
            )}
            <p className="muted small">
              {airport.kind === 'meeting_point'
                ? 'The operator reported this initial meeting point for the selected group. It is not a verified terminal or a connection from an arriving flight.'
                : airport.kind === 'hotel'
                  ? 'This is the accommodation provider’s property location. Room availability comes from a specific quoted offer; transport to the property must be verified separately.'
                  : airport.kind === 'station'
                    ? 'This is the supplier’s actual coach stop. Transfer to or from an airport must be verified separately.'
                    : world.mode === 'live'
                      ? 'Airport geography is reference data. Operational availability requires a verified source.'
                      : 'Airport conditions belong to this simulation. Observed flight positions remain unchanged.'}
            </p>
            {(world.mode === 'live' || isAirportNode(airport)) && (
              <button className="secondary-button full" onClick={onScenario}>
                {world.mode === 'live'
                  ? 'Manage verified node data'
                  : 'Change airport availability'}{' '}
                <ArrowRight size={14} />
              </button>
            )}
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
                ? 'Provider sample. This is not live operational data.'
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
      {reportResourceId &&
        onApplied &&
        onRefresh &&
        createPortal(
          <ResourceUnavailabilityDialog
            key={reportResourceId}
            world={world}
            resourceId={reportResourceId}
            onApplied={onApplied}
            onRefresh={onRefresh}
            onClose={() => setReportResourceId(null)}
          />,
          document.body,
        )}
    </aside>
  );
}
