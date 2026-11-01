"""Allowlisted operator capabilities, using the same validated API handlers.

There is no model-selected URL, HTTP client, SQL or arbitrary Python execution.
The command endpoint authenticates the operator; this registry only admits
business routes protected by that same operator dependency.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import inspect
import json
import re
from typing import Annotated, Any, Literal

from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute
try:
    from fastapi.routing import iter_route_contexts
except ImportError:  # FastAPI versions that flatten included routers eagerly.
    def iter_route_contexts(routes):
        yield from routes
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model

from .models import Model
from .provider_budget import limit_calls
from .platform_evidence import require_external_evidence, require_report_evidence
from .providers.base import ProviderError
from .store import DomainError, Store, utcnow


ROUTE_NAMES = {
    ("GET", "/api/world"): "world.get",
    ("GET", "/api/audit"): "audit.list",
    ("POST", "/api/demo/cases"): "demo.create_case",
    ("POST", "/api/real-data/search"): "real_data.search",
    ("POST", "/api/flights/lookup"): "flights.lookup",
    ("POST", "/api/incidents"): "incidents.create",
    ("POST", "/api/incidents/{incident_id}/groups"): "incidents.bind_groups",
    ("POST", "/api/incidents/{incident_id}/arrival-report"): "incidents.report_arrival",
    ("POST", "/api/incidents/{incident_id}/airport-restrictions"): "incidents.report_airport_restriction",
    ("POST", "/api/incidents/{incident_id}/refresh-flight"): "incidents.refresh_flight",
    ("POST", "/api/incidents/{incident_id}/recovery-jobs"): "recovery.start",
    ("GET", "/api/incidents/{incident_id}/recovery-jobs"): "recovery.list",
    ("GET", "/api/incidents/{incident_id}/airport-transfer-profiles"): "profiles.airport_transfers",
    ("GET", "/api/incidents/{incident_id}/after-flight-transfer-profiles"): "profiles.after_flight_transfers",
    ("GET", "/api/incidents/{incident_id}/groups/{group_id}/romanian-hotel-recovery-profile"): "profiles.romanian_hotel",
    ("GET", "/api/recovery-jobs/{job_id}"): "recovery.get",
    ("POST", "/api/recovery-jobs/{job_id}/cancel"): "recovery.cancel",
    ("POST", "/api/live/import-passengers"): "passengers.import",
    ("POST", "/api/live/generate-passengers"): "passengers.generate",
    ("POST", "/api/live/groups/{group_id}/label"): "passengers.rename",
    ("POST", "/api/live/resources"): "resources.import",
    ("POST", "/api/live/resources/{resource_id}/unavailability"): "resources.report_unavailability",
    ("POST", "/api/live/hotel-quotes"): "hotels.import",
    ("POST", "/api/live/offers/{offer_id}/confirm"): "bookings.confirm",
    ("POST", "/api/live/travel/search"): "travel.search",
    ("POST", "/api/live/travel/import"): "travel.import",
    ("POST", "/api/events"): "events.apply",
    ("POST", "/api/replan"): "plans.replan",
    ("POST", "/api/reset"): "scenarios.reset",
    ("POST", "/api/traffic/start"): "traffic.start",
    ("POST", "/api/traffic/stop"): "traffic.stop",
    ("GET", "/api/airport-transfers/status"): "airport_transfers.status",
    ("POST", "/api/airport-transfers/quotes"): "airport_transfers.quote",
    ("POST", "/api/airport-transfers/quotes/{quote_id}/import"): "airport_transfers.import",
    ("GET", "/api/coaches/status"): "coaches.status",
    ("POST", "/api/coaches/search"): "coaches.search",
    ("GET", "/api/transfers/status"): "transfers.status",
    ("POST", "/api/transfers/quote"): "transfers.quote",
    ("POST", "/api/transfers/planning-quotes"): "transfers.planning_quote",
    ("POST", "/api/transfers/planning-quotes/{quote_id}/import"): "transfers.import",
    ("GET", "/api/groups/{group_id}/notifications"): "notifications.list",
    ("POST", "/api/groups/{group_id}/notifications"): "notifications.send",
    ("GET", "/api/inventory/resources/{resource_id}"): "inventory.get",
}
EXCLUDED = {"/api/commands", "/api/voice/session", "/api/stream"}
# Synthetic changes use the scenario event contracts, not live case/report APIs.
LIVE_OPERATION_PATHS = ("/api/live/", "/api/incidents", "/api/recovery-jobs", "/api/demo/")
SERVER_FIELDS = {"expected_version", "idempotency_key", "scenario_id"}
PRIVATE_FIELDS = {"token", "password", "secret", "authorization", "cookie", "headers", "sdp",
    "passenger_url", "passenger_link", "members", "recipient", "csv_text"}
_REFERENCE = re.compile(r"^\$(result\.\d+(?:\.[A-Za-z0-9_-]+)*|selection\.[a-z_]+)$")


class UINavigate(Model):
    view: Literal["airport", "region", "passenger", "map", "resources", "inspector", "plan", "log"]
    selected_id: str | None = Field(default=None, max_length=120)
    map_style: Literal["architectural", "geographic"] | None = None
    tab: Literal["flight", "bus", "groups", "hotel"] | None = None
    query: str | None = Field(default=None, max_length=300)


class UIOpen(Model):
    screen: Literal["settings", "help", "search", "live-data", "recovery-case", "recovery-picker", "scenario",
        "new-scenario", "demo-setup", "voice-review", "reset", "passenger", "real-data", "arrival", "restrictions", "recovery", "results"]
    tab: Literal["search", "planning_transfer", "generate", "passengers", "transport", "hotels", "confirm"] | None = None
    incident_id: str | None = Field(default=None, max_length=120)
    group_id: str | None = Field(default=None, max_length=120)
    query: str | None = Field(default=None, max_length=300)


class EmptyUI(Model):
    pass


class UITheme(Model):
    value: Literal["light", "dark"]


class UIPlanner(Model):
    value: Literal["ai", "solver"]


class UITimeline(Model):
    action: Literal["earlier", "later", "now", "zoom_in", "zoom_out"]


UI_MODELS = {"ui.navigate": UINavigate, "ui.open": UIOpen, "ui.close": EmptyUI,
    "ui.theme": UITheme, "ui.planner": UIPlanner, "ui.export": EmptyUI,
    "ui.refresh": EmptyUI, "ui.timeline": UITimeline}


def public_result(value, *, depth=0):
    """Bound UI/model receipts and remove credentials and passenger bearer data."""
    if depth > 14:
        return "[nested result omitted]"
    value = jsonable_encoder(value)
    if isinstance(value, dict):
        return {key: public_result(item, depth=depth+1) for key, item in value.items()
            if key.lower() not in PRIVATE_FIELDS and "api_key" not in key.lower()
            and not key.lower().endswith(("_token", "_secret", "_password")) and key != "world"}
    if isinstance(value, list):
        return [public_result(item, depth=depth+1) for item in value[:100]]
    if isinstance(value, str):
        value = re.sub(r"(/(?:api/)?(?:passenger|p)/)[^\s/?\"<>]+", r"\1[redacted]", value)
        return value[:12000]
    return value


def _schema(model):
    schema = model.model_json_schema()
    for name in SERVER_FIELDS:
        schema.get("properties", {}).pop(name, None)
    schema["required"] = [name for name in schema.get("required", []) if name not in SERVER_FIELDS]
    return schema


@dataclass
class Capability:
    name: str
    endpoint: Any
    model: type[BaseModel]
    body_name: str | None
    body_model: type[BaseModel] | None
    outer_fields: list[str]
    method: str
    path: str


class PlatformActions:
    def __init__(self, store, app, settings, *, status_provider=None):
        self.store, self.settings = store, settings
        self.capabilities = {}
        self._selection = ContextVar("platform_selection", default={})
        for route in iter_route_contexts(app.routes):
            if not isinstance(getattr(route, "original_route", route), APIRoute) or route.path in EXCLUDED:
                continue
            dependencies = route.dependant.dependencies
            if not dependencies or any(getattr(dep.call, "__name__", "") != "operator" for dep in dependencies):
                continue
            for method in route.methods & {"GET", "POST", "DELETE"}:
                name = ROUTE_NAMES.get((method, route.path))
                if name is None:
                    # New routes require an explicit public capability name.
                    continue
                fields = {}
                outer = route.dependant.path_params + route.dependant.query_params
                for field in outer:
                    info = deepcopy(field.field_info)
                    fields[field.name] = (info.annotation, info)
                body_name, body_model = None, None
                if route.dependant.body_params:
                    if len(route.dependant.body_params) != 1:
                        continue
                    body = route.dependant.body_params[0]
                    body_name, body_model = body.name, body.field_info.annotation
                    if not inspect.isclass(body_model) or not issubclass(body_model, BaseModel):
                        continue
                    fields.update({key: (field.annotation, deepcopy(field)) for key, field in body_model.model_fields.items()})
                model = create_model("Platform_" + name.replace(".", "_"),
                    __config__=ConfigDict(extra="forbid", allow_inf_nan=False), **fields)
                self.capabilities[name] = Capability(name, route.endpoint, model, body_name, body_model,
                    [field.name for field in outer], method, route.path)
        if status_provider is not None:
            self.capabilities["platform.status"] = Capability("platform.status", status_provider, EmptyUI,
                None, None, [], "GET", "/api/config")
        definitions, catalog = {}, []
        for name, model in [(item.name, item.model) for item in self.capabilities.values()] + list(UI_MODELS.items()):
            schema = _schema(model)
            for definition_name, definition in schema.pop("$defs", {}).items():
                if definition_name in definitions and definitions[definition_name] != definition:
                    raise ValueError("Conflicting platform parameter schema: " + definition_name)
                definitions[definition_name] = definition
            catalog.append({"name": name, "parameters": schema})
        # Shared definitions appear once, not once for each endpoint using them.
        self.catalog = {"capabilities": catalog, "$defs": definitions}
        with store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS platform_action_runs (
                request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, scenario_id TEXT NOT NULL,
                status TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def selection(self, request, world):
        scope = request.scope.model_dump(exclude_none=True) if getattr(request, "scope", None) else {}
        groups = {group.id: group for group in world.groups}
        cases = {case.id: case for case in world.incidents}
        known = {*groups, *cases, *(item.id for item in world.airports), *(item.id for item in world.resources)}
        if scope.get("selected_id") and scope["selected_id"] not in known:
            raise DomainError(409, "Selected item no longer exists; select it again")
        if scope.get("selected_id") in groups:
            scope.setdefault("group_id", scope["selected_id"])
        if scope.get("group_id"):
            group = groups.get(scope["group_id"])
            if group is None:
                raise DomainError(409, "Selected passenger group no longer exists")
            if group.incident_id:
                scope.setdefault("incident_id", group.incident_id)
            if scope.get("incident_id") and (group.incident_id != scope["incident_id"]
                    or scope["incident_id"] not in cases or group.id not in cases[scope["incident_id"]].group_ids):
                raise DomainError(409, "Selected passenger group and recovery case do not match")
        if scope.get("incident_id") and scope["incident_id"] not in cases:
            raise DomainError(409, "Selected recovery case no longer exists")
        return scope

    @contextmanager
    def context(self, request, world):
        token = self._selection.set(self.selection(request, world))
        try:
            yield
        finally:
            self._selection.reset(token)

    def available_in_mode(self, name, mode):
        capability = self.capabilities.get(name)
        return not (mode == "simulation" and capability
            and capability.path.startswith(LIVE_OPERATION_PATHS))

    def model_context(self, world=None):
        mode = world.get("mode") if world is not None else self.store.read().mode
        return {"current_selection": self._selection.get(), "current_utc": utcnow().isoformat(),
            **self.catalog, "operation_mode": mode,
            "capabilities": [item for item in self.catalog["capabilities"]
                if self.available_in_mode(item["name"], mode)],
            "reference_syntax": "$selection.group_id, $selection.incident_id, $selection.selected_id; "
                "$result.0.incident_id or $result.0.group_ids refer to the result of action zero"}

    def replay(self, request):
        with self.store.connect() as conn:
            row = conn.execute("SELECT fingerprint,scenario_id,status,response FROM platform_action_runs WHERE request_key=?",
                (request.idempotency_key,)).fetchone()
        if row is None:
            return None
        if row[0] != Store.fingerprint(request.model_dump()):
            raise DomainError(409, "This command key identifies different arguments")
        world = self.store.read()
        if world.scenario_id != row[1]:
            raise DomainError(409, "The operation changed after this command; it will not be repeated")
        response = json.loads(row[3])
        if row[2] != "completed":
            response.update(explanation="The previous command has an unconfirmed outcome. It was not automatically repeated. Review completed results before starting a new request.",
                outcome_unknown=True)
        return {**response, "world": world.model_dump(mode="json"), "replayed": True}

    def _save(self, request, world, response, status):
        with self.store.connect() as conn:
            conn.execute("UPDATE platform_action_runs SET scenario_id=?,status=?,response=? WHERE request_key=?",
                (world.scenario_id, status, json.dumps(response), request.idempotency_key))

    @staticmethod
    def _resolve(value, results, selection):
        if isinstance(value, str) and value.startswith(("$result.", "$selection.")):
            if not _REFERENCE.fullmatch(value):
                raise DomainError(422, "Invalid action result reference")
            parts = value[1:].split(".")
            current = selection if parts.pop(0) == "selection" else results
            try:
                for part in parts:
                    current = current[int(part)] if isinstance(current, list) else current[part]
            except (KeyError, IndexError, TypeError, ValueError):
                raise DomainError(422, "The requested selection or earlier result field is unavailable") from None
            return deepcopy(current)
        if isinstance(value, dict):
            return {key: PlatformActions._resolve(item, results, selection) for key, item in value.items()}
        if isinstance(value, list):
            return [PlatformActions._resolve(item, results, selection) for item in value]
        return value

    @staticmethod
    def _has_result_reference(value):
        if isinstance(value, str):
            return value.startswith("$result.")
        if isinstance(value, dict):
            return any(PlatformActions._has_result_reference(item) for item in value.values())
        if isinstance(value, list):
            return any(PlatformActions._has_result_reference(item) for item in value)
        return False

    def _arguments(self, name, arguments, world, selection, key, *, preflight=False):
        capability = self.capabilities.get(name)
        model = capability.model if capability else UI_MODELS[name]
        args = deepcopy(arguments)
        if any(field in args for field in SERVER_FIELDS):
            raise DomainError(422, "Version, operation and idempotency identifiers are supplied by the server")
        explicit_group = args.get("group_id")
        scoped = dict(selection)
        if explicit_group and explicit_group != selection.get("group_id"):
            group = next((item for item in world.groups if item.id == explicit_group), None)
            scoped = {"group_id": explicit_group, **({"incident_id": group.incident_id} if group and group.incident_id else {})}
        if "incident_id" in model.model_fields and "incident_id" not in args and scoped.get("incident_id"):
            args["incident_id"] = scoped["incident_id"]
        same_case = "incident_id" not in args or args["incident_id"] == scoped.get("incident_id")
        if "group_id" in model.model_fields and "group_id" not in args and same_case and scoped.get("group_id"):
            args["group_id"] = scoped["group_id"]
        if "resource_id" in model.model_fields and "resource_id" not in args and any(
                item.id == selection.get("selected_id") for item in world.resources):
            args["resource_id"] = selection["selected_id"]
        if "group_ids" in model.model_fields and "group_ids" not in args and same_case and scoped.get("group_id"):
            args["group_ids"] = [scoped["group_id"]]
        server = {"expected_version": world.version, "idempotency_key": key, "scenario_id": world.scenario_id}
        args.update({field: value for field, value in server.items() if field in model.model_fields})
        if preflight and self._has_result_reference(args):
            unknown = set(args) - set(model.model_fields)
            if unknown:
                raise DomainError(422, "Unexpected action arguments: " + ", ".join(sorted(unknown)))
            for field_name, field in model.model_fields.items():
                if field_name not in args:
                    if field.is_required():
                        raise DomainError(422, "Missing action input: " + field_name, {"missing_fields": [field_name]})
                elif not self._has_result_reference(args[field_name]):
                    TypeAdapter(Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation).validate_python(args[field_name])
            return args
        parsed = model.model_validate(args)
        args = parsed.model_dump()
        if name in UI_MODELS:
            self._validate_ui(name, args, world)
        if capability and capability.body_model:
            body = capability.body_model.model_validate({field: args[field] for field in capability.body_model.model_fields})
            return {**{field: args[field] for field in capability.outer_fields}, capability.body_name: body}
        return args

    @staticmethod
    def _validate_ui(name, args, world):
        groups, cases = {group.id: group for group in world.groups}, {case.id: case for case in world.incidents}
        selected = args.get("selected_id")
        if selected and selected not in {*groups, *cases, *(item.id for item in world.resources), *(item.id for item in world.airports)}:
            raise DomainError(422, "The requested interface item does not exist")
        group_id, incident_id = args.get("group_id"), args.get("incident_id")
        if group_id and group_id not in groups:
            raise DomainError(422, "The requested passenger group does not exist")
        if incident_id and incident_id not in cases:
            raise DomainError(422, "The requested recovery case does not exist")
        if group_id and incident_id and (groups[group_id].incident_id != incident_id or group_id not in cases[incident_id].group_ids):
            raise DomainError(422, "The requested passenger group does not belong to this case")
        screen = args.get("screen")
        if screen in {"new-scenario", "reset"} and world.mode != "simulation":
            raise DomainError(409, "Synthetic scenario editing is unavailable in this operation")
        if screen == "demo-setup" and not world.demo:
            raise DomainError(409, "Fictional case setup requires demo mode")

    @staticmethod
    def _failure(name, exc):
        if isinstance(exc, ValidationError):
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            missing = [".".join(map(str, error["loc"])) for error in errors if error["type"] == "missing"]
            return {"name": name, "status": "needs_input" if missing else "failed",
                "error": "Complete the required action inputs." if missing else "Action inputs failed validation.",
                "missing_fields": missing, "validation_errors": public_result(errors)}
        details = getattr(exc, "details", None) or {}
        missing = details.get("missing_fields", []) if isinstance(details, dict) else []
        return {"name": name, "status": "needs_input" if missing else "failed",
            "error": str(exc), **({"missing_fields": missing} if missing else {}),
            **({"result": details["platform_result"]} if isinstance(details, dict) and "platform_result" in details else {})}

    @staticmethod
    def _follow(name, args, result):
        if name in {"real_data.search", "flights.lookup", "travel.search", "hotels.import", "coaches.search",
                "transfers.quote", "transfers.planning_quote", "airport_transfers.quote", "inventory.get"}:
            return {"name": "ui.open", "arguments": {"screen": "results"}}
        incident_id = result.get("incident_id") or args.get("incident_id")
        job = result.get("job") or result.get("recovery_job")
        if isinstance(job, dict):
            incident_id = job.get("incident_id") or incident_id
        if incident_id and name not in {"recovery.list", "profiles.airport_transfers", "profiles.after_flight_transfers", "profiles.romanian_hotel"}:
            groups = result.get("group_ids") or args.get("group_ids") or []
            return {"name": "ui.open", "arguments": {"screen": "recovery-case", "incident_id": incident_id,
                **({"group_id": groups[0]} if len(groups) == 1 else {})}}
        group_id = result.get("group_id") or args.get("group_id")
        resources = result.get("resource_ids") or []
        selected_id = resources[0] if resources else group_id
        if selected_id:
            return {"name": "ui.navigate", "arguments": {"view": "inspector", "selected_id": selected_id}}
        return None

    @staticmethod
    def _summary(name, args, result):
        if name.startswith("ui."):
            return "Interface change requested; awaiting browser confirmation."
        if name == "demo.create_case":
            return (f"Created fictional case {result.get('incident_id')}: {args.get('passengers', 2)} passengers, "
                f"{args.get('origin', 'LHR')} → {args.get('destination', 'OTP')}; no travel inventory created.")
        job = result.get("job") or result.get("recovery_job") if isinstance(result, dict) else None
        if isinstance(job, dict):
            return f"Recovery job {job.get('id', '')}: {job.get('status', 'recorded')}. {job.get('message', '')}".strip()
        if isinstance(result, dict) and result.get("explanation"):
            return result["explanation"]
        if isinstance(result, dict):
            for field in ("items", "matches", "options", "jobs", "events"):
                if isinstance(result.get(field), list):
                    return f"{name}: {len(result[field])} {field} returned" + (f"; {result['state']}." if result.get("state") else ".")
            if name == "passengers.rename":
                return f"Renamed passenger group to {args.get('name', '')}."
            if name == "world.get":
                return f"Operation: {len(result.get('groups', []))} groups and {len(result.get('resources', []))} services."
            if name == "notifications.send":
                return f"Notification: {result.get('status', 'recorded')}; provider {result.get('provider', 'configured source')}."
        return f"{name} completed."

    @staticmethod
    def _limit_queued_recovery(capability, kwargs, available):
        if capability.body_name is None:
            return
        body = kwargs[capability.body_name]
        target = body if capability.name == "recovery.start" else getattr(body, "recovery", None)
        if target is None or not hasattr(target, "max_provider_calls"):
            return
        if available < 3:
            raise DomainError(422, "The remaining command allowance is below the recovery job minimum of three calls")
        revised = target.model_copy(update={"max_provider_calls": min(target.max_provider_calls, available)})
        kwargs[capability.body_name] = revised if target is body else body.model_copy(update={"recovery": revised})

    async def execute(self, request, world, actions):
        previous = self.replay(request)
        if previous:
            return previous
        from .live_commands import literal_call_limit
        stated_limit = literal_call_limit(request.text)
        total_limit = min(27, stated_limit) if stated_limit is not None else 8
        external_calls, recovery_reserved = 0, 0
        response = {"platform_results": [], "ui_actions": [], "applied": False, "mode": "ai",
            "command_interpretation_calls": 1, "platform_total_call_limit": total_limit}
        selection = self.selection(request, world)
        received_at = utcnow().isoformat()
        if world.mode == "live":
            with self.store.connect() as conn:
                row = conn.execute("SELECT created_at FROM live_command_intents WHERE request_key=? AND fingerprint=? AND scenario_id=?",
                    ("command:" + request.idempotency_key, Store.fingerprint(request.model_dump()), world.scenario_id)).fetchone()
            if row:
                received_at = row[0]
        prepared = []
        try:
            if not isinstance(actions, list) or not 1 <= len(actions) <= 6:
                raise DomainError(422, "A command supports one to six platform actions")
            for item in actions:
                if not isinstance(item, dict) or set(item) != {"name", "arguments_json"}:
                    raise DomainError(422, "Invalid platform action envelope")
                name = item["name"]
                if not isinstance(name, str) or name not in {*self.capabilities, *UI_MODELS}:
                    raise DomainError(422, "Unknown or unavailable platform action")
                if not self.available_in_mode(name, world.mode):
                    raise DomainError(422, "This action requires a live operation; simulation changes use scenario events")
                if not isinstance(item["arguments_json"], str) or len(item["arguments_json"]) > 32000:
                    raise DomainError(422, "Action arguments must be a bounded JSON object")
                try:
                    args = json.loads(item["arguments_json"])
                except (ValueError, RecursionError):
                    raise DomainError(422, "Action arguments must be valid JSON") from None
                if not isinstance(args, dict):
                    raise DomainError(422, "Action arguments must be a JSON object")
                # Selection values are stable, while result values become available in order.
                probe = self._resolve_selection(args, selection)
                step_key = "platform-" + Store.fingerprint({"command": request.idempotency_key, "step": len(prepared)})
                require_external_evidence(name, probe, request.text, selection)
                require_report_evidence(name, probe, request, world, selection, received_at, preflight=True)
                checked = self._arguments(name, probe, world, selection, step_key, preflight=True)
                capability = self.capabilities.get(name)
                if capability and capability.body_name and not self._has_result_reference(probe):
                    grounded = {key: value for key, value in checked.items() if key != capability.body_name}
                    grounded.update(checked[capability.body_name].model_dump())
                    require_report_evidence(name, grounded, request, world, selection, received_at)
                prepared.append((name, probe, step_key))
        except (DomainError, ValidationError) as exc:
            response["platform_results"].append(self._failure(item.get("name", "invalid") if isinstance(locals().get("item"), dict) else "invalid", exc))
            response["explanation"] = response["platform_results"][-1]["error"] + " No platform actions were executed."
            return self.store.remember("command:"+request.idempotency_key, request.model_dump(), world.version, response)

        with self.store.connect() as conn:
            conn.execute("INSERT INTO platform_action_runs VALUES(?,?,?,?,?,?)", (request.idempotency_key,
                Store.fingerprint(request.model_dump()), world.scenario_id, "running", json.dumps(response), utcnow().isoformat()))
        results = []
        expected_version, scenario_id = world.version, world.scenario_id
        for name, original_args, step_key in prepared:
            budget = None
            try:
                current = self.store.read()
                if current.version != expected_version or current.scenario_id != scenario_id:
                    raise DomainError(409, "Operation changed between actions; remaining actions were not executed")
                args = self._resolve(original_args, results, selection)
                kwargs = self._arguments(name, args, current, selection, step_key)
                if name in UI_MODELS:
                    ui = {"name": name, "arguments": UI_MODELS[name].model_validate(kwargs).model_dump(exclude_none=True)}
                    response["ui_actions"].append(ui)
                    result = {"requested": True, "message": "Interface action requested; the browser confirms its outcome."}
                else:
                    capability = self.capabilities[name]
                    grounded_args = {**kwargs}
                    if capability.body_name:
                        grounded_args.pop(capability.body_name, None)
                        grounded_args.update(kwargs[capability.body_name].model_dump())
                    require_external_evidence(name, args, request.text, selection)
                    grounding_selection = dict(selection)
                    group_reference = original_args.get("group_ids")
                    if isinstance(group_reference, str):
                        created_reference = re.fullmatch(r"\$result\.(\d+)\.group_ids", group_reference)
                        if created_reference and int(created_reference[1]) < len(results):
                            source_index = int(created_reference[1])
                            if prepared[source_index][0] in {"demo.create_case", "passengers.generate", "passengers.import"}:
                                grounding_selection["created_group_ids"] = results[source_index].get("group_ids")
                    require_report_evidence(name, grounded_args, request, current, grounding_selection, received_at)
                    allowance = max(0, total_limit - 1 - external_calls - recovery_reserved)
                    self._limit_queued_recovery(capability, kwargs, allowance)
                    if name == "traffic.start" and stated_limit is not None:
                        raise DomainError(422, "Continuous traffic polling uses its separate shared budget; a finite command allowance cannot authorize an ongoing feed")
                    if name == "notifications.send":
                        body = kwargs[capability.body_name]
                        if body.recipient.casefold() not in request.text.casefold():
                            raise DomainError(422, "State the exact notification recipient in the current request")
                    with limit_calls(allowance) as budget:
                        if inspect.iscoroutinefunction(capability.endpoint):
                            raw = await capability.endpoint(**kwargs)
                        else:
                            # These handlers perform short, atomic local operations.
                            # A detached worker could commit after a timeout releases
                            # the command claim and reports an unknown outcome.
                            raw = capability.endpoint(**kwargs)
                    raw = jsonable_encoder(raw)
                    result = public_result(raw)
                    if isinstance(result, dict) and (result.get("state") in {"error", "not_configured", "sandbox", "blocked", "unknown_outcome"}
                            or (name == "notifications.send" and result.get("status") in {"failed", "unknown", "blocked", "unknown_outcome"})):
                        raise DomainError(422, result.get("message") or result.get("error_message") or "The source did not return usable live results",
                            {"platform_result": result})
                    job = result.get("job") or result.get("recovery_job") if isinstance(result, dict) else None
                    if isinstance(job, dict) and (name == "recovery.start" or getattr(kwargs.get(capability.body_name), "recovery", None) is not None):
                        recovery_reserved += job.get("max_provider_calls", 0)
                    changed_world = raw.get("world") if isinstance(raw, dict) else None
                    if isinstance(changed_world, dict):
                        expected_version = changed_world["version"]
                        scenario_id = changed_world["scenario_id"]
                        response["applied"] |= expected_version != current.version or scenario_id != current.scenario_id
                    elif capability.method != "GET":
                        # Jobs, polling and notifications have durable effects outside World.
                        response["applied"] |= name in {"recovery.start", "recovery.cancel", "traffic.start", "traffic.stop", "notifications.send"}
                    if isinstance(result, dict):
                        for field in ("incident_id", "focus_id", "report", "reports"):
                            if result.get(field) is not None:
                                response[field] = result[field]
                        if isinstance(job, dict):
                            response["recovery_job"] = job
                            response["incident_id"] = job.get("incident_id") or response.get("incident_id")
                    follow = self._follow(name, args, result) if isinstance(result, dict) else None
                    if follow:
                        response["ui_actions"].append(follow)
                results.append(result)
                response["platform_results"].append({"name": name, "status": "succeeded", "result": result,
                    "summary": self._summary(name, args, result)})
                self._save(request, self.store.read(), response, "running")
            except (DomainError, ProviderError, ValidationError) as exc:
                response["platform_results"].append(self._failure(name, exc))
                break
            except asyncio.CancelledError:
                response["platform_results"].append({"name": name, "status": "failed",
                    "error": "The command was interrupted during this action; its final outcome is unconfirmed. It will not be repeated automatically."})
                response["outcome_unknown"] = True
                response["explanation"] = "The command was interrupted. Completed actions remain recorded; review their results and the current state."
                response.update(platform_external_calls=external_calls + (budget.used if budget is not None else 0),
                    recovery_calls_reserved=recovery_reserved)
                self._save(request, self.store.read(), response, "interrupted")
                raise
            except Exception:
                response["platform_results"].append({"name": name, "status": "failed",
                    "error": "The action outcome could not be confirmed. It will not be automatically repeated."})
                response["outcome_unknown"] = True
                break
            finally:
                external_calls += budget.used if budget is not None else 0
        current = self.store.read()
        failure = next((item for item in response["platform_results"] if item["status"] != "succeeded"), None)
        summaries = [item["summary"] for item in response["platform_results"] if item["status"] == "succeeded" and not item["name"].startswith("ui.")]
        response["explanation"] = " ".join(summaries) if summaries else "No server action completed."
        if response.get("outcome_unknown"):
            response["explanation"] = (" ".join(summaries) + " The last action's outcome is unconfirmed; review the current state.").strip()
        if failure:
            response["explanation"] += " " + failure["error"] + " Remaining actions were not executed."
        if response["ui_actions"]:
            response["explanation"] += " Related interface updates were requested."
        response.update(platform_external_calls=external_calls, recovery_calls_reserved=recovery_reserved)
        self._save(request, current, response, "completed")
        return {**response, "world": current.model_dump(mode="json"), "replayed": False}

    @staticmethod
    def _resolve_selection(value, selection):
        if isinstance(value, str) and value.startswith("$selection."):
            return PlatformActions._resolve(value, [], selection)
        if isinstance(value, dict):
            return {key: PlatformActions._resolve_selection(item, selection) for key, item in value.items()}
        if isinstance(value, list):
            return [PlatformActions._resolve_selection(item, selection) for item in value]
        return value
