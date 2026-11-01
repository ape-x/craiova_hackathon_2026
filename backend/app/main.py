from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
import sqlite3
from contextlib import asynccontextmanager, suppress
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .models import AcceptRequest, CommandRequest, ConstraintRequest, EventRequest, LoginRequest, Model, Mutation, ReplanRequest, World
from .providers import AIProvider, FlightProvider, ProviderError, VoiceProvider
from .real_data import RealDataRequest, RealDataService
from .live_data import ManifestImport, ManualResourceInput, PassengerGenerationInput
from .live_service import LiveOperations, HotelQuoteImport, ExternalConfirmation, GroupLabelUpdate
from .live_travel import LiveTravel, LiveTravelSearch, TravelQuoteImport
from .providers.travel import TravelProvider
from .recovery_jobs import RecoveryJobs, RecoveryJobRequest, RecoveryCancellation
from .live_commands import LiveCommands
from .ground_transfers import GroundTransfers, create_transfer_router
from .coach_fares import CoachFares, create_coach_router
from .transfer_planning import GroundPlanning, create_planning_transfer_router
from .incidents import FlightContextSelection, FlightLookupRequest, IncidentCreate, IncidentGroupBinding, IncidentService
from .service import RecoveryService
from .store import DomainError, Store, utcnow
from .voice_context import VoiceScope, build_voice_context
from .operational_reports import ArrivalReportInput, OperationalReports, ResourceUnavailabilityInput
from .airport_restrictions import AirportRestrictionReportInput, AirportRestrictions
from .airport_cab_planning import AirportCabPlanning, create_airport_cab_router
from .inventory_history import create_inventory_history_router


class VoiceRequest(Model):
    sdp: str = Field(min_length=10,max_length=65536,pattern=r"^v=0\r?\n")
    scope: VoiceScope | None = None


class RedactAccessPaths(logging.Filter):
    """Passenger links are bearer credentials; do not write them into access logs."""
    def filter(self,record):
        if isinstance(record.args,tuple) and len(record.args)==5 and isinstance(record.args[2],str):
            args=list(record.args)
            path=args[2].split("?",1)[0]
            args[2]=re.sub(r"(/(?:api/)?(?:passenger|p)/)[^/]+",r"\1[redacted]",path)
            record.args=tuple(args)
        return True


class BodySizeLimit:
    """Bound JSON/SDP input even when an HTTP client omits Content-Length."""
    def __init__(self,app,max_bytes):
        self.app,self.max_bytes=app,max_bytes

    async def __call__(self,scope,receive,send):
        if scope["type"]!="http" or scope["method"] not in ("POST","PUT","PATCH"):
            return await self.app(scope,receive,send)
        messages=[]
        size=0
        while True:
            message=await receive()
            messages.append(message)
            if message["type"]=="http.disconnect":
                return
            size+=len(message.get("body",b""))
            if size>self.max_bytes:
                return await JSONResponse({"detail":"Request body too large"},status_code=413)(scope,receive,send)
            if not message.get("more_body",False):
                break
        index=0
        async def replay():
            nonlocal index
            if index<len(messages):
                message=messages[index];index+=1
                return message
            return await receive()
        return await self.app(scope,replay,send)


def create_app(settings: Settings | None=None) -> FastAPI:
    settings=settings or Settings()
    access_logger=logging.getLogger("uvicorn.access")
    if not any(isinstance(item,RedactAccessPaths) for item in access_logger.filters):
        access_logger.addFilter(RedactAccessPaths())
    store=Store(settings.database_path,settings.observation_retention_seconds,settings.operation_mode,settings.fr24_result_limit*8)
    ai,flights,voice=AIProvider(settings),FlightProvider(settings,usage_store=store),VoiceProvider(settings)
    incidents=IncidentService(settings,store,flights)
    real_data=RealDataService(settings)
    service=RecoveryService(settings,store,ai if ai.configured else None)
    live=LiveOperations(store,service,real_data)
    travel_provider=TravelProvider(settings)
    travel=LiveTravel(store,travel_provider)
    ground_transfers=GroundTransfers(settings)
    coach_fares=CoachFares(settings,fx=ground_transfers.fx)
    airport_cab=AirportCabPlanning(settings,store)
    jobs=RecoveryJobs(settings,store,service,travel_provider,ai,hotel_provider=real_data.provider,
        apl_provider=ground_transfers.provider,fx_provider=ground_transfers.fx,coach_fares=coach_fares,airport_cab=airport_cab)
    service.live_commands=LiveCommands(store,jobs)
    ground_planning=GroundPlanning(store,ground_transfers,real_data.provider)
    operational_reports=OperationalReports(store,jobs)
    airport_restrictions=AirportRestrictions(store,jobs)
    login_attempts=defaultdict(deque)
    # Reserve worst-case credit cost in the persistent shared request ledger;
    # restarting or adding a worker cannot replenish the paid provider budget.
    poll_request_limit=settings.fr24_max_requests

    async def flight_loop():
        while True:
            try:
                await asyncio.to_thread(store.purge_observations)
                await asyncio.to_thread(ground_planning.purge)
                await asyncio.to_thread(airport_cab.purge)
            except sqlite3.Error:
                logging.getLogger(__name__).warning("Observation cache cleanup could not access the database")
                await asyncio.sleep(1)
                continue
            if flights.configured and await asyncio.to_thread(store.control,"traffic_running",False):
                try:
                    observations=await flights.fetch()
                    await asyncio.to_thread(store.set_observations,observations,{"state":"connected","source":"Flightradar24","updated_at":utcnow().isoformat(),"environment":settings.fr24_environment})
                except ProviderError as exc:
                    if exc.code!="poll_interval":
                        old=await asyncio.to_thread(store.read)
                        await asyncio.to_thread(store.set_observations,old.observations,{"state":"error","message":str(exc),"code":exc.code,"updated_at":utcnow().isoformat()})
                        if exc.code in ("budget_exhausted","http_401","http_403","http_429"):
                            await asyncio.to_thread(store.set_control,"traffic_running",False)
                except Exception:
                    logging.getLogger(__name__).exception("Observation polling stopped after an unexpected failure")
                    await asyncio.to_thread(store.set_control,"traffic_running",False)
                    old=await asyncio.to_thread(store.read)
                    await asyncio.to_thread(store.set_observations,old.observations,{"state":"error","message":"Observation polling stopped after an internal error; inspect server logs.","updated_at":utcnow().isoformat()})
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(flight_loop())
        jobs.start_worker()
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await jobs.aclose()
        await asyncio.gather(ai.aclose(),flights.aclose(),voice.aclose(),real_data.aclose(),travel_provider.aclose(),ground_transfers.aclose(),coach_fares.aclose(),airport_cab.aclose())

    app=FastAPI(title="Airport Recovery Live",version="0.1.0",lifespan=lifespan,docs_url=None if settings.production else "/docs",redoc_url=None,openapi_url=None if settings.production else "/openapi.json")
    app.state.store,app.state.service,app.state.settings=store,service,settings
    app.state.real_data=real_data
    app.state.live=live
    app.state.travel=travel
    app.state.incidents=incidents
    app.state.jobs=jobs
    app.state.live_commands=service.live_commands
    app.state.ground_transfers=ground_transfers
    app.state.coach_fares=coach_fares
    app.state.ground_planning=ground_planning
    app.state.airport_cab=airport_cab
    app.state.operational_reports=operational_reports
    app.state.airport_restrictions=airport_restrictions
    app.add_middleware(CORSMiddleware,allow_origins=settings.origins,allow_credentials=True,allow_methods=["GET","POST","DELETE"],allow_headers=["Content-Type","Authorization","Last-Event-ID"])
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",")])
    app.add_middleware(BodySizeLimit,max_bytes=settings.max_request_bytes)

    def session_cookie():
        payload=f"{int(time.time())}.{secrets.token_hex(12)}"
        digest=hmac.new(settings.operator_token.encode(),payload.encode(),hashlib.sha256).hexdigest()
        store.set_control("session:"+digest,int(time.time())+settings.session_ttl_seconds)
        return payload+"."+digest

    def valid_session(value):
        try:
            issued,nonce,digest=value.split(".")
            age=time.time()-int(issued)
            expected=hmac.new(settings.operator_token.encode(),f"{issued}.{nonce}".encode(),hashlib.sha256).hexdigest()
            return 0<=age<=settings.session_ttl_seconds and hmac.compare_digest(digest.encode(),expected.encode()) and bool(store.control("session:"+digest,False))
        except (ValueError,AttributeError,TypeError):
            return False

    def authenticated(request: Request):
        if not settings.operator_token:
            return True
        bearer=request.headers.get("authorization","")
        if bearer.startswith("Bearer ") and secrets.compare_digest(bearer[7:].encode(),settings.operator_token.encode()):
            return True
        return valid_session(request.cookies.get("recovery_session",""))

    def operator(request: Request):
        if not authenticated(request):
            raise DomainError(401,"Operator authentication required")

    app.include_router(create_transfer_router(ground_transfers,operator))
    app.include_router(create_coach_router(coach_fares,operator))
    app.include_router(create_planning_transfer_router(ground_planning,operator))
    app.include_router(create_airport_cab_router(airport_cab,operator))
    app.include_router(create_inventory_history_router(store,operator))

    @app.middleware("http")
    async def security_headers(request,call_next):
        if request.method in ("POST","PUT","PATCH","DELETE"):
            origin=request.headers.get("origin")
            if origin and origin.rstrip("/") not in settings.origins:
                return JSONResponse({"detail":"Origin is not allowed"},status_code=403)
            try:
                if int(request.headers.get("content-length","0"))>settings.max_request_bytes:
                    return JSONResponse({"detail":"Request body too large"},status_code=413)
            except ValueError:
                return JSONResponse({"detail":"Invalid Content-Length"},status_code=400)
        response=await call_next(request)
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        response.headers["Cache-Control"]="no-store"
        if settings.production:
            response.headers["Strict-Transport-Security"]="max-age=31536000"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request,exc):
        headers={"Retry-After":str(exc.details["retry_after_seconds"])} if isinstance(exc.details,dict) and exc.details.get("in_progress") else None
        return JSONResponse({"detail":exc.message,"details":exc.details,"applied":exc.applied},status_code=exc.status,headers=headers)

    @app.exception_handler(ProviderError)
    async def provider_error(request,exc):
        return JSONResponse({"detail":str(exc),"provider":exc.provider,"code":exc.code,"applied":False},status_code=503 if exc.code=="not_configured" else 502)

    @app.exception_handler(ValidationError)
    async def invalid_provider_output(request,exc):
        # Internal structured provider validation is an upstream error, never a partial apply.
        return JSONResponse({"detail":"Structured command failed schema validation; no changes were applied","applied":False},status_code=502)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request,exc):
        # FastAPI's default errors include raw input/ctx: tokens and SDP would
        # be reflected, and NaN input cannot be serialized by JSONResponse.
        # Unknown property names can also contain credentials, so only expose
        # known schema locations and fixed local descriptions.
        fields={"body","query","path","header","expected_version","idempotency_key","type","target_id","value","mode","text","token","deadline","accessible","budget","sdp","offer_id"}
        errors=[]
        for error in exc.errors():
            location=[part if isinstance(part,int) or part in fields else "unknown_field" for part in error.get("loc",())]
            errors.append({"loc":location,"msg":"Request field failed validation","type":"request_validation"})
        return JSONResponse({"detail":errors,"applied":False},status_code=422)

    @app.get("/api/health")
    def health():
        return {"status":"ok","service":"airport-recovery-live","simulation":settings.operation_mode=="simulation"}

    @app.get("/api/ready")
    def ready():
        try:
            with store.connect() as conn:
                row=conn.execute("SELECT data FROM world WHERE id=1").fetchone()
            if not row:
                raise sqlite3.DatabaseError("Authoritative world is missing")
            World.model_validate_json(row[0])
        except (sqlite3.Error,ValidationError):
            return JSONResponse({"status":"unavailable","database":"unavailable"},status_code=503)
        return {"status":"ready","database":"connected","simulation":settings.operation_mode=="simulation"}

    @app.get("/api/config")
    def config(request: Request):
        return {"environment":settings.environment,"auth_required":bool(settings.operator_token),"authenticated":authenticated(request),
            "ai_configured":ai.configured,"voice_configured":voice.configured,"fr24_configured":flights.configured,
            "google_maps_api_key":settings.google_maps_api_key,"public_base_url":settings.public_base_url,
            "hold_ttl_seconds":settings.hold_ttl_seconds,"models":{"reasoning":settings.openai_model,"decision":settings.openai_decision_model,"voice":settings.openai_voice_model},
            "traffic_running":store.control("traffic_running",False),"traffic_requests_used":store.poll_usage(),"traffic_request_budget":poll_request_limit,
            "traffic_credit_budget":settings.fr24_max_credits,"traffic_credits_reserved":store.fr24_credits_reserved(),
            "observation_retention_seconds":settings.observation_retention_seconds,
            "real_data":{"hotels":"Google Places" if real_data.provider.google_configured else "OpenStreetMap",
                         "transport":"Google Routes" if real_data.provider.google_configured else "OSRM / OpenStreetMap",
                         "hotel_offers_configured":real_data.provider.hotel_offers_configured,
                         "hotel_offers_source":real_data.provider.hotel_offer_source,
                         "hotel_offers_environment":None if real_data.provider.hotel_offer_provider is None else "sandbox" if real_data.provider.hotel_offer_provider == "liteapi" and real_data.provider.liteapi_sandbox else "live"},
            "travel_sources":{"flights":{"source":travel_provider.flight_source,"configured":travel_provider.flight_configured,"environment":travel_provider.flight_environment},
                              "coaches":{"source":"Distribusion","configured":travel_provider.ground_configured,"environment":settings.distribusion_environment}},
            "simulation_only":settings.operation_mode=="simulation","operation_mode":settings.operation_mode,"solver_optimality":"bounded heuristic; global optimality not proven"}

    @app.post("/api/auth/login")
    def login(body: LoginRequest,request: Request,response: Response):
        ip=request.client.host if request.client else "unknown"
        attempts=login_attempts[ip]
        now=time.monotonic()
        while attempts and attempts[0]<now-60:
            attempts.popleft()
        if len(attempts)>=10:
            raise DomainError(429,"Too many login attempts; wait one minute")
        attempts.append(now)
        if settings.operator_token and not secrets.compare_digest(body.token.encode(),settings.operator_token.encode()):
            raise DomainError(401,"Invalid operator token")
        response.set_cookie("recovery_session",session_cookie(),httponly=True,secure=settings.production,samesite="strict",max_age=settings.session_ttl_seconds,path="/api")
        return {"authenticated":True}

    @app.post("/api/auth/logout")
    def logout(request: Request,response: Response):
        cookie=request.cookies.get("recovery_session","")
        if valid_session(cookie):
            store.set_control("session:"+cookie.split(".")[-1],None)
        response.delete_cookie("recovery_session",path="/api")
        return {"authenticated":False}

    def world_response():
        world=store.read()
        world.provider_status.update({"ai":{"configured":ai.configured,"state":"configured_unverified" if ai.configured else "not_configured"},"voice":{"configured":voice.configured},"traffic_running":store.control("traffic_running",False)})
        return world

    @app.get("/api/world",dependencies=[Depends(operator)])
    def world():
        return world_response()

    @app.get("/api/audit",dependencies=[Depends(operator)])
    def audit_history():
        return {"events":store.audit_history()}

    @app.post("/api/real-data/search",dependencies=[Depends(operator)])
    async def search_real_data(body: RealDataRequest):
        return await real_data.search(body)

    @app.post("/api/flights/lookup",dependencies=[Depends(operator)])
    async def flight_lookup(body: FlightLookupRequest):
        return await incidents.lookup(body)

    @app.post("/api/incidents",dependencies=[Depends(operator)])
    def incident_create(body: IncidentCreate):
        return incidents.create(body)

    @app.post("/api/incidents/{incident_id}/groups",dependencies=[Depends(operator)])
    def incident_groups(incident_id: str,body: IncidentGroupBinding):
        return incidents.bind(incident_id,body)

    @app.post("/api/incidents/{incident_id}/arrival-report",dependencies=[Depends(operator)])
    def incident_arrival_report(incident_id: str,body: ArrivalReportInput):
        return operational_reports.arrival(incident_id,body)

    @app.post("/api/incidents/{incident_id}/airport-restrictions",dependencies=[Depends(operator)])
    def incident_airport_restriction(incident_id: str,body: AirportRestrictionReportInput):
        return airport_restrictions.report(incident_id,body)

    @app.post("/api/incidents/{incident_id}/refresh-flight",dependencies=[Depends(operator)])
    def incident_refresh(incident_id: str,body: FlightContextSelection):
        return incidents.refresh(incident_id,body)

    @app.post("/api/incidents/{incident_id}/recovery-jobs",dependencies=[Depends(operator)],status_code=202)
    def recovery_job_start(incident_id: str,body: RecoveryJobRequest):
        return jobs.start(incident_id,body)

    @app.get("/api/incidents/{incident_id}/recovery-jobs",dependencies=[Depends(operator)])
    def recovery_job_list(incident_id: str):
        return jobs.list(incident_id)

    @app.get("/api/recovery-jobs/{job_id}",dependencies=[Depends(operator)])
    def recovery_job_get(job_id: str):
        return jobs.get(job_id)

    @app.post("/api/recovery-jobs/{job_id}/cancel",dependencies=[Depends(operator)])
    def recovery_job_cancel(job_id: str,body: RecoveryCancellation):
        return jobs.cancel(job_id,body)

    @app.post("/api/live/import-passengers",dependencies=[Depends(operator)])
    def import_passengers(body: ManifestImport):
        return live.manifest(body)

    @app.post("/api/live/generate-passengers",dependencies=[Depends(operator)])
    def generate_test_passengers(body: PassengerGenerationInput):
        return live.generate(body)

    @app.post("/api/live/groups/{group_id}/label",dependencies=[Depends(operator)])
    def label_passenger_group(group_id: str,body: GroupLabelUpdate):
        return live.label_group(group_id,body)

    @app.post("/api/live/resources",dependencies=[Depends(operator)])
    def add_verified_resource(body: ManualResourceInput):
        return live.resource(body)

    @app.post("/api/live/resources/{resource_id}/unavailability",dependencies=[Depends(operator)])
    def resource_unavailability_report(resource_id: str,body: ResourceUnavailabilityInput):
        return operational_reports.unavailable(resource_id,body)

    @app.post("/api/live/hotel-quotes",dependencies=[Depends(operator)])
    async def add_hotel_quotes(body: HotelQuoteImport):
        return await live.hotels(body)

    @app.post("/api/live/offers/{offer_id}/confirm",dependencies=[Depends(operator)])
    def confirm_external_bookings(offer_id: str, body: ExternalConfirmation):
        return live.confirm(offer_id,body)

    @app.post("/api/live/travel/search",dependencies=[Depends(operator)])
    async def search_travel(body: LiveTravelSearch):
        return await travel.search(body)

    @app.post("/api/live/travel/import",dependencies=[Depends(operator)])
    def import_travel_quote(body: TravelQuoteImport):
        return travel.import_quote(body)

    @app.get("/api/stream",dependencies=[Depends(operator)])
    async def stream(request: Request):
        async def events():
            last=request.headers.get("last-event-id","")
            heartbeat=0
            while not await request.is_disconnected():
                if not authenticated(request):
                    break
                snapshot=await asyncio.to_thread(world_response)
                payload=snapshot.model_dump_json()
                signature=f"{snapshot.version}-"+hashlib.sha256(payload.encode()).hexdigest()[:12]
                if signature!=last:
                    yield f"id: {signature}\nevent: world\ndata: {payload}\n\n"
                    last=signature
                elif heartbeat%15==0:
                    yield ": heartbeat\n\n"
                heartbeat+=1
                # A revoked/expired operator session closes the stream as well.
                await asyncio.sleep(1)
        return StreamingResponse(events(),media_type="text/event-stream",headers={"X-Accel-Buffering":"no","Cache-Control":"no-cache"})

    @app.post("/api/events",dependencies=[Depends(operator)])
    def scenario_event(body: EventRequest):
        return service.event(body)

    @app.post("/api/replan",dependencies=[Depends(operator)])
    async def replan(body: ReplanRequest):
        return await service.replan(body)

    @app.post("/api/commands",dependencies=[Depends(operator)])
    async def command(body: CommandRequest):
        return await service.command(body)

    @app.post("/api/reset",dependencies=[Depends(operator)])
    def reset(body: Mutation):
        return service.reset(body)

    @app.get("/api/passenger/{token}")
    def passenger(token: str):
        return service.passenger(token)

    @app.post("/api/passenger/{token}/constraints")
    def constraints(token: str,body: ConstraintRequest):
        return service.constraints(token,body)

    @app.post("/api/offers/{offer_id}/accept")
    def accept(offer_id: str,body: AcceptRequest):
        return service.accept(offer_id,body)

    @app.post("/api/offers/{offer_id}/decline")
    def decline(offer_id: str,body: AcceptRequest):
        return service.decline(offer_id,body.token,body.idempotency_key)

    @app.post("/api/voice/session",dependencies=[Depends(operator)])
    async def voice_session(body: VoiceRequest):
        if body.scope is None:
            return await voice.create(body.sdp)
        context=build_voice_context(await asyncio.to_thread(store.read),body.scope)
        return await voice.create(body.sdp,reference_context=context)

    @app.post("/api/traffic/start",dependencies=[Depends(operator)])
    def traffic_start():
        if not flights.configured:
            raise DomainError(503,"Flightradar24 API key is not configured. No observed traffic is available.")
        if store.poll_usage()>=poll_request_limit or store.fr24_credits_reserved()+settings.fr24_result_limit*8>settings.fr24_max_credits:
            raise DomainError(409,"Shared Flightradar24 request/credit budget exhausted")
        store.set_control("traffic_running",True)
        return {"running":True,"poll_seconds":settings.flight_poll_seconds,"requests_used":store.poll_usage(),"max_requests":poll_request_limit}

    @app.post("/api/traffic/stop",dependencies=[Depends(operator)])
    def traffic_stop():
        store.set_control("traffic_running",False)
        return {"running":False,"requests_used":store.poll_usage()}

    return app


app=create_app()
