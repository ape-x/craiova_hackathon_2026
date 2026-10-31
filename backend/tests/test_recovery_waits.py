"""Live waiting disclosures use local dates; no unstated rest policy is inferred."""
from datetime import datetime, timezone

import pytest

from app.models import Airport, Allocation, Plan, Strategy
from app.recovery_jobs import flight_query_options, journey_waits
from app.solver import build_candidate, itinerary_options, validate_allocations
from test_recovery_scope import expiry, itinerary, resource, world


@pytest.mark.parametrize("code,lat,lon,ready,departure,overnight",[
    ("CRA",44.3181,23.8886,1410,1450,True),
    ("LHR",51.4706,-.4619,1370,1460,False),
    ("LHR",51.4706,-.4619,1500,1980,True),
])
def test_live_calendar_boundary_does_not_invent_mandatory_hotel(code,lat,lon,ready,departure,overnight):
    state=world()
    state.epoch="2030-01-01T00:00:00+02:00"
    state.sim_time=ready
    state.budget=1000
    state.airports.append(Airport(id=code,code=code,name=code,lat=lat,lon=lon)) if code not in {a.id for a in state.airports} else None
    node=next(item for item in state.airports if item.id==code)
    node.lat,node.lon=lat,lon
    group=state.groups[0]
    group.origin,group.destination,group.ready_at,group.deadline=code,"OTP",ready,departure+300
    state.resources=[resource("F1",kind="flight",origin=code,departure=departure,arrival=departure+120)]
    candidate=build_candidate(state,Strategy(),scope_group_ids={group.id})
    assert candidate.recovered_passengers==2 and candidate.validation["valid"]
    waits=journey_waits(state,candidate)
    assert len(waits)==1 and waits[0]["duration_minutes"]==departure-ready
    assert waits[0]["overnight"] is overnight and waits[0]["accommodation_included"] is False
    assert waits[0]["timezone"]==("Europe/Bucharest" if code=="CRA" else "Europe/London")


def test_six_leg_live_bound_includes_a_complete_five_segment_property_chain():
    state=world()
    state.budget=1000
    state.groups[0].destination="LTN"
    state.groups[0].deadline=2200
    state.airports.append(Airport(id="HOTEL",code="HOTEL",name="Real property",kind="hotel",lat=44.56,lon=26.1))
    state.resources=[resource("TO_OTP",destination="OTP",departure=650,arrival=800),
        resource("TO_PROPERTY",origin="OTP",destination="HOTEL",departure=820,arrival=840),
        resource("STAY",kind="hotel",origin="HOTEL",destination="HOTEL",departure=870,arrival=1800),
        resource("TO_AIRPORT",origin="HOTEL",destination="OTP",departure=1820,arrival=1840),
        resource("FLIGHT",kind="flight",origin="OTP",destination="LTN",departure=1900,arrival=2100)]
    assert validate_allocations(state,[Allocation(group_id="GNEW",itinerary=itinerary(state.resources))],scope_group_ids={"GNEW"})["valid"]
    assert any([leg.resource_id for leg in item.legs]==[r.id for r in state.resources] for item in itinerary_options(state,state.groups[0]))


def test_internal_supplier_connection_wait_is_disclosed_in_its_actual_timezone():
    state=world()
    state.budget=1000
    state.groups[0].deadline=3000
    state.resources=[resource("CONNECTED",kind="flight",departure=700,arrival=2600,provenance={"segments":[
        {"origin":"CRA","destination":"LHR","departure_at":"2030-01-01T12:00:00Z","arrival_at":"2030-01-01T23:00:00Z"},
        {"origin":"LHR","destination":"OTP","departure_at":"2030-01-02T07:00:00Z","arrival_at":"2030-01-02T09:00:00Z"}]})]
    plan=build_candidate(state,Strategy(),scope_group_ids={"GNEW"})
    waits=journey_waits(state,plan)
    connection=next(item for item in waits if item["location_id"]=="LHR")
    assert connection["timezone"]=="Europe/London" and connection["overnight"] is True
    assert connection["duration_minutes"]==480 and connection["accommodation_included"] is False


def test_origin_local_search_date_accepts_los_angeles_after_bucharest_midnight(monkeypatch):
    import app.travel as travel
    instant=datetime(2030,1,2,1,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    monkeypatch.setattr(travel,"datetime",Clock)
    request=travel.TravelSearchRequest(kind="flight",origin_code="LAX",destination_code="JFK",departure_date="2030-01-01",adults=1)
    assert request.departure_date.isoformat()=="2030-01-01"
    with pytest.raises(ValueError):
        travel.TravelSearchRequest(kind="flight",origin_code="LAX",destination_code="JFK",departure_date="2029-12-31",adults=1)


def test_live_nuitee_property_node_guard_rejects_legacy_airport_attachment():
    from app.solver import resource_feasible
    state=world()
    hotel=resource("H",kind="hotel",origin="CRA",destination="CRA",departure=700,arrival=1600,
        source="Nuitee / LiteAPI",quote_total_cost=90,budget_total_cost=100,quote_rooms=1,
        provenance={"pricing":{"status":"complete","mandatory_total":{"amount":"100","currency":"EUR"}}})
    group=state.groups[0]
    assert not resource_feasible(state,hotel,group,"CRA",650,set())
    node=Airport(id="HP-FIXTURE",code="HP-FIXTURE",name="Actual property",kind="hotel",lat=44.5,lon=26.1)
    state.airports.append(node)
    hotel.origin=hotel.destination=node.id
    hotel.provenance["property_location"]={"node_id":node.id,"latitude":node.lat,"longitude":node.lon}
    assert resource_feasible(state,hotel,group,node.id,650,set())
    hotel.provenance["property_location"]["longitude"]=25.0
    assert not resource_feasible(state,hotel,group,node.id,650,set())
