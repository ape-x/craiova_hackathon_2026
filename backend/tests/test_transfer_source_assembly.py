"""Separately recorded source results assemble without clients, services or SQL."""
import copy
from datetime import timedelta

import httpx
import pytest

from app.store import Store
from app.transfer_planning import (
    GroundPlanning, assess_transfer_sources, prepare_transfer_inputs,
    prepare_transfer_response,
)
from test_transfer_planning import setup
from test_transfer_return import returning


async def completed_sources(fixture):
    store,sources,service,request,clock=fixture[:5]
    body=request()
    manual=await service.quote(body)
    context=service._context(store.read(),body)
    hotel=manual["hotel"]
    metadata={"latitude":hotel["latitude"],"longitude":hotel["longitude"],
        "postal_address":{key:hotel[key] for key in ("hotel_id","name","address","city","country","source","retrieved_at")}}
    raw=copy.deepcopy(manual["transfer"])
    raw.pop("fx")
    for option in raw["options"]:
        option.pop("eur_reference_estimate")
    return sources,clock,context,manual,metadata,raw


@pytest.mark.asyncio
@pytest.mark.parametrize("direction",["initial_airport_to_hotel","hotel_to_airport"])
async def test_pure_separate_source_assembly_matches_manual_response_without_any_constructor(request,monkeypatch,direction):
    fixture=request.getfixturevalue("setup" if direction=="initial_airport_to_hotel" else "returning")
    sources,clock,context,manual,metadata,raw=await completed_sources(fixture)
    snapshots=copy.deepcopy([context,metadata,raw,manual])
    def forbidden(*args,**kwargs):
        raise AssertionError("Pure source assembly must not construct clients/services or connect SQL")
    monkeypatch.setattr(GroundPlanning,"__init__",forbidden)
    monkeypatch.setattr(Store,"connect",forbidden)
    monkeypatch.setattr(httpx,"AsyncClient",forbidden)
    prepared=prepare_transfer_inputs(context,metadata)
    assert prepared["blockers"]==[]
    assert prepared["apl_request"]["expected_direction"]==("to_hub" if direction=="hotel_to_airport" else "from_hub")
    assert prepared["route_request"]["origin"]==manual["route"]["request"]["origin"]
    assert prepared["route_request"]["destination"]==manual["route"]["request"]["destination"]
    response=assess_transfer_sources(context,prepare_transfer_response(context,manual["quote_id"],now=clock[0]),
        hotel_metadata=metadata,apl_quote=raw,fx_enrichment=manual["transfer"],dated_route=manual["route"],now=clock[0])
    assert response==manual
    assert [context,metadata,raw,manual]==snapshots
    assert len(sources.calls)==3
    # Reassessment must not append duplicate options or extend receipt lifetimes.
    assert assess_transfer_sources(prepared["context"],response,now=clock[0])==response
    assert "quote_sequence" not in prepared["context"]  # Only a durable caller allocates it.


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code",[("missing","property_metadata_unavailable"),("identity","property_address_missing"),
    ("coordinates","property_location_changed"),("address","property_address_missing"),("country","property_outside_scope")])
async def test_bad_property_metadata_never_prepares_apl_or_route_dispatch(setup,change,code):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    if change=="missing": metadata=None
    if change=="identity": metadata["postal_address"]["hotel_id"]="other-property"
    if change=="coordinates": metadata["longitude"]+=.01
    if change=="address": metadata["postal_address"]["address"]=None
    if change=="country": metadata["postal_address"]["country"]="FR"
    prepared=prepare_transfer_inputs(context,metadata)
    assert prepared["apl_request"] is None and prepared["route_request"] is None
    assert code in {item["code"] for item in prepared["blockers"]}
    assert "apl_request" not in context and len(sources.calls)==3


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["price","option","receipt","request"])
async def test_fx_enrichment_from_a_different_apl_receipt_cannot_override_the_source(setup,change):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    enriched=copy.deepcopy(manual["transfer"])
    if change=="price": enriched["options"][0]["price"]["amount"]="1.00"
    if change=="option": enriched["options"][0]["id"]="different-car"
    if change=="receipt": enriched["received_at"]=(clock[0]-timedelta(seconds=1)).isoformat()
    if change=="request": enriched["request"]["suitcases"]+=1
    response=assess_transfer_sources(context,prepare_transfer_response(context,"job-step-1",now=clock[0]),
        hotel_metadata=metadata,apl_quote=raw,fx_enrichment=enriched,dated_route=manual["route"],now=clock[0])
    assert response["state"]=="blocked" and response["transfer"]==raw
    assert "fx_quote_mismatch" in {item["code"] for item in response["blockers"]}
    assert all(not item["importable"] for item in response["options"])
    assert len(sources.calls)==3


@pytest.mark.asyncio
async def test_missing_or_failed_ecb_keeps_gbp_and_blocks_estimated_budget_import(setup):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    failed_fx=copy.deepcopy(raw)
    failed_fx["fx"]={"state":"unavailable","code":"fx_timeout"}
    for option in failed_fx["options"]: option["eur_reference_estimate"]=None
    for enrichment in (None,failed_fx):
        response=assess_transfer_sources(context,prepare_transfer_response(context,"job-step-1",now=clock[0]),
            hotel_metadata=metadata,apl_quote=raw,fx_enrichment=enrichment,dated_route=manual["route"],now=clock[0])
        assert response["state"]=="blocked"
        assert response["transfer"]["options"][0]["price"]==raw["options"][0]["price"]
        assert all("reference_price_unavailable" in {reason["code"] for reason in item["blockers"]} for item in response["options"])
    assert len(sources.calls)==3


@pytest.mark.asyncio
async def test_failed_route_preserves_completed_transfer_and_explicit_error_without_retry(setup):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    errors=[{"code":"route_timeout","message":"Recorded route dispatch timed out; outcome unknown."}]
    response=assess_transfer_sources(context,prepare_transfer_response(context,"job-step-1",now=clock[0]),
        hotel_metadata=metadata,apl_quote=raw,fx_enrichment=manual["transfer"],errors=errors,now=clock[0])
    assert response["state"]=="blocked" and response["route"] is None
    assert response["transfer"]==manual["transfer"] and response["blockers"]==errors
    assert len(sources.calls)==3


@pytest.mark.asyncio
async def test_an_enriched_quote_cannot_stand_in_for_an_absent_apl_dispatch(setup):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    response=assess_transfer_sources(context,prepare_transfer_response(context,"job-step-1",now=clock[0]),
        hotel_metadata=metadata,fx_enrichment=manual["transfer"],dated_route=manual["route"],now=clock[0])
    assert response["state"]=="blocked" and response["transfer"] is None
    assert {"fx_quote_mismatch","transfer_unavailable"}<={item["code"] for item in response["blockers"]}


@pytest.mark.asyncio
async def test_reconciled_hotel_evidence_cannot_be_omitted_from_assembly(setup):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    prepared=prepare_transfer_inputs(context,metadata)
    response=assess_transfer_sources(prepared["context"],prepare_transfer_response(context,"job-step-1",now=clock[0]),
        apl_quote=raw,fx_enrichment=manual["transfer"],dated_route=manual["route"],now=clock[0])
    assert response["state"]=="blocked" and response["hotel"] is None
    assert "property_metadata_unavailable" in {item["code"] for item in response["blockers"]}


@pytest.mark.asyncio
async def test_old_google_result_is_not_ready_even_when_apl_and_fx_are_current(setup):
    sources,clock,context,manual,metadata,raw=await completed_sources(setup)
    route=copy.deepcopy(manual["route"])
    route["fetched_at"]=(clock[0]-timedelta(seconds=121)).isoformat()
    response=assess_transfer_sources(context,prepare_transfer_response(context,"job-step-1",now=clock[0]),
        hotel_metadata=metadata,apl_quote=raw,fx_enrichment=manual["transfer"],dated_route=route,now=clock[0])
    assert response["state"]=="blocked"
    assert "source_receipt_expired" in {item["code"] for item in response["blockers"]}
    assert all(not item["importable"] for item in response["options"])
    assert len(sources.calls)==3
