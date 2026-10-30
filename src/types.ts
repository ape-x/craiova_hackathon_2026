export type Status = "available" | "held" | "confirmed" | "refused" | "expired";
export type Member = { id: string; name: string; accessible: boolean };
export type Group = {
  id: string;
  label: string;
  size: number;
  members: Member[];
  bags: number;
  accessible: number;
  eligible: boolean;
  token: string;
  room_needs: { occupants: number; accessible: boolean }[];
};
export type Hotel = {
  id: string;
  name: string;
  area: string;
  address: string;
  distance_km: number;
  travel_minutes: number;
  status: Status;
  rooms: { id: string; capacity: number; rate: number; accessible: boolean }[];
};
export type Vehicle = {
  id: string;
  provider_id: string;
  provider: string;
  label: string;
  seats: number;
  bags: number;
  wheelchairs: number;
  price: number;
  status: Status;
  available_from: string;
  available_until: string;
};
export type Assignment = {
  group_id: string;
  hotel_id: string;
  room_ids: string[];
  trip_id: string | null;
  hotel_status: Status;
  explanation: string;
  expires_at: string;
};
export type Trip = {
  id: string;
  vehicle_id: string;
  hotel_id: string;
  group_ids: string[];
  departure_at: string;
  status: Status;
};
export type Metrics = {
  passengers: number;
  eligible: number;
  covered: number;
  confirmed: number;
  exceptions: number;
  hotel_cost: number;
  transport_cost: number;
  meal_cost: number;
  total_cost: number;
  room_count: number;
  trip_count: number;
  nights: number;
};
export type Workspace = {
  incident: {
    id: string;
    flight: string;
    airport: string;
    destination: string;
    passenger_count: number;
    start: string;
    end: string;
    budget: number;
    status: string;
  };
  groups: Group[];
  hotels: Hotel[];
  vehicles: Vehicle[];
  assignments: Assignment[];
  trips: Trip[];
  metrics: Metrics;
  exceptions: { group_id: string; passengers: number; reason: string }[];
  plan_version: number;
  approved_version: number | null;
  approval_required: boolean;
  revision: number;
  audit: {
    id: number;
    action: string;
    reason: string;
    actor: string;
    at: string;
  }[];
  last_change: string;
  meal_rate: number;
};
export type Passenger = {
  group: { id: string; label: string; size: number; members: Member[] };
  flight: string;
  end: string;
  hotel: { name: string; address: string; rooms: number } | null;
  transfer: {
    provider: string;
    label: string;
    departure_at: string;
    meeting_point: string;
  } | null;
  voucher: {
    code: string;
    value: number;
    per_person: number;
    venue: string;
    expires_at: string;
  } | null;
};
