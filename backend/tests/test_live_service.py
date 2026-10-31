"""Live operational mutations use source data and never call booking APIs."""
import asyncio
import copy
import csv
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.live_data import MANIFEST_COLUMNS, ManifestImport, ManualResourceInput, PassengerGenerationInput
from app.live_service import ExternalConfirmation, HotelQuoteImport, LiveOperations
from app.models import AcceptRequest, Itinerary, Leg, Offer, Reservation
from app.providers.base import ProviderError
from app.service import RecoveryService
from app.solver import resource_cost
from app.store import DomainError, Store


class Provider:
    hotel_offer_provider = "liteapi"

    def __init__(self):
        self.calls = 0
        self.after_request = None
        self.error = None

    async def offers(self, airport, query):
        self.calls += 1
        if self.after_request:
            self.after_request()
        if self.error:
            raise self.error
        return [{"id": "hotel-real-id", "name": "Supplier hotel", "provider": "liteapi",
                 "provider_offer_id": f"quoted-offer-{self.calls}", "currency": "EUR", "total_price": "176.45",
                 "checkin": query.checkin.isoformat(), "checkout": query.checkout.isoformat(),
                 "adults": query.adults, "rooms": query.rooms, "quoted_at": datetime.now(timezone.utc).isoformat(),
                 "pricing": {"booking_total": {"amount": "176.45", "currency": "EUR"}, "excluded_fee_totals": [],
                             "mandatory_total": {"amount": "176.45", "currency": "EUR"}, "status": "complete",
                             "unresolved_reasons": [], "payment_timing": "unconfirmed"},
                 "expires_at": None, "room_rates": [{"room_name": "Supplier room"}]}]


@pytest.fixture
def ops(tmp_path):
    settings = Settings(_env_file=None, operation_mode="live", database_path=str(tmp_path / "live.sqlite3"))
    store = Store(settings.database_path, operation_mode="live")
    recovery = RecoveryService(settings, store)
    return LiveOperations(store, recovery, SimpleNamespace(provider=Provider()))


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def evidence():
    return {"source_name": "Operator statement", "source_reference": "dispatch-123", "confirmed_by": "Duty operator", "observed_at": now()}


def manifest_request(ops, **changes):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=MANIFEST_COLUMNS)
    writer.writeheader()
    for i in range(2):
        writer.writerow({"passenger_id": f"ACTUAL-{i}", "group_id": "REAL-GROUP", "name": f"Imported person {i}",
            "origin": "CRA", "destination": "OTP", "original_flight_id": "ACTUAL-FLIGHT",
            "ready_at": (now()+timedelta(minutes=30)).isoformat(), "deadline": (now()+timedelta(days=3)).isoformat(),
            "group_budget_eur": "500.00", "accessible": "false", "priority": "2", "is_adult": "true"})
    return ManifestImport(**({"expected_version": ops.store.read().version, "idempotency_key": "manifest-import-123", "csv_text": text.getvalue(), **evidence()} | changes))


def resource_request(ops, **changes):
    return ManualResourceInput(**({"expected_version": ops.store.read().version, "idempotency_key": "resource-import-123",
        "id": "FLIGHT-REAL", "kind": "flight", "name": "Confirmed operator service", "origin": "CRA", "destination": "OTP",
        "departure_at": now()+timedelta(hours=2), "arrival_at": now()+timedelta(hours=4), "capacity": 4,
        "unit_price_eur": "40.00", "accessible": True, "vehicle_id": None,
        "valid_until": now()+timedelta(hours=1), **evidence()} | changes))


def hotel_request(ops, **changes):
    local = now().astimezone(ZoneInfo("Europe/Bucharest"))
    checkin = (local+timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    checkout = (local+timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    return HotelQuoteImport(**({"expected_version": ops.store.read().version, "idempotency_key": "hotel-import-123",
        "group_id": "REAL-GROUP", "airport_code": "CRA", "checkin": checkin.date(), "checkout": checkout.date(),
        "rooms": 1, "checkin_at": checkin, "checkout_at": checkout, "stay_times_confirmed": True} | changes))


def test_manifest_and_resource_mutations_are_atomic_idempotent_and_preserve_sources(ops):
    request = manifest_request(ops)
    first = ops.manifest(request)
    assert len(first["world"]["groups"]) == 1 and first["world"]["budget"] == 500
    assert first["world"]["groups"][0]["source"] == "manifest_import"
    assert ops.manifest(request)["replayed"]
    assert len(ops.store.read().groups) == 1
    result = ops.resource(resource_request(ops))
    assert result["world"]["resources"][0]["source"] == "operator_confirmed"
    assert result["world"]["resources"][0]["booking_status"] == "unbooked"
    assert all(airport["available"] for airport in result["world"]["airports"])
    assert all(airport["operational_status"] == "unknown" for airport in result["world"]["airports"])
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError):
        ops.manifest(manifest_request(ops, idempotency_key="duplicate-manifest"))
    assert ops.store.read().model_dump_json() == before


def test_explicit_test_passenger_generation_never_generates_resources(ops):
    request = PassengerGenerationInput(expected_version=ops.store.read().version, idempotency_key="generate-passengers",
        passengers=5, group_size=2, origin="CRA", destination="OTP", original_flight_id="ACTUAL-FLIGHT",
        ready_at=now()+timedelta(minutes=30), deadline=now()+timedelta(days=1), group_budget_eur="100",
        accessible=False, priority=1, is_adult=True)
    result = ops.generate(request)
    assert [group["size"] for group in result["world"]["groups"]] == [2,2,1]
    assert all(group["source"] == "synthetic" for group in result["world"]["groups"])
    assert result["world"]["resources"] == [] and result["world"]["budget"] == 300


@pytest.mark.asyncio
async def test_hotel_import_is_bound_to_group_exact_amount_and_actual_expiry_only(ops):
    ops.manifest(manifest_request(ops))
    request = hotel_request(ops)
    result = await ops.hotels(request)
    resource = result["world"]["resources"][0]
    assert resource["source"] == "Nuitee / LiteAPI"
    assert resource["eligible_group_ids"] == ["REAL-GROUP"]
    assert resource["quote_total_cost"] == 176.45 and resource["quote_rooms"] == resource["capacity"] == 1
    assert resource["budget_total_cost"] == 176.45
    assert resource["valid_until"] is None and resource["requires_revalidation"] is True
    assert resource["external_offer_id"] == "quoted-offer-1"
    assert resource["provenance"]["room_rates"][0]["room_name"] == "Supplier room"
    assert resource["accessible"] is False
    assert (await ops.hotels(request))["replayed"] and ops.real_data.provider.calls == 1
    replacement = await ops.hotels(hotel_request(ops, idempotency_key="hotel-refresh-123"))
    assert len(replacement["world"]["resources"]) == 1
    assert replacement["world"]["resources"][0]["external_offer_id"] == "quoted-offer-2"


def alter_hotel_quotes(ops, change):
    original = ops.real_data.provider.offers
    async def offers(airport, query):
        quotes = await original(airport, query)
        change(quotes)
        return quotes
    ops.real_data.provider.offers = offers


@pytest.mark.asyncio
async def test_hotel_budget_includes_extra_fees_while_supplier_quote_stays_exact(ops):
    ops.manifest(manifest_request(ops))
    def change(quotes):
        quote = quotes[0]
        quote["total_price"] = "176.4501"
        quote["pricing"].update(
            booking_total={"amount": "176.4501", "currency": "EUR"},
            excluded_fee_totals=[{"amount": "5.0001", "currency": "EUR"}],
            mandatory_total={"amount": "181.4502", "currency": "EUR"},
        )
    alter_hotel_quotes(ops, change)
    result = await ops.hotels(hotel_request(ops))
    resource = ops.store.read().resources[0]
    assert resource.quote_total_cost == 176.4501
    assert resource.budget_total_cost == 181.46
    assert resource_cost(resource, ops.store.read().groups[0]) == 181.46
    assert resource.provenance["total_price"] == "176.4501"
    assert resource.provenance["pricing"]["mandatory_total"]["amount"] == "181.4502"
    assert result["skipped_incomplete_pricing"] == 0
    assert resource.booking_status == "unbooked"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["fees_not_disclosed", "excluded_fee_amount_missing", "unsupported_budget_currency"])
async def test_incomplete_hotel_pricing_cannot_change_inventory_or_create_budgeted_options(ops, reason):
    ops.manifest(manifest_request(ops))
    def change(quotes):
        quotes[0]["pricing"].update(status="incomplete", mandatory_total=None, unresolved_reasons=[reason])
    alter_hotel_quotes(ops, change)
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError, match="unresolved mandatory costs") as error:
        await ops.hotels(hotel_request(ops))
    assert error.value.status == 422
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_hotel_import_reports_skipped_incomplete_quotes_without_losing_complete_options(ops):
    ops.manifest(manifest_request(ops))
    def change(quotes):
        incomplete = copy.deepcopy(quotes[0])
        incomplete["id"] = "hotel-with-unresolved-fees"
        incomplete["provider_offer_id"] = "opaque-other-offer"
        incomplete["pricing"].update(status="incomplete", mandatory_total=None, unresolved_reasons=["fees_not_disclosed"])
        quotes.insert(0, incomplete)
    alter_hotel_quotes(ops, change)
    result = await ops.hotels(hotel_request(ops))
    assert len(result["world"]["resources"]) == 1
    assert result["world"]["resources"][0]["provenance"]["id"] == "hotel-real-id"
    assert result["skipped_incomplete_pricing"] == 1
    assert "Skipped 1 quotes with unresolved mandatory costs" in result["explanation"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    lambda quote: quote.pop("pricing"),
    lambda quote: quote["pricing"].update(mandatory_total={"amount": "5.00", "currency": "EUR"}),
    lambda quote: quote["pricing"].update(mandatory_total={"amount": "176.45", "currency": "RON"}),
    lambda quote: quote["pricing"].update(status="incomplete"),
])
async def test_hotel_import_rejects_missing_or_inconsistent_normalized_pricing_atomically(ops, change):
    ops.manifest(manifest_request(ops))
    alter_hotel_quotes(ops, lambda quotes: change(quotes[0]))
    before = ops.store.read().model_dump_json()
    with pytest.raises(DomainError) as error:
        await ops.hotels(hotel_request(ops))
    assert error.value.status == 502
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_hotel_fetch_cannot_commit_against_changed_world(ops):
    ops.manifest(manifest_request(ops))
    request = hotel_request(ops)
    ops.real_data.provider.after_request = lambda: ops.store.mutate("concurrent-update", {}, request.expected_version, lambda world: {})
    with pytest.raises(DomainError) as error:
        await ops.hotels(request)
    assert error.value.status == 409 and ops.store.read().resources == []


@pytest.mark.asyncio
async def test_hotel_provider_failure_does_not_mutate_or_fall_back(ops):
    ops.manifest(manifest_request(ops))
    before = ops.store.read().version
    ops.real_data.provider.error = ProviderError("Provider unavailable", code="http_503")
    with pytest.raises(ProviderError):
        await ops.hotels(hotel_request(ops))
    assert ops.store.read().version == before and ops.store.read().resources == []


def test_hotel_stay_times_require_confirmation_and_matching_dates(ops):
    ops.manifest(manifest_request(ops))
    with pytest.raises(ValidationError):
        hotel_request(ops, stay_times_confirmed=False)
    with pytest.raises(ValidationError):
        hotel_request(ops, checkin_at=now())


def attach_proposal(ops):
    def create(world):
        resource = world.resources[0]
        itinerary = Itinerary(legs=[Leg(resource_id=resource.id, kind="flight", origin="CRA", destination="OTP",
            departure=resource.departure, arrival=resource.arrival, cost=80)], arrival=resource.arrival, total_cost=80)
        expiry = (now()+timedelta(minutes=10)).isoformat()
        world.offers.append(Offer(id="PROPOSAL-1", group_id="REAL-GROUP", itinerary=itinerary, status="proposed", expires_at=expiry, created_version=world.version+1))
        world.reservations.append(Reservation(id="RECORD-1", offer_id="PROPOSAL-1", group_id="REAL-GROUP", resource_id=resource.id, quantity=2, status="proposed", expires_at=expiry))
    ops.store.mutate("attach-test-proposal", {}, ops.store.read().version, create)


def confirmation(ops, **changes):
    return ExternalConfirmation(**({"expected_version": ops.store.read().version, "idempotency_key": "confirm-external-123", "bookings": [{
        "resource_id": "FLIGHT-REAL", "confirmation_reference": "TICKET-PNR-123", "confirmed_by": "Operator",
        "confirmed_at": now().isoformat()}]} | changes))


def test_external_confirmation_records_all_segments_atomically_without_provider_calls(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    request = confirmation(ops)
    result = ops.confirm("PROPOSAL-1", request)
    assert result["offer"]["status"] == "confirmed"
    assert result["offer"]["external_bookings"][0]["confirmation_reference"] == "TICKET-PNR-123"
    assert result["world"]["groups"][0]["status"] == "confirmed"
    assert result["world"]["reservations"][0]["status"] == "confirmed"
    assert ops.real_data.provider.calls == 0
    assert ops.confirm("PROPOSAL-1", request)["replayed"]


@pytest.mark.parametrize("accepted", [False, True])
def test_live_decline_releases_proposed_or_accepted_allocations_without_booking_claims(ops, accepted):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    token = ops.store.read().groups[0].token
    if accepted:
        response = ops.recovery.accept("PROPOSAL-1", AcceptRequest(token=token, idempotency_key="accept-live-proposal"))
        assert response["offer"]["status"] == "accepted" and response["simulated"] is False
    before = ops.store.read().version
    result = ops.recovery.decline("PROPOSAL-1", token, "decline-live-proposal")
    assert result["simulated"] is False and result["offer"]["status"] == "declined"
    assert "No supplier cancellation" in result["offer"]["reason"]
    world = ops.store.read()
    assert world.version == before+1
    assert world.groups[0].status == "waiting"
    assert all(row.status == "released" for row in world.reservations)
    assert world.stats["resource_usage"]["FLIGHT-REAL"] == 0
    assert world.offers[0].external_bookings == []
    assert ops.recovery.decline("PROPOSAL-1", token, "decline-live-proposal")["replayed"]
    assert ops.store.read().version == world.version
    assert len([event for event in world.events if event.type == "offer_declined"]) == 1
    assert ops.real_data.provider.calls == 0


def test_passenger_decline_cannot_cancel_a_recorded_external_booking(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    ops.confirm("PROPOSAL-1", confirmation(ops))
    before = ops.store.read()
    with pytest.raises(DomainError) as error:
        ops.recovery.decline("PROPOSAL-1", before.groups[0].token, "decline-booked-proposal")
    assert error.value.status == 409
    after = ops.store.read()
    assert after.version == before.version and after.offers == before.offers
    assert after.reservations == before.reservations
    assert after.groups[0].status == "confirmed"
    assert ops.real_data.provider.calls == 0


def test_wrong_external_reference_and_test_passenger_confirmation_roll_back(ops):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    before = ops.store.read().version
    bad = confirmation(ops, bookings=[{"resource_id":"OTHER", "confirmation_reference":"proof", "confirmed_by":"operator", "confirmed_at":now()}])
    with pytest.raises(DomainError):
        ops.confirm("PROPOSAL-1", bad)
    assert ops.store.read().version == before
    ops.store.mutate("set-test-group", {}, before, lambda world: setattr(world.groups[0], "source", "synthetic"))
    before = ops.store.read().version
    with pytest.raises(DomainError):
        ops.confirm("PROPOSAL-1", confirmation(ops))
    assert ops.store.read().version == before


@pytest.mark.parametrize("confirmed", [False, True])
def test_inventory_update_invalidates_only_unbooked_proposals(ops, confirmed):
    ops.manifest(manifest_request(ops))
    ops.resource(resource_request(ops))
    attach_proposal(ops)
    if confirmed:
        ops.confirm("PROPOSAL-1", confirmation(ops))
    result = ops.resource(resource_request(ops, id="SECOND-FLIGHT", idempotency_key="another-real-resource"))
    assert result["world"]["offers"][0]["status"] == ("confirmed" if confirmed else "invalidated")
    assert result["world"]["reservations"][0]["status"] == ("confirmed" if confirmed else "released")
    if confirmed:
        assert result["world"]["offers"][0]["external_bookings"][0]["confirmation_reference"] == "TICKET-PNR-123"


def test_operator_flight_confirmation_does_not_override_known_airport_closure(ops):
    ops.manifest(manifest_request(ops))
    def close(world):
        airport = next(item for item in world.airports if item.id == "CRA")
        airport.operational_status, airport.available = "closed", False
    ops.store.mutate("airport-closure", {}, ops.store.read().version, close)
    result = ops.resource(resource_request(ops))
    airport = next(item for item in result["world"]["airports"] if item["id"] == "CRA")
    assert airport["operational_status"] == "closed" and airport["available"] is False
