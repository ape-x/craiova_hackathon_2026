"""Offline native-EUR transfer evidence; no supplier, model or routing calls."""
import copy
import json
from datetime import datetime, timedelta, timezone
from decimal import localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.airport_cab_evidence as evidence
import app.pricing as pricing
import app.service as service_module
import app.solver as solver
from app.airport_catalog import lookup_airport
from app.models import Airport, Allocation, Offer, PassengerGroup, Reservation, Resource, Strategy, World
from app.live_service import ExternalConfirmation, LiveOperations
from app.providers.bucharest_cab import AirportCabQuoteRequest, normalize_quote
from app.service import RecoveryService
from app.store import DomainError


NOW = datetime(2030, 1, 4, tzinfo=timezone.utc)
PICKUP = datetime(2030, 1, 4, 10, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    clock = [NOW]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    for module in (evidence, pricing, solver):
        monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setattr(service_module, "utcnow", lambda: clock[0])
    return clock


def source_quote(*, passengers=2, suitcases=2, handbags=2):
    payload = json.loads((Path(__file__).parent / "fixtures/bucharest_cab_otp_cra.json").read_text())
    payload.update(datetime_param="2030-01-04 12:00:00", datetime="2030-01-04 10:00:00")
    request = AirportCabQuoteRequest(origin_airport="OTP", destination_airport="CRA", pickup_at=PICKUP,
        passengers=passengers, suitcases=suitcases, handbags=handbags)
    return normalize_quote(payload, request, received_at=NOW)


def airport(code):
    row = lookup_airport(code)
    return Airport(id=code, code=code, name=row["name"], lat=row["lat"], lon=row["lon"], operational_status="open")


def cab(world, group, *, identity="CAB-ONE", option_index=0):
    quote = source_quote(passengers=group.size)
    option = quote["options"][option_index]
    departure, arrival = evidence.airport_cab_timing(world, quote)
    return Resource(id=identity, kind="bus", transport_mode="private_transfer", name=option["car_type"],
        origin="OTP", destination="CRA", departure=departure, arrival=arrival, capacity=group.size,
        accessible=False, available=True, cost_per_person=0, source=evidence.SOURCE, status="quoted",
        quote_total_cost=float(option["price"]["amount"]), eligible_group_ids=[group.id], requires_revalidation=True,
        capacity_scope="private_vehicle_quote",
        quote_freshness=pricing.AplQuoteFreshness(basis="app_receipt", received_at=quote["received_at"], max_age_seconds=120),
        provenance={"airport_cab_quote":quote, "airport_cab_option_id":option["id"],
            "airport_cab_context":evidence.airport_cab_context(world, group),
            "direction":"initial_airport_to_airport", "estimated_arrival":True, "supplier_booked":False,
            "quoted_at":None, "quote_id":"receipt-"+identity, "quote_sequence":1})


def state(*, destination="CRA"):
    world = World(scenario_id="cab-evidence", mode="live", epoch=(NOW-timedelta(seconds=30)).isoformat(),
        sim_time=1, budget=1000, airports=[airport(code) for code in ("OTP", "CRA", "LHR")],
        groups=[PassengerGroup(id="G1", name="Reported passengers", size=2, adult_count=2,
            origin="OTP", destination=destination, ready_at=600, deadline=1200, budget=500,
            original_flight_id="already-arrived", token="offline", source="operator_input")], resources=[])
    world.resources = [cab(world, world.groups[0])]
    return world


def quote(world):
    return world.resources[0].provenance["airport_cab_quote"]


def selected(world):
    return quote(world)["options"][0]


def test_native_eur_initial_journey_preserves_airport_level_scope_and_cost_once():
    world = state()
    original_group = world.groups[0].model_dump()
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2
    assert plan.total_cost == 188 and plan.cost_evidence is None
    itinerary = plan.allocations[0].itinerary
    assert itinerary.total_cost == 188 and itinerary.cost_evidence is None
    leg = itinerary.legs[0]
    assert (leg.origin, leg.destination, leg.departure, leg.arrival) == ("OTP", "CRA", 601, 748)
    assert leg.cost == 188 and leg.transport_mode == "private_transfer"
    assert leg.price_evidence is leg.initial_position_assumption is leg.airport_arrival_assumption is None
    resource = world.resources[0]
    assert resource.capacity == 2 and selected(world)["max_passengers"] == 3
    assert selected(world)["remaining_fleet"] is None and resource.vehicle_id is None
    assert world.groups[0].model_dump() == original_group
    assert resource.budget_total_cost is resource.valid_until is None


@pytest.mark.parametrize("scope", ["group", "world"])
@pytest.mark.parametrize("amount, recovered", [(187.99, 0), (188, 2)])
def test_exact_native_eur_vehicle_price_controls_budgets(scope, amount, recovered):
    world = state()
    if scope == "group":
        world.groups[0].budget = amount
        world.resources[0].provenance["airport_cab_context"] = evidence.airport_cab_context(world, world.groups[0])
    else:
        world.budget = amount
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == recovered


@pytest.mark.parametrize("change", [
    lambda w: setattr(w.resources[0], "quote_total_cost", 187),
    lambda w: setattr(w.resources[0], "budget_total_cost", 188),
    lambda w: setattr(w.resources[0], "cost_per_person", 94),
    lambda w: setattr(w.resources[0], "capacity", 3),
    lambda w: setattr(w.resources[0], "eligible_group_ids", []),
    lambda w: setattr(w.resources[0], "quote_freshness", None),
    lambda w: setattr(w.resources[0], "valid_until", (NOW+timedelta(hours=1)).isoformat()),
    lambda w: w.resources[0].provenance.update(quoted_at=NOW.isoformat()),
    lambda w: setattr(w.resources[0], "vehicle_id", "invented-car"),
    lambda w: setattr(w.resources[0], "external_offer_id", "invented-hold"),
    lambda w: setattr(w.resources[0], "status", "confirmed"),
    lambda w: setattr(w.resources[0], "booking_status", "confirmed"),
    lambda w: w.resources[0].provenance.update(supplier_booked=True),
    lambda w: w.resources[0].provenance.update(estimated_arrival=False),
    lambda w: w.resources[0].provenance.update(direction="after_flight"),
    lambda w: setattr(w.groups[0], "status", "confirmed"),
    lambda w: setattr(w.groups[0], "adult_count", None),
    lambda w: setattr(w.groups[0], "adult_count", 1),
    lambda w: setattr(w.groups[0], "accessible", True),
    lambda w: setattr(w.resources[0], "accessible", True),
    lambda w: setattr(w.groups[0], "origin", "LHR"),
    lambda w: setattr(w.groups[0], "ready_at", 602),
    lambda w: setattr(w.groups[0], "deadline", 747),
    lambda w: setattr(w.groups[0], "size", 3),
    lambda w: w.groups[0].provenance.update(new_passenger_report=True),
    lambda w: setattr(w, "scenario_id", "different"),
    lambda w: setattr(w, "epoch", (NOW+timedelta(seconds=30)).isoformat()),
    lambda w: setattr(w.airports[0], "kind", "station"),
    lambda w: setattr(w.airports[1], "lat", 0),
    lambda w: setattr(w.resources[0], "departure", 600),
    lambda w: setattr(w.resources[0], "arrival", 747),
    lambda w: quote(w).update(provider="Other supplier"),
    lambda w: quote(w).update(source_url="https://example.invalid"),
    lambda w: quote(w).update(booking_supported=True),
    lambda w: quote(w).update(source_quoted_at=NOW.isoformat()),
    lambda w: quote(w).update(expires_at=(NOW+timedelta(hours=1)).isoformat()),
    lambda w: quote(w)["request"].update(passengers=3),
    lambda w: quote(w)["request"].update(passengers=True),
    lambda w: quote(w)["request"].update(suitcases=3),
    lambda w: quote(w)["request"].update(handbags=3),
    lambda w: quote(w)["request"].update(baggage_supplier_scoped=True),
    lambda w: quote(w)["request"].update(supplier_pickup_datetime="2030-01-04 10:00:00"),
    lambda w: quote(w)["request"].update(pickup_at="2030-01-04T10:00:00"),
    lambda w: quote(w)["resolved_route"]["origin"].update(place_id="other-airport"),
    lambda w: quote(w)["resolved_route"]["destination"].update(latitude=44),
    lambda w: quote(w)["route"].update(duration_seconds=8787.0),
    lambda w: quote(w)["route"].update(duration_basis="traffic_aware"),
    lambda w: quote(w)["route"].update(distance_meters=1),
    lambda w: quote(w)["policy"].update(required_advance_hours=4),
    lambda w: selected(w).update(fits_request=False),
    lambda w: selected(w).update(remaining_fleet=1),
    lambda w: selected(w).update(availability_status="confirmed"),
    lambda w: selected(w).update(supplier_valid_until=(NOW+timedelta(hours=1)).isoformat()),
    lambda w: selected(w).update(max_passengers=1),
    lambda w: selected(w).update(max_suitcases=True),
    lambda w: selected(w).update(max_handbags=1),
    lambda w: selected(w)["price"].update(currency="RON"),
    lambda w: selected(w)["price_components"].update(distance_fare_rounded_up="187.00"),
    lambda w: selected(w)["price_components"].update(payment_method="card"),
    lambda w: selected(w)["price_components"].update(extras_total="3.00"),
    lambda w: selected(w)["price_components"].update(night_surcharge_total="3.00"),
    lambda w: quote(w)["options"].append(copy.deepcopy(selected(w))),
])
def test_tampered_or_changed_evidence_cannot_enter_solver(change):
    world = state()
    change(world)
    assert not evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])
    assert not solver.resource_feasible(world, world.resources[0], world.groups[0], world.groups[0].origin, 600, set())


def test_exact_receipt_age_and_future_receipt_fail_closed():
    world = state()
    resource, group = world.resources[0], world.groups[0]
    assert evidence.valid_airport_cab_resource(world, resource, group, now=NOW+timedelta(seconds=120))
    assert not evidence.valid_airport_cab_resource(world, resource, group, now=NOW+timedelta(seconds=120, microseconds=1))
    assert not evidence.valid_airport_cab_resource(world, resource, group, now=NOW-timedelta(microseconds=1))
    resource.quote_freshness.received_at = (NOW+timedelta(seconds=1)).isoformat()
    assert not solver.live_resource_current(resource)


def test_unknown_source_cannot_reuse_freshness_or_cab_context_to_bypass_guard():
    world = state()
    resource = world.resources[0]
    resource.source = "Other source"
    assert not solver.resource_feasible(world, resource, world.groups[0], "OTP", 600, set())
    resource.quote_freshness = None
    resource.valid_until = (NOW+timedelta(hours=1)).isoformat()
    assert solver.live_resource_current(resource)
    assert not solver.resource_feasible(world, resource, world.groups[0], "OTP", 600, set())


def test_initial_quote_cannot_be_used_after_a_flight_or_another_leg():
    world = state()
    resource, group = world.resources[0], world.groups[0]
    assert solver.resource_feasible(world, resource, group, "OTP", 600, set())
    assert not solver.resource_feasible(world, resource, group, "OTP", 600, {"ARRIVING-FLIGHT"})
    assert not solver.resource_feasible(world, resource, group, "LHR", 600, set())


@pytest.mark.parametrize("status", ["needs_diversion", "weather_held"])
def test_original_arrival_becoming_pending_invalidates_receipt_without_group_change(status):
    world = state()
    plan = solver.build_candidate(world, Strategy())
    before_context = evidence.airport_cab_context(world, world.groups[0])
    world.resources.append(Resource(id="already-arrived", kind="flight", name="Original arrival",
        origin="LHR", destination="OTP", departure=400, arrival=599, capacity=2, cost_per_person=0,
        status=status, source="Fixture flight facts"))
    assert evidence.airport_cab_context(world, world.groups[0]) == before_context
    assert not evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])
    assert not solver.resource_feasible(world, world.resources[0], world.groups[0], "OTP", 600, set())
    assert not solver.validate_allocations(world, plan.allocations)["valid"]


def test_transfer_can_be_initial_prefix_of_a_real_onward_flight_only_with_complete_chain():
    world = state(destination="LHR")
    assert not solver.build_candidate(world, Strategy()).allocations
    world.resources.append(Resource(id="ONWARD", kind="flight", name="Onward quote", origin="CRA", destination="LHR",
        departure=778, arrival=900, capacity=2, cost_per_person=50, source="Fixture flight source", eligible_group_ids=["G1"],
        valid_until=(NOW+timedelta(hours=1)).isoformat()))
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2 and plan.total_cost == 288
    world.resources[-1].departure = 777
    assert not solver.build_candidate(world, Strategy()).allocations


def test_air_arrival_restriction_does_not_assert_a_road_closure():
    world = state()
    world.airports[1].operational_status = "closed"
    world.airports[1].available = False
    assert solver.build_candidate(world, Strategy()).recovered_passengers == 2


def test_one_group_can_compare_categories_but_two_groups_cannot_invent_two_cars():
    world = state()
    world.resources.append(cab(world, world.groups[0], identity="CAB-VAN", option_index=2))
    plan = solver.build_candidate(world, Strategy(objective="lowest_cost"))
    assert plan.validation["valid"] and plan.total_cost == 188
    group = world.groups[0].model_copy(deep=True)
    group.id = "G2"
    world.groups.append(group)
    world.resources.append(cab(world, group, identity="CAB-TWO"))
    joint = solver.build_candidate(world, Strategy())
    assert joint.validation["valid"] and joint.recovered_passengers == 2
    assert len(joint.exceptions) == 1
    assert "multiple-vehicle availability is unverified" in joint.exceptions[0]["reason"]
    first = solver.itinerary_options(world, world.groups[0])[0]
    second = solver.itinerary_options(world, group)[0]
    forged = [Allocation(group_id="G1", itinerary=first), Allocation(group_id="G2", itinerary=second)]
    check = solver.validate_allocations(world, forged)
    assert not check["valid"] and any("multiple-vehicle availability" in error for error in check["errors"])


@pytest.mark.parametrize("has_reservation", [True, False])
def test_fixed_out_of_scope_proposal_consumes_application_single_quote_allowance(has_reservation):
    world = state()
    old = solver.build_candidate(world, Strategy()).allocations[0].itinerary
    world.offers.append(Offer(id="FIXED", group_id="G1", itinerary=old, status="proposed",
        expires_at=(NOW+timedelta(seconds=120)).isoformat(), created_version=1))
    if has_reservation:
        world.reservations.append(Reservation(id="R1", offer_id="FIXED", group_id="G1", resource_id="CAB-ONE",
            quantity=2, status="proposed", expires_at=(NOW+timedelta(seconds=120)).isoformat()))
    second = world.groups[0].model_copy(deep=True)
    second.id = "G2"
    world.groups.append(second)
    world.resources.append(cab(world, second, identity="CAB-TWO"))
    plan = solver.build_candidate(world, Strategy(), scope_group_ids={"G2"})
    assert plan.validation["valid"] and not plan.allocations
    assert world.offers[0].status == "proposed"


def test_hold_uses_app_freshness_without_supplier_expiry_or_booking_claim():
    world = state()
    plan = solver.build_candidate(world, Strategy())
    service = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=10**9), None)
    service._hold_plan(world, plan, "solver", "Recover", "fixture", "fixture", [plan], 1)
    assert datetime.fromisoformat(world.offers[0].expires_at) == NOW+timedelta(seconds=120)
    assert world.offers[0].status == "proposed"
    assert world.resources[0].booking_status == "unbooked"
    assert world.resources[0].valid_until is None


def test_price_arithmetic_ignores_ambient_decimal_context():
    world = state()
    with localcontext() as context:
        context.prec = 2
        assert evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])


def test_presentation_status_name_and_token_changes_do_not_rewrite_reported_position():
    world = state()
    world.groups[0].status = "offered"
    world.groups[0].name = "Updated label"
    world.groups[0].token = "rotated-offline"
    assert evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])


def test_pickup_must_follow_exact_reported_readiness_not_only_rounded_minute():
    world = state()
    # Epoch has thirty seconds: minute601 is10:00:30 UTC, after the source's10:00 pickup.
    world.groups[0].ready_at = 601
    world.resources[0].provenance["airport_cab_context"] = evidence.airport_cab_context(world, world.groups[0])
    assert world.resources[0].departure == world.groups[0].ready_at
    assert not evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])


def test_source_coordinates_keep_verified_tolerance_without_moving_catalog_nodes():
    world = state()
    before = [item.model_dump() for item in world.airports]
    quote(world)["resolved_route"]["origin"]["latitude"] += 0.0005
    assert evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])
    assert [item.model_dump() for item in world.airports] == before


def test_source_lead_policy_is_rechecked_even_inside_application_freshness_window():
    world = state()
    quote(world)["policy"].update(min_advance_day_hours=10, min_advance_night_hours=10, required_advance_hours=10)
    assert evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])
    assert not evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0], now=NOW+timedelta(seconds=1))


def near_lead_deadline(world):
    received = NOW + timedelta(minutes=59)
    quote(world)["received_at"] = received.isoformat()
    world.resources[0].quote_freshness.received_at = received.isoformat()
    return received, NOW + timedelta(hours=1)


def test_current_receipt_and_solver_stop_at_source_lead_boundary_inside_app_window(fixed_clock):
    world = state()
    received, cutoff = near_lead_deadline(world)
    resource = world.resources[0]
    assert pricing.app_receipt_deadline(resource) == received + timedelta(seconds=120)
    assert evidence.airport_cab_deadline(resource) == cutoff
    for instant in (received, cutoff - timedelta(microseconds=1), cutoff):
        fixed_clock[0] = instant
        assert evidence.airport_cab_current(resource)
        assert solver.live_resource_current(resource)
        assert evidence.valid_airport_cab_resource(world, resource, world.groups[0])
    fixed_clock[0] = cutoff + timedelta(microseconds=1)
    assert not evidence.airport_cab_current(resource)
    assert not solver.live_resource_current(resource)
    assert not evidence.valid_airport_cab_resource(world, resource, world.groups[0])


@pytest.mark.parametrize("change", [
    lambda q: q.pop("policy"),
    lambda q: q["policy"].update(required_advance_hours=8),
    lambda q: q["policy"].update(required_advance_hours="9"),
    lambda q: q["policy"].update(required_advance_hours=True),
    lambda q: q["policy"].update(min_advance_day_hours=-1),
    lambda q: q["policy"].update(min_advance_night_hours=169),
    lambda q: q["policy"].update(advance_policy_basis="shorter_day_rule"),
    lambda q: q["policy"].update(night_restricted_start_time="25:00:00"),
    lambda q: q["policy"].pop("night_restricted_end_time"),
    lambda q: q["request"].update(pickup_at="2030-01-04T10:00:00"),
    lambda q: q["request"].update(supplier_pickup_datetime="2030-01-04 13:00:00"),
    lambda q: q["request"].update(timezone="UTC"),
])
def test_current_receipt_requires_strict_source_advance_policy(change):
    world = state()
    change(quote(world))
    assert evidence.airport_cab_deadline(world.resources[0]) is None
    assert not evidence.airport_cab_current(world.resources[0])
    assert not solver.live_resource_current(world.resources[0])


def test_proposal_and_every_local_segment_expire_at_earlier_cab_lead_cutoff(fixed_clock):
    world = state(destination="LHR")
    received, cutoff = near_lead_deadline(world)
    fixed_clock[0] = received
    world.resources.append(Resource(id="ONWARD", kind="flight", name="Onward quote", origin="CRA", destination="LHR",
        departure=778, arrival=900, capacity=2, cost_per_person=50, source="Fixture flight source", eligible_group_ids=["G1"],
        valid_until=(received+timedelta(hours=1)).isoformat()))
    plan = solver.build_candidate(world, Strategy())
    assert plan.validation["valid"] and plan.recovered_passengers == 2
    service = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=120), None)
    service._hold_plan(world, plan, "solver", "Recover", "fixture", "fixture", [plan], 1)
    assert datetime.fromisoformat(world.offers[0].expires_at) == cutoff
    assert len(world.reservations) == 2
    assert all(datetime.fromisoformat(row.expires_at) == cutoff for row in world.reservations)
    assert world.resources[0].valid_until is None
    assert world.resources[0].quote_freshness.max_age_seconds == 120


@pytest.mark.parametrize("parts, amount", [
    ({"price_per_km":"0.00", "distance_fare_rounded_up":"0.00", "custom_price":"40.00"}, "40.00"),
    ({"minimal_order_price":"200.00"}, "200.00"),
    ({"night_surcharge_applied":True, "night_surcharge_total":"3.00"}, "191.00"),
])
def test_native_price_formula_supports_source_custom_minimum_and_flat_night_fee(parts, amount):
    world = state()
    selected(world)["price_components"].update(parts)
    selected(world)["price"]["amount"] = amount
    world.resources[0].quote_total_cost = float(amount)
    assert evidence.valid_airport_cab_resource(world, world.resources[0], world.groups[0])


@pytest.mark.parametrize("with_onward_flight", [False, True])
def test_external_confirmation_rejects_category_quote_before_mutating_any_state(monkeypatch, with_onward_flight):
    import app.live_service as live_service
    monkeypatch.setattr(live_service, "utcnow", lambda: NOW)
    world = state(destination="LHR" if with_onward_flight else "CRA")
    if with_onward_flight:
        world.resources.append(Resource(id="ONWARD", kind="flight", name="Onward quote", origin="CRA", destination="LHR",
            departure=778, arrival=900, capacity=2, cost_per_person=50, source="Fixture flight source", eligible_group_ids=["G1"],
            valid_until=(NOW+timedelta(hours=1)).isoformat()))
    plan = solver.build_candidate(world, Strategy())
    recovery = RecoveryService(SimpleNamespace(request_timeout_seconds=1, hold_ttl_seconds=120), None)
    recovery._hold_plan(world, plan, "solver", "Recover", "fixture", "fixture", [plan], 1)
    before = world.model_dump(mode="json")
    # Deliberately no rollback wrapper: the guard itself must precede every write.
    store = SimpleNamespace(mutate=lambda key, payload, version, apply: apply(world))
    service = LiveOperations(store, recovery, None)
    request = ExternalConfirmation(expected_version=world.version, idempotency_key="cab-no-external-booking",
        bookings=[{"resource_id":leg.resource_id, "confirmation_reference":"operator-text", "confirmed_by":"fixture",
            "confirmed_at":NOW} for leg in world.offers[0].itinerary.legs])
    with pytest.raises(DomainError) as caught:
        service.confirm(world.offers[0].id, request)
    assert caught.value.status == 409
    assert caught.value.details == {"code":"airport_cab_booking_not_supported"}
    assert "unreserved vehicle-category" in str(caught.value)
    assert world.model_dump(mode="json") == before
