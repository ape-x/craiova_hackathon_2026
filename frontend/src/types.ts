import type { TransferFxReference } from './transferQuoteData';

export type ResourceKind = 'bus' | 'flight' | 'hotel';
export interface PriceEvidence {
  kind: 'apl_ecb_reference';
  original: { amount: string; currency: 'GBP' };
  estimated_eur: { amount: string; currency: 'EUR' };
  price_scope: 'per_vehicle';
  fx_reference: TransferFxReference;
  policy_id: 'ecb_reference_v1';
  settlement_guaranteed: false;
  revalidation_required: true;
}
export interface QuoteFreshness {
  basis: 'app_receipt';
  received_at: string;
  max_age_seconds: 120;
}
export interface InitialPositionAssumption {
  source: 'operator_declared';
  confirmed: true;
  group_id: string;
  group_fingerprint: string;
  scenario_id: string;
  epoch: string;
  airport_context_id: string;
  point_id: string;
  label: string;
  coordinates: { latitude: number; longitude: number };
  ready_at: number;
}
export interface CostEvidence {
  kind: 'includes_ecb_reference';
  total_eur: string;
  estimated_eur: string;
  original_gbp: string;
  policy_id: 'ecb_reference_v1';
  settlement_guaranteed: false;
  revalidation_required: true;
}
export interface AirportArrivalAssumption {
  source: 'operator_declared';
  confirmed: true;
  group_id: string;
  group_fingerprint: string;
  scenario_id: string;
  epoch: string;
  airport_id: string;
  onward_resource_id: string;
  onward_flight_fingerprint: string;
  onward_departure_at: string;
  dropoff: { label: string; coordinates: { latitude: number; longitude: number } };
  processing_minutes: number;
  departure_terminal: null;
}
export interface AirportWeather {
  condition: 'clear' | 'fog' | 'storm' | 'snow' | 'wind' | 'rain';
  visibility_m: number | null;
  wind_kts: number | null;
  landing_allowed: boolean;
  departure_allowed: boolean;
}
export interface Airport {
  kind?: 'airport' | 'station' | 'hotel' | 'meeting_point';
  id: string;
  code: string;
  name: string;
  lat: number;
  lon: number;
  available: boolean;
  diversion_eligible: boolean;
  operational_status?: 'unknown' | 'open' | 'closed';
  weather?: AirportWeather | null;
}
export interface Resource {
  id: string;
  kind: ResourceKind;
  name: string;
  origin: string;
  destination: string;
  departure: number;
  arrival: number;
  capacity: number;
  accessible: boolean;
  available: boolean;
  cost_per_person: number;
  room_capacity: number;
  vehicle_id: string | null;
  status: string;
  source: string;
  provenance?: Record<string, unknown>;
  valid_until?: string | null;
  eligible_group_ids?: string[];
  quote_total_cost?: number | null;
  budget_total_cost?: number | null;
  price_evidence?: PriceEvidence | null;
  quote_freshness?: QuoteFreshness | null;
  transport_mode?: 'private_transfer' | null;
  initial_position_assumption?: InitialPositionAssumption | null;
  airport_arrival_assumption?: AirportArrivalAssumption | null;
  quote_rooms?: number | null;
  requires_revalidation?: boolean;
  external_offer_id?: string | null;
  booking_status?: string;
  capacity_scope?: 'quoted_request' | 'private_vehicle_quote' | 'operator_confirmed';
}
export interface InventoryResourceRecord {
  scenario_id: string;
  resource: Resource;
  locations: Airport[];
  archived: boolean;
  archived_at: string | null;
  archived_version: number | null;
  archive_reason: string | null;
}
export interface PassengerGroup {
  incident_id?: string | null;
  id: string;
  name: string;
  size: number;
  members: string[];
  origin: string;
  destination: string;
  deadline: number;
  ready_at?: number;
  accessible: boolean;
  budget: number;
  original_flight_id: string;
  status: 'scheduled' | 'waiting' | 'offered' | 'confirmed' | 'affected' | 'exception';
  token: string;
  priority: number;
  source?: string;
  adult_count?: number | null;
  provenance?: Record<string, unknown>;
}
export interface Leg {
  resource_id: string;
  kind: ResourceKind;
  origin: string;
  destination: string;
  departure: number;
  arrival: number;
  cost: number;
  price_evidence?: PriceEvidence | null;
  transport_mode?: 'private_transfer' | null;
  initial_position_assumption?: InitialPositionAssumption | null;
  airport_arrival_assumption?: AirportArrivalAssumption | null;
}
export interface Itinerary {
  legs: Leg[];
  arrival: number;
  total_cost: number;
  cost_evidence?: CostEvidence | null;
}
export interface Offer {
  external_bookings?: Record<string, unknown>[];
  id: string;
  group_id: string;
  itinerary: Itinerary;
  status:
    | 'held'
    | 'proposed'
    | 'accepted'
    | 'confirmed'
    | 'expired'
    | 'invalidated'
    | 'superseded'
    | 'declined';
  expires_at: string;
  created_version: number;
  confirmed_at: string | null;
  reason: string | null;
}
export interface Reservation {
  id: string;
  offer_id: string;
  group_id: string;
  resource_id: string;
  quantity: number;
  status: 'held' | 'proposed' | 'accepted' | 'confirmed' | 'released';
  expires_at: string;
}
export interface AuditEvent {
  id: string;
  type: string;
  timestamp: string;
  version: number;
  target_id: string | null;
  description: string;
  data: Record<string, unknown>;
}
export interface Strategy {
  id: string;
  label: string;
  objective: 'balanced' | 'fastest' | 'lowest_cost';
  preferred_airport: string | null;
  weights: Record<string, number>;
}
export interface Plan {
  id: string;
  strategy: Strategy;
  allocations: { group_id: string; itinerary: Itinerary }[];
  exceptions: { group_id?: string; reason?: string; [key: string]: unknown }[];
  recovered_passengers: number;
  total_cost: number;
  cost_evidence?: CostEvidence | null;
  average_arrival: number;
  validation: Record<string, unknown>;
  solver: string;
  optimality: string;
  latency_ms: number;
}
export interface Decision {
  id: string;
  timestamp: string;
  source_version: number;
  applied_version: number;
  mode: 'ai' | 'solver';
  provider: string;
  request: string;
  explanation: string;
  selected_plan_id: string;
  candidates: Plan[];
  latency_ms: number;
  timings_ms?: Record<string, number>;
  focus_id: string | null;
}
export interface Observation {
  id?: string;
  fr24_id?: string;
  callsign?: string;
  lat?: number;
  lon?: number;
  latitude?: number;
  longitude?: number;
  altitude?: number;
  alt?: number;
  heading?: number;
  track?: number;
  timestamp?: string;
  observed_at?: string;
  source?: string;
  [key: string]: unknown;
}
export interface WorldStats {
  [key: string]: number | boolean | CostEvidence | null | undefined;
  committed_cost_evidence?: CostEvidence | null;
  confirmed_cost_evidence?: CostEvidence | null;
  budget_remaining_is_estimate?: boolean;
}
export interface World {
  incidents?: RecoveryIncident[];
  scenario_id: string;
  version: number;
  epoch: string;
  sim_time: number;
  mode: 'simulation' | 'live';
  currency: string;
  budget: number;
  airports: Airport[];
  resources: Resource[];
  groups: PassengerGroup[];
  offers: Offer[];
  reservations: Reservation[];
  events: AuditEvent[];
  decisions: Decision[];
  observations: Observation[];
  stats: WorldStats;
  provider_status: Record<string, unknown>;
}
export interface FlightAirportReference {
  iata: string | null;
  icao: string | null;
  catalog_code: string | null;
}
export interface FlightSelection {
  selection_id: string;
  fr24_id: string;
  flight_number: string | null;
  callsign: string | null;
  registration: string | null;
  aircraft_type: string | null;
  origin: FlightAirportReference | null;
  intended_destination: FlightAirportReference | null;
  actual_destination: FlightAirportReference | null;
  first_seen: string | null;
  last_seen: string | null;
  takeoff_at: string | null;
  landed_at: string | null;
  flight_ended: boolean | null;
  status: 'tracking' | 'ended' | 'unknown';
  freshness: 'current' | 'stale' | 'historical' | 'unknown';
}
export interface FlightLookup {
  state: 'ready' | 'no_match' | 'not_configured' | 'error';
  lookup_id: string | null;
  queried_at: string;
  expires_at: string | null;
  source: 'Flightradar24';
  cached: boolean;
  matches: FlightSelection[];
  message: string | null;
}
export interface RecoveryIncident {
  id: string;
  created_at: string;
  source: 'operator_report';
  kind: 'destination_unavailable' | 'flight_disruption';
  description: string;
  requested_flight_number: string;
  date_utc: string;
  flight_ref: { lookup_id: string; selection_id: string };
  unavailable_airport_code: string | null;
  group_ids: string[];
  status: 'needs_passenger_data' | 'open';
  flight: FlightSelection | null;
  flight_context_state: 'available' | 'expired';
}
export interface IncidentInput {
  lookup_id: string;
  selection_id: string;
  kind: RecoveryIncident['kind'];
  description: string;
  unavailable_airport_code?: string;
  affected_group_ids: string[];
}
export interface IncidentResult extends MutationResult {
  incident_id: string;
}
export type RecoveryJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'cancelled'
  | 'needs_review'
  | 'unknown_outcome';
export type RecoveryJobStage =
  | 'queued'
  | 'validating'
  | 'planning_queries'
  | 'searching_flights'
  | 'searching_hotels'
  | 'locating_hotel'
  | 'quoting_initial_airport_to_hotel'
  | 'routing_initial_airport_to_hotel'
  | 'reference_fx'
  | 'quoting_hotel_to_airport'
  | 'routing_hotel_to_airport'
  | 'evaluating'
  | 'deciding'
  | 'applying'
  | 'finished';
export interface HotelRecoveryInput {
  checkin_at: string;
  checkout_at: string;
  rooms: number;
  minimum_rest_minutes: number;
  initial_point: { label: string; latitude: number; longitude: number; confirmed: true };
  pickup_at: string;
  suitcases: number;
  onward_airport_code: string;
  airport_dropoff: { label: string; latitude: number; longitude: number; confirmed: true };
  airport_processing_minutes: number;
}
export interface RecoveryJobInput {
  group_ids: string[];
  objective: 'balanced' | 'fastest' | 'lowest_cost';
  modes: ResourceKind[];
  max_provider_calls: number;
  instructions: string;
  hotel_recovery?: HotelRecoveryInput;
}
export interface RecoveryJob extends Omit<RecoveryJobInput, 'instructions' | 'hotel_recovery'> {
  id: string;
  incident_id: string;
  status: RecoveryJobStatus;
  stage: RecoveryJobStage;
  message: string;
  created_at: string;
  updated_at: string;
  cancel_requested: boolean;
  source_version: number;
  applied_version: number | null;
  calls_dispatched: number;
  steps: {
    id: string;
    stage: RecoveryJobStage;
    provider: string | null;
    status: 'dispatched' | 'succeeded' | 'failed' | 'unknown' | 'skipped';
    started_at: string | null;
    finished_at: string | null;
    group_ids: string[];
    message: string;
    quote_count: number;
  }[];
  gaps: { code: string; message: string; group_ids: string[] }[];
  result: null | {
    decision_id: string;
    plan_id: string;
    recovered_passengers: number;
    exception_passengers: number;
    offer_ids: string[];
    resource_ids: string[];
    source_counts: Record<string, number>;
    hotel_recovery?: {
      hotel_id: string;
      hotel_node_id: string;
      hotel_shortlist_limit: 1;
      flight_query_date: string;
      return_pickup_at: string;
      return_pickup_basis: 'checkout_plus_10_minute_app_allowance';
      required_provider_calls: 10;
    };
    waits?: {
      group_id: string;
      location_id: string;
      from_at: string;
      to_at: string;
      duration_minutes: number;
      timezone: string | null;
      overnight: boolean | null;
      accommodation_included: false;
    }[];
  };
}
export interface RecoveryJobResponse {
  job: RecoveryJob;
  replayed?: boolean;
}
export interface AppConfig {
  ai_configured?: boolean;
  voice_configured?: boolean;
  fr24_configured?: boolean;
  google_maps_api_key?: string;
  public_base_url?: string;
  auth_required?: boolean;
  environment?: string;
  providers?: Record<string, unknown>;
  [key: string]: unknown;
}
export type SceneMode = 'airport' | 'region' | 'passenger';
export type EventType =
  | 'resource_unavailable'
  | 'flight_cancelled'
  | 'airport_closed'
  | 'resource_capacity'
  | 'budget_changed'
  | 'group_deadline'
  | 'group_accessibility'
  | 'resource_accessibility'
  | 'flight_diverted'
  | 'group_budget'
  | 'resource_restored';
export interface ScenarioChange {
  type: EventType;
  target_id: string | null;
  value: unknown;
}
export type OperationalReportReceipt = {
  id: string;
  source: 'operator_report';
  received_at: string;
  description: string;
  affected_group_ids: string[];
  affected_offer_ids: string[];
} & (
  | {
      type: 'arrival_report';
      incident_id: string;
      group_ids: string[];
      airport_code: string;
      ready_at: string;
      readiness_basis?: {
        kind: 'command_relative_minutes';
        reference: string;
        command_received_at: string;
        anchor_at: string;
        rounding: 'ceil_minute';
        minutes: number;
      };
    }
  | { type: 'resource_unavailability'; resource_id: string; previous_available: boolean }
);
export interface RecoveryBlockedReceipt {
  code:
    | 'insufficient_call_allowance'
    | 'hotel_setup_required'
    | 'recovery_prerequisite_failed'
    | 'recovery_queue_unavailable';
  explanation: string;
}
export interface MutationResult {
  world: World;
  replayed?: boolean;
  decision?: Decision;
  explanation?: string;
  focus_id?: string | null;
  applied?: boolean;
  mode?: 'ai' | 'solver';
  incident_id?: string;
  recovery_job?: RecoveryJob | null;
  command_interpretation_calls?: number;
  recovery_total_call_limit?: number;
  report?: OperationalReportReceipt;
  recovery_blocked?: RecoveryBlockedReceipt;
}
