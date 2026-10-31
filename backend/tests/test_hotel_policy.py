"""Property-local policy checks use source fixtures, never hotel/provider calls."""
import asyncio
import copy
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import httpx

from app.hotel_location import property_zone
from app.hotel_policy import HotelPolicyError, source_stay_policy, validate_stay_policy
from app.store import DomainError
from test_live_service import alter_hotel_quotes, hotel_request, manifest_request, now, ops
from test_real_data import settings
from app.providers.real_data import RealDataProvider


RECEIVED = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
POLICY = {"checkin_start": "03:00 PM", "checkin_end": "", "checkout": "12:00 PM", "instructions": [], "special_instructions": ""}


def local(day, hour, minute=0, *, fold=0):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute,
        tzinfo=property_zone("Europe/London"), fold=fold)


def stay(**changes):
    return SimpleNamespace(**({"checkin": date(2026, 11, 1), "checkout": date(2026, 11, 2),
        "checkin_at": local(date(2026, 11, 1), 15), "checkout_at": local(date(2026, 11, 2), 12)} | changes))


def quote(policy=None):
    return {"id": "property-fixture", "stay_policy": source_stay_policy("property-fixture", POLICY if policy is None else policy, RECEIVED.isoformat())}


@pytest.mark.parametrize("arrival_hour,arrival_minute", [(15, 1), (23, 0), (14, 59)])
def test_unknown_checkin_end_never_implies_unlimited_arrival(arrival_hour, arrival_minute):
    with pytest.raises(HotelPolicyError, match="exact published"):
        validate_stay_policy(quote(), stay(checkin_at=local(date(2026, 11, 1), arrival_hour, arrival_minute)), "Europe/London", now=RECEIVED)


def test_exact_published_start_and_checkout_cutoff_are_valid_without_operator_override():
    result = validate_stay_policy(quote(), stay(), "Europe/London", now=RECEIVED)
    assert result["validated"] and result["checkin_rule"] == "exact_start_only" and result["checkin_end"] is None
    with pytest.raises(HotelPolicyError, match="checkout cutoff") as failure:
        validate_stay_policy(quote(), stay(checkout_at=local(date(2026, 11, 2), 12, 1)), "Europe/London", now=RECEIVED)
    assert "2026-11-02T12:00:00+00:00 (Europe/London)" in failure.value.message


@pytest.mark.parametrize("hour", [15, 18, 20])
def test_explicit_checkin_window_accepts_only_sourced_interval(hour):
    result = validate_stay_policy(quote(POLICY | {"checkin_end": "20:00"}),
        stay(checkin_at=local(date(2026, 11, 1), hour)), "Europe/London", now=RECEIVED)
    assert result["checkin_rule"] == "bounded_window"


@pytest.mark.parametrize("policy", [
    {"checkin_start": "anytime"}, {"checkin_start": None}, {"checkin_end": "until late"}, {"checkout": "noon"},
    {"checkout": "24:00"}, {"checkout": ["12:00"]}, {"checkinStart": "16:00"},
])
def test_missing_ambiguous_or_conflicting_clock_values_are_not_guessed(policy):
    with pytest.raises(HotelPolicyError):
        validate_stay_policy(quote(POLICY | policy), stay(), "Europe/London", now=RECEIVED)


def test_provider_aliases_must_agree_and_special_instructions_need_confirmation():
    assert validate_stay_policy(quote(POLICY | {"checkinStart": "15:00"}), stay(), "Europe/London", now=RECEIVED)["validated"]
    with pytest.raises(HotelPolicyError, match="special check-in"):
        validate_stay_policy(quote(POLICY | {"special_instructions": "Contact the property before arrival."}), stay(), "Europe/London", now=RECEIVED)


@pytest.mark.parametrize("changes", [{"hotel_id": "another-property"}, {"source": "other"},
    {"retrieved_at": (RECEIVED - timedelta(seconds=121)).isoformat()},
    {"retrieved_at": (RECEIVED + timedelta(seconds=1)).isoformat()}, {"retrieved_at": "2026-10-31T12:00:00"}])
def test_policy_source_identity_and_freshness_must_match(changes):
    value = quote()
    value["stay_policy"].update(changes)
    with pytest.raises(HotelPolicyError, match="identity or retrieval"):
        validate_stay_policy(value, stay(), "Europe/London", now=RECEIVED)


def test_requested_offset_does_not_override_property_timezone_or_calendar():
    with pytest.raises(HotelPolicyError):
        validate_stay_policy(quote(), stay(checkin_at=datetime(2026, 11, 1, 15, tzinfo=timezone(timedelta(hours=2)))), "Europe/London", now=RECEIVED)
    with pytest.raises(HotelPolicyError, match="quoted checkout date"):
        validate_stay_policy(quote(), stay(checkout_at=local(date(2026, 11, 3), 10)), "Europe/London", now=RECEIVED)


def test_explicit_cross_midnight_window_uses_the_quoted_hotel_night():
    result = validate_stay_policy(quote(POLICY | {"checkin_end": "02:00 AM"}),
        stay(checkin_at=local(date(2026, 11, 2), 1)), "Europe/London", now=RECEIVED)
    assert result["checkin_end"] == "2026-11-02T02:00:00+00:00"
    with pytest.raises(HotelPolicyError, match="outside"):
        validate_stay_policy(quote(POLICY | {"checkin_end": "02:00 AM"}),
            stay(checkin_at=local(date(2026, 11, 2), 2, 1)), "Europe/London", now=RECEIVED)


@pytest.mark.parametrize("day", [date(2027, 3, 28), date(2027, 10, 31)])
def test_dst_ambiguous_or_nonexistent_policy_boundary_is_not_assigned_a_fold(day):
    with pytest.raises(HotelPolicyError, match="ambiguous or nonexistent"):
        validate_stay_policy(quote(POLICY | {"checkin_start": "01:30", "checkin_end": "20:00"}),
            stay(checkin=day, checkout=day + timedelta(days=1), checkin_at=local(day, 15), checkout_at=local(day + timedelta(days=1), 10)),
            "Europe/London", now=RECEIVED)


def automatic_request(operations, **changes):
    tomorrow = now().astimezone(property_zone("Europe/London")).date() + timedelta(days=1)
    return hotel_request(operations, **({"airport_code": "LHR", "checkin": tomorrow, "checkout": tomorrow + timedelta(days=1),
        "checkin_at": local(tomorrow, 15), "checkout_at": local(tomorrow + timedelta(days=1), 12), "stay_times_confirmed": False} | changes))


@pytest.mark.asyncio
async def test_property_metadata_preserves_exact_identity_and_structured_policy():
    calls = []
    def metadata(request):
        calls.append(request)
        assert request.url.params["hotelId"] == "property-fixture"
        return httpx.Response(200, json={"data": {"id": "property-fixture", "location": {"latitude": 51.53, "longitude": -0.28},
                                                   "checkinCheckoutTimes": POLICY}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(metadata)) as client:
        result = await RealDataProvider(settings(liteapi_api_key="prod_test", liteapi_environment="live"), client=client).hotel_location("property-fixture")
    assert len(calls) == 1
    policy = result["stay_policy"]
    assert policy["hotel_id"] == "property-fixture" and policy["source"] == "Nuitee / LiteAPI"
    assert policy["checkin_start"] == "03:00 PM" and policy["checkin_end"] is None and policy["checkout"] == "12:00 PM"
    assert policy["status"] == "provided" and policy["retrieved_at"] == result["location_retrieved_at"]


@pytest.mark.asyncio
async def test_automatic_hotel_import_fetches_policy_even_with_rate_coordinates(ops):
    ops.manifest(manifest_request(ops))
    metadata_calls = []
    async def metadata(hotel_id):
        metadata_calls.append(hotel_id)
        return {"latitude": 51.53, "longitude": -0.28,
            "stay_policy": source_stay_policy(hotel_id, POLICY, now().isoformat())}
    ops.real_data.provider.hotel_location = metadata
    request = automatic_request(ops)
    result = await ops.hotels(request)
    assert metadata_calls == ["hotel-real-id"] and ops.real_data.provider.calls == 1
    provenance = result["world"]["resources"][0]["provenance"]
    assert provenance["stay_times_source"] == "supplier_policy" and provenance["stay_times_confirmed"] is False
    assert provenance["stay_policy_validation"]["validated"] and provenance["property_location"]["timezone"] == "Europe/London"
    assert provenance["ground_connections_verified"] is False and not result["world"]["offers"]
    assert result["skipped_stay_policy"] == 0 and result["hotel_exclusions"] == []
    assert (await ops.hotels(request))["replayed"] and len(metadata_calls) == 1


@pytest.mark.asyncio
async def test_all_unverified_hotel_policies_leave_existing_operation_unchanged(ops):
    ops.manifest(manifest_request(ops))
    await ops.hotels(hotel_request(ops))
    before = ops.store.read().model_dump_json()
    async def metadata(hotel_id):
        return {"latitude": 51.53, "longitude": -0.28, "stay_policy": source_stay_policy(hotel_id, None, now().isoformat())}
    ops.real_data.provider.hotel_location = metadata
    with pytest.raises(DomainError) as failure:
        await ops.hotels(automatic_request(ops, idempotency_key="unverified-policy-import"))
    assert failure.value.status == 422 and failure.value.details["skipped_stay_policy"] == 1
    assert failure.value.details["hotel_exclusions"][0]["code"] == "stay_policy_unavailable"
    assert ops.store.read().model_dump_json() == before


@pytest.mark.asyncio
async def test_policy_exclusions_and_bounded_metadata_work_are_separate_from_pricing(ops):
    ops.manifest(manifest_request(ops))
    def many(quotes):
        original = quotes[0]
        quotes[:] = [{**copy.deepcopy(original), "id": f"property-{index}", "provider_offer_id": f"offer-{index}"} for index in range(10)]
        quotes[-1]["pricing"].update(status="incomplete", mandatory_total=None, unresolved_reasons=["missing_fee"])
    alter_hotel_quotes(ops, many)
    active = maximum = 0
    calls = []
    async def metadata(hotel_id):
        nonlocal active, maximum
        active += 1
        maximum = max(active, maximum)
        calls.append(hotel_id)
        await asyncio.sleep(0)
        active -= 1
        raw = POLICY if hotel_id == "property-0" else None
        return {"latitude": 51.53, "longitude": -0.28, "stay_policy": source_stay_policy(hotel_id, raw, now().isoformat())}
    ops.real_data.provider.hotel_location = metadata
    result = await ops.hotels(automatic_request(ops))
    assert len(calls) == 9 and maximum == 3 and active == 0
    assert len(result["world"]["resources"]) == 1 and result["skipped_stay_policy"] == 8
    assert result["skipped_incomplete_pricing"] == 1 and len(result["hotel_exclusions"]) == 8


@pytest.mark.asyncio
async def test_oversized_hotel_response_never_dispatches_metadata(ops):
    ops.manifest(manifest_request(ops))
    alter_hotel_quotes(ops, lambda quotes: quotes.extend(copy.deepcopy(quotes[0]) for _ in range(10)))
    async def unexpected(hotel_id):
        raise AssertionError("Metadata must not be requested for an unbounded response")
    ops.real_data.provider.hotel_location = unexpected
    with pytest.raises(DomainError, match="invalid hotel quote list"):
        await ops.hotels(automatic_request(ops))


@pytest.mark.asyncio
async def test_explicit_operator_confirmation_remains_separate_from_supplier_policy(ops):
    ops.manifest(manifest_request(ops))
    async def unexpected(hotel_id):
        raise AssertionError("Existing coordinates and explicit confirmation need no policy request")
    ops.real_data.provider.hotel_location = unexpected
    result = await ops.hotels(hotel_request(ops))
    provenance = result["world"]["resources"][0]["provenance"]
    assert provenance["stay_times_source"] == "operator_confirmed" and provenance["stay_times_confirmed"] is True
    assert provenance["stay_policy_validation"] is None
