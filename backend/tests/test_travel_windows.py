"""Bounded HTTP fixtures: filter eligible journeys before the public result cap."""
import copy
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from app.providers.travel import TravelProvider
from test_travel import flight_data, ground_data, nuitee_data, query, refs, settings


CONFIG = {
    "nuitee":{"liteapi_api_key":"prod_mock_only","liteapi_environment":"live"},
    "duffel":{"duffel_access_token":"duffel_live_mock_only","duffel_environment":"live"},
    "distribusion":{"distribusion_api_key":"mock_only","distribusion_environment":"live","distribusion_offers_enabled":True},
}


def times():
    departure = datetime.combine(query().departure_date,time(12),ZoneInfo("Europe/Bucharest"))
    return departure,departure+timedelta(hours=4)


def payload(provider, rows):
    """Rows are (price, departure, arrival); shared routes have several fares."""
    if provider == "duffel":
        result = flight_data()
        base = result["data"]["offers"][0]
        result["data"]["offers"] = []
        for index,(price,departure,arrival) in enumerate(rows):
            offer=copy.deepcopy(base)
            offer.update(id=f"offer-{index}",total_amount=str(price))
            offer["slices"][0]["segments"][0].update(departing_at=departure.isoformat(),arriving_at=arrival.isoformat())
            result["data"]["offers"].append(offer)
        return result
    grouped={}
    for index,(price,departure,arrival) in enumerate(rows):
        grouped.setdefault((departure,arrival),[]).append((index,price))
    if provider == "nuitee":
        result=nuitee_data()
        base=result["data"][0]["journeys"][0]
        result["data"][0]["journeys"]=[]
        for route_index,((departure,arrival),fares) in enumerate(grouped.items()):
            journey=copy.deepcopy(base)
            journey["journeyKey"]=f"journey-{route_index}"
            journey["segments"][0].update(departureTime=departure.isoformat(),arrivalTime=arrival.isoformat())
            journey["offers"]=[]
            for index,price in fares:
                offer=copy.deepcopy(base["offers"][0])
                offer["offerId"]=f"offer-{index}"
                offer["pricing"]["display"].update(total=price,base=price,taxes=0,fees=0)
                journey["offers"].append(offer)
            result["data"][0]["journeys"].append(journey)
        return result
    result=ground_data()
    connection,segment,fare=result["data"][0],result["included"][0],result["included"][1]
    result["data"],result["included"]=[],result["included"][2:]
    for route_index,((departure,arrival),fares) in enumerate(grouped.items()):
        source_segment=copy.deepcopy(segment)
        source_segment["id"]=f"segment-{route_index}"
        source_segment["attributes"].update(departure_time=departure.isoformat(),arrival_time=arrival.isoformat())
        source_connection=copy.deepcopy(connection)
        source_connection["id"]=f"connection-{route_index}"
        source_connection["relationships"].update(segments=refs("segments",source_segment["id"]),
            fares=refs("fares",*(f"fare-{index}" for index,_ in fares)))
        result["data"].append(source_connection)
        result["included"].append(source_segment)
        for index,price in fares:
            source_fare=copy.deepcopy(fare)
            source_fare["id"]=f"fare-{index}"
            source_fare["attributes"].update(offer_id=f"offer-{index}",price=price*100,segment_ids=[source_segment["id"]])
            result["included"].append(source_fare)
    return result


async def search(provider, rows, **bounds):
    calls=[]
    def respond(request):
        body=json.loads(request.content)
        calls.append(body)
        # These are local selection criteria, never undocumented supplier fields.
        assert "earliest_departure_at" not in json.dumps(body)
        assert "latest_arrival_at" not in json.dumps(body)
        return httpx.Response(200,json=payload(provider,rows))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result=await TravelProvider(settings(**CONFIG[provider]),client=client).search(query("bus" if provider=="distribusion" else "flight",**bounds))
    assert len(calls)==1 and result["state"]=="ready"
    return result["items"],calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider",CONFIG)
async def test_more_than_ten_cheap_late_fares_do_not_hide_the_feasible_offer(provider):
    departure,arrival=times()
    rows=[(price,departure,arrival+timedelta(days=1)) for price in range(1,13)]+[(500,departure,arrival)]
    bounded,body=await search(provider,rows,latest_arrival_at=arrival)
    assert [(item["provider_offer_id"],item["total_price"]) for item in bounded]==[("offer-12","500")]
    unbounded,previous_body=await search(provider,rows)
    assert len(unbounded)==10 and [item["provider_offer_id"] for item in unbounded]==[f"offer-{index}" for index in range(10)]
    assert body==previous_body


@pytest.mark.asyncio
@pytest.mark.parametrize("provider",CONFIG)
async def test_early_fares_are_also_filtered_before_the_result_limit(provider):
    departure,arrival=times()
    rows=[(price,departure-timedelta(minutes=30),arrival) for price in range(1,13)]+[(500,departure,arrival)]
    items,_=await search(provider,rows,earliest_departure_at=departure)
    assert [item["provider_offer_id"] for item in items]==["offer-12"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider",CONFIG)
@pytest.mark.parametrize("earliest_delta,latest_delta,expected",[(0,0,1),(1,0,0),(0,-1,0)])
async def test_exact_inclusive_bounds_use_instants_instead_of_local_clocks(provider,earliest_delta,latest_delta,expected):
    departure,arrival=times()
    earliest=(departure+timedelta(microseconds=earliest_delta)).astimezone(timezone(timedelta(hours=-5)))
    latest=(arrival+timedelta(microseconds=latest_delta)).astimezone(timezone(timedelta(hours=9)))
    items,_=await search(provider,[(100,departure,arrival)],earliest_departure_at=earliest.isoformat(),latest_arrival_at=latest.isoformat())
    assert len(items)==expected


@pytest.mark.parametrize("field",["earliest_departure_at","latest_arrival_at"])
@pytest.mark.parametrize("value",["2030-01-01T12:00:00",datetime(2030,1,1,12),True,1900000000,"not-a-time"])
def test_time_bounds_reject_naive_or_implicitly_coerced_values(field,value):
    with pytest.raises(ValidationError):
        query(**{field:value})


def test_bound_order_is_explicit_and_absent_bounds_preserve_optional_contract():
    departure,arrival=times()
    with pytest.raises(ValidationError):
        query(earliest_departure_at=arrival,latest_arrival_at=departure)
    assert query().earliest_departure_at is query().latest_arrival_at is None
    bounded=query(earliest_departure_at=departure,latest_arrival_at=arrival)
    assert bounded.earliest_departure_at==departure.astimezone(timezone.utc)
    assert bounded.latest_arrival_at==arrival.astimezone(timezone.utc)
