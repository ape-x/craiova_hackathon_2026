export type ResourceKind = 'bus' | 'flight' | 'hotel';
export interface AirportWeather {
  condition: 'clear' | 'fog' | 'storm' | 'snow' | 'wind' | 'rain';
  visibility_m: number | null;
  wind_kts: number | null;
  landing_allowed: boolean;
  departure_allowed: boolean;
}
export interface Airport {
  kind?: 'airport' | 'station' | 'hotel';
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
  quote_rooms?: number | null;
  requires_revalidation?: boolean;
  external_offer_id?: string | null;
  booking_status?: string;
  capacity_scope?: 'quoted_request' | 'operator_confirmed';
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
}
export interface Leg {
  resource_id: string;
  kind: ResourceKind;
  origin: string;
  destination: string;
  departure: number;
  arrival: number;
  cost: number;
}
export interface Itinerary {
  legs: Leg[];
  arrival: number;
  total_cost: number;
}
export interface Offer {
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
  stats: Record<string, number>;
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
  | 'evaluating'
  | 'deciding'
  | 'applying'
  | 'finished';
export interface RecoveryJobInput {
  group_ids: string[];
  objective: 'balanced' | 'fastest' | 'lowest_cost';
  modes: ResourceKind[];
  max_provider_calls: number;
  instructions: string;
}
export interface RecoveryJob extends Omit<RecoveryJobInput, 'instructions'> {
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
}
