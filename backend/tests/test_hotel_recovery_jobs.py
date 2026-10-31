"""Whole hotel packages run against fixture providers and isolated SQLite only."""
import asyncio
import copy
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest

from app.airport_catalog import lookup_airport
from app.hotel_policy import source_stay_policy
from app.hotel_recovery import HotelRecoveryInputs
from app.hotel_recovery_profile import PROFILE_KEY, build_hotel_recovery_profile, read_hotel_recovery_profile
from app.models import Airport, Itinerary, Leg, Offer, Reservation, Resource
from app.providers.base import ProviderError
from app.providers.ecb_rates import EcbRates
from app.providers.reference_fx import EcbRateTable, ECB_ZONE
from app.recovery_jobs import RecoveryCancellation, RecoveryJobRequest
from app.stay_requirements import REQUIREMENT_KEY
from app.store import DomainError, utcnow
from test_recovery_jobs import setup, run_next
from test_transfer_planning import Sources


class PackageSources(Sources):
    configured=True
    hotel_offer_provider="liteapi"
    liteapi_configured=True
    liteapi_sandbox=False
    def __init__(self):
        super().__init__(utcnow)
        self.incomplete=False
        self.bad_policy=False
        self.duration="3600.000000001"
        self.return_error=False
        self.return_route_seen=asyncio.Event()
        self.outbound_gate=None
        self.active=0
        self.max_active=0

    async def offers(self,airport,request):
        self.calls.append(("hotel_rates",airport,request))
        def item(identity,amount):
            money={"amount":amount,"currency":"EUR"}
            return {"id":identity,"name":"Sourced property","provider":"liteapi","provider_offer_id":"supplier-"+identity,
                "currency":"EUR","total_price":amount,"adults":request.adults,"rooms":request.rooms,
                "checkin":request.checkin.isoformat(),"checkout":request.checkout.isoformat(),"quoted_at":utcnow().isoformat(),
                "expires_at":(utcnow()+timedelta(minutes=5)).isoformat(),
                "pricing":{"status":"incomplete" if self.incomplete else "complete","booking_total":money,"mandatory_total":None if self.incomplete else money,
                    "excluded_fee_totals":[],"unresolved_reasons":["missing_fees"] if self.incomplete else []}}
        return [item("hotel-first","150.00"),item("hotel-unused","250.00")]

    async def hotel_location(self,identity):
        result=await super().hotel_location(identity)
        result["stay_policy"]=source_stay_policy(identity,{"checkin_start":"16:00" if self.bad_policy else "15:00","checkout":"12:00"},utcnow().isoformat())
        return result

    async def quote(self,request):
        self.active+=1
        self.max_active=max(self.max_active,self.active)
        try:
            if self.outbound_gate and request.expected_direction=="from_hub": await self.outbound_gate.wait()
            if self.return_error and request.expected_direction=="to_hub":
                await self.return_route_seen.wait()
                raise ProviderError("Fixture return outcome unknown",provider="APL",code="timeout")
            result=await super().quote(request)
            result["resolved_route"]["direction"]=request.expected_direction
            result.pop("fx")
            result["options"][0].pop("eur_reference_estimate")
            other=copy.deepcopy(result["options"][0])
            other.update(id="car-more-expensive",car_type="Larger car",price={"amount":"100.00","currency":"GBP"})
            result["options"].append(other)
            return result
        finally: self.active-=1

    async def dated_route(self,origin,destination,pickup):
        result=await super().dated_route(origin,destination,pickup)
        if origin["latitude"]==51.53: self.return_route_seen.set()
        result["duration_seconds"]=self.duration
        return result


class PackageFX:
    MAX_RATE_AGE_DAYS=3
    _now=staticmethod(utcnow)
    enrich=EcbRates.enrich
    def __init__(self): self.calls=0
    async def rates(self):
        self.calls+=1
        return {"table":EcbRateTable(rate_date=utcnow().astimezone(ECB_ZONE).date(),retrieved_at=utcnow(),source_units_per_eur={"GBP":Decimal("0.8")}),"cache":"fresh"}


@pytest.fixture
def package(setup):
    settings,store,jobs,ai,travel=setup
    from app.providers.apl import LONDON
    tomorrow=utcnow().astimezone(LONDON).date()+timedelta(days=1)
    checkin=datetime.combine(tomorrow,time(15),LONDON)
    checkout=datetime.combine(tomorrow+timedelta(days=1),time(9),LONDON)
    point={"label":"Terminal 5 declared road point","latitude":51.47,"longitude":-.48,"confirmed":True}
    inputs={"checkin_at":checkin.isoformat(),"checkout_at":checkout.isoformat(),"rooms":1,"minimum_rest_minutes":480,
        "initial_point":point,"pickup_at":(checkin-timedelta(minutes=90)).isoformat(),"suitcases":2,
        "onward_airport_code":"LHR","airport_dropoff":point,"airport_processing_minutes":30}
    def configure(world):
        reference=lookup_airport("LHR")
        world.airports.append(Airport(id="LHR",code="LHR",name="London Heathrow Airport",lat=reference["lat"],lon=reference["lon"]))
        group=world.groups[0]
        group.origin="LHR"
        group.size=group.adult_count=2
        group.members=["Fixture adult A","Fixture adult B"]
        group.ready_at=0
        group.deadline=int((checkout+timedelta(hours=12)-datetime.fromisoformat(world.epoch)).total_seconds()/60)
        group.budget=800
        world.budget=2000
    store.mutate("package-fixture",{},store.read().version,configure)
    sources,fx=PackageSources(),PackageFX()
    jobs.hotel_provider=jobs.apl_provider=sources
    jobs.fx_provider=fx
    original=travel.search
    async def alternatives(query):
        result=await original(query)
        other=copy.deepcopy(result["items"][0])
        other["id"]+="-unused"
        other["provider_offer_id"]+="-unused"
        other["total_price"]="200.00"
        result["items"].append(other)
        return result
    travel.search=alternatives
    return store,jobs,ai,travel,sources,fx,inputs


def package_request(store,inputs,**changes):
    return RecoveryJobRequest(**({"group_ids":["G1"],"expected_version":store.read().version,"idempotency_key":"package-job-fixture",
        "modes":["flight","hotel","bus"],"max_provider_calls":10,"hotel_recovery":inputs}|changes))


def world_snapshot(store):
    # Live Store.read derives sim_time from wall-clock minutes without a commit.
    # Keep every other field, including version, requirements and reservations,
    # so crossing a minute cannot masquerade as an operational mutation.
    return store.read().model_dump(mode="json",exclude={"sim_time"})


@pytest.mark.asyncio
async def test_full_package_counts_each_source_and_atomically_commits_only_chosen_chain(package):
    store,jobs,ai,travel,sources,fx,inputs=package
    before=world_snapshot(store)
    request=package_request(store,inputs)
    jobs.start("CASE",request)
    assert world_snapshot(store)==before
    result=await run_next(jobs)
    assert result["status"]=="completed",result
    assert result["calls_dispatched"]==10 and len(result["steps"])==10
    assert all(item["status"]=="succeeded" for item in result["steps"])
    assert [item["provider"] for item in result["steps"]].count("European Central Bank")==1
    assert fx.calls==1 and len(travel.calls)==1 and len(ai.decisions)==1
    assert sum(item[0]=="metadata" for item in sources.calls)==1
    assert next(item[1] for item in sources.calls if item[0]=="metadata")=="hotel-first"
    world=store.read()
    assert world.version==before["version"]+1
    assert len(world.resources)==4 and len(world.offers)==1
    assert [leg.kind for leg in world.offers[0].itinerary.legs]==["bus","hotel","bus","flight"]
    assert all(item.booking_status=="unbooked" for item in world.resources)
    assert world.groups[0].provenance[REQUIREMENT_KEY]["minimum_rest_minutes"]==480
    assert world.groups[0].origin=="LHR" and world.groups[1].model_dump(mode="json")==before["groups"][1]
    assert world.offers[0].itinerary.total_cost==450
    assert world.offers[0].itinerary.cost_evidence is not None
    assert result["result"]["hotel_recovery"]["hotel_shortlist_limit"]==1
    assert result["result"]["hotel_recovery"]["return_pickup_basis"]=="checkout_plus_10_minute_app_allowance"
    with store.connect() as conn:
        rows=conn.execute("SELECT rowid FROM transfer_quote_receipts ORDER BY rowid").fetchall()
    assert len(rows)==2
    assert {item.provenance["quote_sequence"] for item in world.resources if item.source=="Airport Pickups London"}=={row[0] for row in rows}
    assert jobs.start("CASE",request)["replayed"] is True and fx.calls==1


@pytest.mark.asyncio
async def test_success_saves_stable_profile_before_both_transfer_fingerprints(package):
    from app.airport_arrival import valid_airport_arrival
    from app.initial_position import initial_position_fingerprint, valid_initial_position
    store,jobs,ai,travel,sources,fx,inputs=package
    assert PROFILE_KEY not in store.read().groups[0].provenance
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="completed",result
    world=store.read()
    group=world.groups[0]
    expected=build_hotel_recovery_profile(world,group,HotelRecoveryInputs.model_validate(inputs))
    assert group.provenance[PROFILE_KEY]==expected
    assert read_hotel_recovery_profile(world,group)==HotelRecoveryInputs.model_validate(inputs).model_dump(mode="json")
    projected_group=next(item for item in ai.decisions[0][0]["groups"] if item["id"]==group.id)
    assert projected_group["provenance"][PROFILE_KEY]==expected
    assert set(expected)=={"schema_version","source","scope_fingerprint","inputs"}
    outbound=next(item for item in world.resources if item.initial_position_assumption is not None)
    inbound=next(item for item in world.resources if item.airport_arrival_assumption is not None)
    fingerprint=initial_position_fingerprint(world,group)
    assert outbound.initial_position_assumption.group_fingerprint==fingerprint
    assert inbound.airport_arrival_assumption.group_fingerprint==fingerprint
    assert valid_initial_position(world,outbound,group) and valid_airport_arrival(world,inbound,group)
    missing_profile=group.model_copy(deep=True)
    missing_profile.provenance.pop(PROFILE_KEY)
    assert not valid_initial_position(world,outbound,missing_profile)
    assert not valid_airport_arrival(world,inbound,missing_profile)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome",["blocked","unknown","rollback"])
async def test_failed_package_preserves_previous_profile_and_foreign_state(package,monkeypatch,outcome):
    store,jobs,ai,travel,sources,fx,inputs=package
    previous=HotelRecoveryInputs.model_validate(inputs|{"minimum_rest_minutes":240})
    def previous_profiles(world):
        for group in world.groups:
            group.provenance[PROFILE_KEY]=build_hotel_recovery_profile(world,group,previous)
    store.mutate("saved-profile-fixture",{},store.read().version,previous_profiles)
    if outcome=="blocked": sources.incomplete=True
    elif outcome=="unknown": sources.return_error=True
    else:
        def reject(*args): raise DomainError(409,"Fixture atomic receipt rollback")
        monkeypatch.setattr(jobs.ledger,"complete_in_transaction",reject)
    before=world_snapshot(store)
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"] in ("blocked","unknown_outcome","needs_review"),result
    assert world_snapshot(store)==before
    assert read_hotel_recovery_profile(store.read(),store.read().groups[0])["minimum_rest_minutes"]==240


@pytest.mark.parametrize("change",[{"max_provider_calls":9},{"group_ids":["G1","G2"]},{"modes":["flight","hotel"]}])
def test_package_contract_rejects_insufficient_explicit_scope_or_allowance_before_calls(package,change):
    store,jobs,ai,travel,sources,fx,inputs=package
    with pytest.raises(DomainError) as error: jobs.start("CASE",package_request(store,inputs,**change))
    assert error.value.status==422
    assert not ai.plans and not travel.calls and not sources.calls and fx.calls==0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,expected_calls",[("pricing",3),("policy",4),("metadata",4),("outbound_late",7),("budget",7)])
async def test_missing_room_policy_physical_link_or_budget_stops_dependent_calls_without_mutation(package,failure,expected_calls):
    store,jobs,ai,travel,sources,fx,inputs=package
    if failure=="pricing": sources.incomplete=True
    if failure=="policy": sources.bad_policy=True
    if failure=="metadata": sources.address=None
    if failure=="outbound_late": sources.duration="7200"
    if failure=="budget":
        store.mutate("lower-package-budget",{},store.read().version,lambda world:setattr(world.groups[0],"budget",300))
    before=world_snapshot(store)
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"] in ("blocked","needs_review"),result
    assert result["calls_dispatched"]==expected_calls
    assert world_snapshot(store)==before and not ai.decisions
    assert not any(item[0]=="apl" and item[1].expected_direction=="to_hub" for item in sources.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("budget,expected_calls",[(0.60,9),(0.595,9),(0.594,7)])
async def test_early_package_budget_uses_exact_money_and_validator_half_cent_tolerance(package,monkeypatch,budget,expected_calls):
    store,jobs,ai,travel,sources,fx,inputs=package
    original_rates,original_quote,original_flights=sources.offers,sources.quote,travel.search
    async def rates(*args):
        items=await original_rates(*args)
        items[0]["total_price"]="0.20"
        for key in ("booking_total","mandatory_total"):
            items[0]["pricing"][key]={"amount":"0.20","currency":"EUR"}
        return items
    async def quote(*args):
        result=await original_quote(*args)
        result["options"][0]["price"]["amount"]="0.08"  # ECB 0.8 GBP/EUR gives EUR0.10.
        return result
    async def flights(*args):
        result=await original_flights(*args)
        result["items"][0]["total_price"]="0.30"
        return result
    monkeypatch.setattr(sources,"offers",rates)
    monkeypatch.setattr(sources,"quote",quote)
    monkeypatch.setattr(travel,"search",flights)
    store.mutate("boundary-package-budget",{},store.read().version,lambda world:setattr(world.groups[0],"budget",budget))
    before=world_snapshot(store)
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    # The known minimum is exactly EUR0.60; a floating sum is 0.6000000000000001.
    # The return fare later makes every full chain too expensive, so no plan commits.
    assert result["status"]=="blocked",result
    assert result["calls_dispatched"]==expected_calls
    assert (any(item["code"]=="hotel_package_budget" for item in result["gaps"]))==(expected_calls==7)
    assert world_snapshot(store)==before and not ai.decisions


@pytest.mark.asyncio
async def test_unknown_return_outcome_is_durable_and_never_retried_or_applied(package):
    store,jobs,ai,travel,sources,fx,inputs=package
    sources.return_error=True
    body=package_request(store,inputs)
    before=world_snapshot(store)
    jobs.start("CASE",body)
    result=await run_next(jobs)
    assert result["status"]=="unknown_outcome",result
    assert result["calls_dispatched"]==9 and any(item["status"]=="unknown" for item in result["steps"])
    assert all(item["status"]!="dispatched" for item in result["steps"])
    assert world_snapshot(store)==before and not ai.decisions
    count=len(sources.calls)
    assert jobs.start("CASE",body)["replayed"] is True and len(sources.calls)==count


@pytest.mark.asyncio
async def test_package_cancellation_drains_dispatched_sources_and_never_changes_world(package):
    store,jobs,ai,travel,sources,fx,inputs=package
    sources.outbound_gate=asyncio.Event()
    started=jobs.start("CASE",package_request(store,inputs))
    claimed=jobs.ledger.claim_next()
    before=world_snapshot(store)
    task=asyncio.create_task(jobs.run(*claimed))
    while not (any(item[0]=="route" for item in sources.calls) and sources.active and fx.calls==1):
        if task.done():
            task.result()
            pytest.fail("Job ended before reaching the source gate")
        await asyncio.sleep(.001)
    jobs.cancel(started["job"]["id"],RecoveryCancellation(idempotency_key="cancel-hotel-package"))
    sources.outbound_gate.set()
    await task
    result=jobs.get(started["job"]["id"])["job"]
    assert result["status"]=="cancelled" and result["calls_dispatched"]==7
    assert all(item["status"]!="dispatched" for item in result["steps"])
    assert world_snapshot(store)==before and not ai.decisions


@pytest.mark.asyncio
async def test_failure_of_atomic_job_receipt_rolls_back_requirement_nodes_resources_and_offers(package,monkeypatch):
    store,jobs,ai,travel,sources,fx,inputs=package
    before=world_snapshot(store)
    def reject(*args): raise DomainError(409,"Fixture atomic completion rejected")
    monkeypatch.setattr(jobs.ledger,"complete_in_transaction",reject)
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="needs_review" and result["calls_dispatched"]==10
    assert world_snapshot(store)==before


@pytest.mark.asyncio
async def test_quote_expiry_during_decisions_rolls_back_the_whole_selected_package(package,monkeypatch):
    import app.hotel_recovery as module
    store,jobs,ai,travel,sources,fx,inputs=package
    before=world_snapshot(store)
    async def aged_sources():
        monkeypatch.setattr(module,"utcnow",lambda:utcnow()+timedelta(seconds=121))
    ai.before_choose=aged_sources
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="needs_review" and result["calls_dispatched"]==10
    assert "expired" in result["message"].lower()
    assert world_snapshot(store)==before


@pytest.mark.asyncio
async def test_operator_change_during_decisions_does_not_commit_a_stale_package(package):
    store,jobs,ai,travel,sources,fx,inputs=package
    async def changed_operation():
        store.mutate("operator-current-budget",{},store.read().version,lambda world:setattr(world,"budget",1999))
    ai.before_choose=changed_operation
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="needs_review" and result["calls_dispatched"]==10
    world=store.read()
    assert world.budget==1999 and world.resources==[] and world.offers==[]
    assert REQUIREMENT_KEY not in world.groups[0].provenance


@pytest.mark.asyncio
async def test_hotel_query_uses_exact_post_stay_window_before_provider_shortlist(package):
    store,jobs,ai,travel,sources,fx,inputs=package
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="completed"
    sent=travel.calls[0]
    assert sent.earliest_departure_at==datetime.fromisoformat(inputs["checkout_at"])+timedelta(minutes=40)
    world=store.read()
    assert sent.latest_arrival_at==datetime.fromisoformat(world.epoch)+timedelta(minutes=world.groups[0].deadline)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds_after_bound",[0,30])
async def test_cheapest_flight_without_driving_time_does_not_hide_later_complete_package(package,monkeypatch,seconds_after_bound):
    store,jobs,ai,travel,sources,fx,inputs=package
    original=travel.search
    async def boundary_first(query):
        result=await original(query)
        boundary=copy.deepcopy(result["items"][0])
        departure=query.earliest_departure_at+timedelta(seconds=seconds_after_bound)
        arrival=departure+timedelta(hours=2)
        boundary.update(id="boundary-flight",provider_offer_id="boundary-flight",total_price="50.00",
            departure_at=departure.isoformat(),arrival_at=arrival.isoformat())
        boundary["segments"][0].update(departure_at=departure.isoformat(),arrival_at=arrival.isoformat())
        result["items"].insert(0,boundary)
        return result
    monkeypatch.setattr(travel,"search",boundary_first)
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="completed",result
    assert result["calls_dispatched"]==10 and len(travel.calls)==1
    world=store.read()
    assert len(world.resources)==4 and world.offers[0].itinerary.total_cost==450
    flight=next(item for item in world.resources if item.kind=="flight")
    assert flight.external_offer_id!="boundary-flight" and flight.quote_total_cost==100


@pytest.mark.asyncio
async def test_normal_flight_job_passes_readiness_and_deadline_to_provider_shortlist(setup):
    from test_recovery_jobs import request
    _,store,jobs,ai,travel=setup
    world=store.read()
    jobs.start("CASE",request(store))
    result=await run_next(jobs)
    assert result["status"]=="completed"
    assert travel.calls[0].earliest_departure_at==datetime.fromisoformat(world.epoch)+timedelta(minutes=world.groups[0].ready_at+30)
    assert travel.calls[0].latest_arrival_at==datetime.fromisoformat(world.epoch)+timedelta(minutes=world.groups[0].deadline)


@pytest.mark.asyncio
async def test_unrelated_confirmed_booking_and_reservation_remain_exact(package):
    store,jobs,ai,travel,sources,fx,inputs=package
    def foreign(world):
        group=world.groups[1]
        group.status="confirmed"
        group.provenance[PROFILE_KEY]=build_hotel_recovery_profile(world,group,HotelRecoveryInputs.model_validate(inputs))
        resource=Resource(id="FOREIGN",kind="flight",name="External booked flight",origin=group.origin,destination=group.destination,
            departure=group.ready_at+60,arrival=group.ready_at+120,capacity=1,cost_per_person=100,source="nuitee",booking_status="confirmed",eligible_group_ids=[group.id],quote_total_cost=100)
        world.resources.append(resource)
        itinerary=Itinerary(legs=[Leg(resource_id=resource.id,kind="flight",origin=resource.origin,destination=resource.destination,departure=resource.departure,arrival=resource.arrival,cost=100)],total_cost=100,arrival=resource.arrival)
        world.offers.append(Offer(id="FOREIGN-OFFER",group_id=group.id,itinerary=itinerary,status="confirmed",expires_at="2090-01-01T00:00:00Z",created_version=world.version,
            external_bookings=[{"confirmation_reference":"fixture-existing-booking"}]))
        world.reservations.append(Reservation(id="FOREIGN-RESERVATION",offer_id="FOREIGN-OFFER",group_id=group.id,resource_id=resource.id,quantity=1,status="confirmed",expires_at="2090-01-01T00:00:00Z"))
    store.mutate("foreign-booking",{},store.read().version,foreign)
    before=world_snapshot(store)
    jobs.start("CASE",package_request(store,inputs))
    result=await run_next(jobs)
    assert result["status"]=="completed",result
    after=world_snapshot(store)
    for field,identity in (("resources","FOREIGN"),("offers","FOREIGN-OFFER"),("reservations","FOREIGN-RESERVATION")):
        assert next(item for item in after[field] if item["id"]==identity)==next(item for item in before[field] if item["id"]==identity)
    assert after["groups"][1]==before["groups"][1]


def test_flight_only_request_serialization_preserves_legacy_receipt_identity(package):
    store,*_=package
    request=RecoveryJobRequest(expected_version=store.read().version,idempotency_key="flight-only-replay",group_ids=["G1"])
    assert "hotel_recovery" not in request.model_dump(mode="json")


def test_stay_input_missing_or_contradictory_never_defaults_to_airport_wait(package):
    *_,inputs=package
    for changes in ({"minimum_rest_minutes":2000},{"airport_processing_minutes":True},{"initial_point":None}):
        with pytest.raises(ValueError): HotelRecoveryInputs.model_validate(inputs|changes)
