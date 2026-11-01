"""Ground model-proposed evidence in the current operator statement.

The general capability dispatcher must preserve the narrower voice report
boundary: model output and assistant history are never supplier evidence.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json
import re
from types import SimpleNamespace

from .store import DomainError


REPORTS = {"incidents.report_arrival", "incidents.report_airport_restriction", "resources.report_unavailability"}


def _literal(value, text):
    if isinstance(value, bool):
        return re.search(r"\b" + str(value).lower() + r"\b", text, re.IGNORECASE) is not None
    if isinstance(value, (int, float, Decimal)):
        numbers = re.findall(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?![\w.])", text)
        return any(Decimal(candidate) == Decimal(str(value)) for candidate in numbers)
    if isinstance(value, datetime):
        value = value.isoformat()
    if not isinstance(value, str) or not value or value.startswith(("$result.", "$selection.")):
        return False
    variants = {value, json.dumps(value, ensure_ascii=False)[1:-1], json.dumps(value)[1:-1]}
    if value.endswith("+00:00"):
        variants.add(value[:-6] + "Z")
    elif value.endswith("Z"):
        variants.add(value[:-1] + "+00:00")
    return any(re.search((r"(?<!\w)" if candidate[0].isalnum() else "") + re.escape(candidate)
                         + (r"(?!\w)" if candidate[-1].isalnum() else ""), text)
               is not None for candidate in variants)


def require_external_evidence(name, args, text, selection=None):
    selection = selection or {}
    fields = []
    if name == "bookings.confirm":
        for index, booking in enumerate(args.get("bookings", [])):
            if isinstance(booking, dict):
                fields.extend((f"bookings.{index}.{field}", booking.get(field))
                              for field in ("confirmation_reference", "confirmed_by", "confirmed_at"))
    elif name in {"resources.import", "passengers.import"}:
        fields.extend((field, args.get(field)) for field in
                      ("source_name", "source_reference", "confirmed_by", "observed_at"))
        if name == "passengers.import":
            fields.append(("csv_text", args.get("csv_text")))
        # Ground operational values too: a real source name cannot authorize
        # invented seats, prices, routes or passenger details.
        fields.extend((field, value) for field, value in args.items() if value is not None
                      and field not in {item[0] for item in fields}
                      and not (field in {"incident_id", "group_id"} and value == selection.get(field))
                      and field not in {"expected_version", "scenario_id", "idempotency_key"})
    forbidden_instruction = (r"\b(?:do not|don't|never|nu)\s+(?:\w+\s+){0,2}(?:confirm\w*|record\w*|import\w*|book\w*|inregistr\w*)\b"
                             r"|\b(?:if|unless|daca|dacă)\b[^.;\n]*\b(?:confirm\w*|record\w*|import\w*|book\w*|inregistr\w*)\b")
    if fields and re.search(forbidden_instruction, text, re.IGNORECASE):
        raise DomainError(422, "The current request does not affirmatively authorize recording this evidence.",
                          {"missing_fields": ["current_evidence_instruction"]})
    missing = [field for field, value in fields if not _literal(value, text)]
    # A pasted record binds each value to its own field, not merely to an
    # unrelated occurrence (e.g. a capacity invented from a year in a date).
    structured = None
    evidence_key = "bookings" if name == "bookings.confirm" else "source_reference"
    if fields:
        decoder = json.JSONDecoder()
        for start in list(re.finditer(r"\{", text))[:64]:
            try:
                record, _ = decoder.raw_decode(text[start.start():])
            except ValueError:
                continue
            if isinstance(record, dict) and evidence_key in record:
                structured = record
                break
    if structured is not None:
        def equivalent(left, right):
            if isinstance(left, (int, float, Decimal)) and not isinstance(left, bool):
                return isinstance(right, (int, float, Decimal)) and not isinstance(right, bool) and Decimal(str(left)) == Decimal(str(right))
            return left == right
        if name == "bookings.confirm":
            if args.get("bookings") != structured.get("bookings"):
                missing.append("bookings")
        else:
            missing.extend(field for field, value in fields if field not in structured or not equivalent(value, structured[field]))
    elif name in {"resources.import", "passengers.import"}:
        numeric_labels = {"capacity": r"capacity|capacitate|seats?|locuri|passengers?|pasageri",
                          "unit_price_eur": r"price|pret|preț|cost|eur|euro", "group_budget_eur": r"budget|buget|eur|euro"}
        for field, value in fields:
            if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
                label = numeric_labels.get(field, re.escape(field))
                number = re.escape(str(value)) + (r"(?:\.0+)?" if Decimal(str(value)) == int(value) else "")
                if not re.search(r"(?:\b(?:" + label + r")\b[\s:=\"]{0,10}" + number
                                 + r"(?![\w.])|(?<![\w.])" + number + r"\s*(?:" + label + r")\b)", text, re.IGNORECASE):
                    missing.append(field)
    if missing:
        raise DomainError(422, "Supply the actual external evidence in the current request; assistant suggestions cannot establish a booking, manifest or service.",
                          {"missing_fields": missing})


def _airport_reference(claim, code):
    from .airport_mentions import literal_airport_ids
    from .airport_catalog import _COMMON_WORDS
    words = list(re.finditer(r"[^\W_]+", claim))
    # Prefer the literal code, then the shortest unambiguous literal name.
    candidates = []
    for index, word in enumerate(words):
        for last in words[index:index + 10]:
            fragment = claim[word.start():last.end()]
            if len(fragment) < 3 or (len(fragment) <= 4 and fragment.casefold() in _COMMON_WORDS):
                continue
            if literal_airport_ids(fragment) == {code}:
                candidates.append(fragment)
    if not candidates:
        raise ValueError("State the airport explicitly in the current report.")
    return min(candidates, key=len)


def _ready_at(claim, proposed, airport_reference, received_at):
    from .live_commands import _DURATION_NUMBERS, _read_reference_date, normalized
    from .live_operational_commands import _readiness
    value = normalized(claim)
    markers = list(re.finditer(r"\b(?:ready|readiness|pregatit[ai]?|pregatiti|pregatite|gata|disponibili)\b", value))
    if len(markers) != 1:
        raise ValueError("State one explicit common passenger readiness time.")
    marker = markers[0]
    matches = []
    for start in re.finditer(r"\b\w", value[marker.end():]):
        position = marker.end() + start.start()
        tail = re.sub(r"(?<=\d)t(?=\d{2}:)", " ", value[position:])
        try:
            date = _read_reference_date(tail)
        except ValueError:
            date = None
        if date:
            clock = re.match(r"\s*(?:(?:at|la|ora)\s+)?\d{2}:\d{2}(?::00)?\s*(?:z|utc|gmt|[+-]\d{2}:\d{2})(?!\w)", tail[date[1]:])
            if clock:
                matches.append((position, position + date[1] + clock.end()))
    durations = r"\b(?:in|peste)\s+(?:[0-9]+|" + "|".join(re.escape(word) for word in _DURATION_NUMBERS) + r")\s+(?:de\s+)?(?:minutes?|minute)\b"
    matches.extend((match.start(), match.end()) for match in re.finditer(durations, value))
    matches = sorted(set(matches))
    if len(matches) != 1 or len(re.findall(r"\d{1,2}:\d{2}", value)) > (2 if re.search(r"[+-]\d{2}:\d{2}", value) else 1):
        raise ValueError("State one complete readiness date/time with UTC or an offset, or a duration in minutes.")
    start, end = matches[0]
    between = value[marker.end():start].replace(normalized(airport_reference), " ")
    between = re.sub(r"\b(?:at|in|on|la|pe|ora|from|de)\b", " ", between)
    if between.strip(" ,:") or start < marker.end():
        raise ValueError("The stated time must describe passenger readiness, not a flight or another event.")
    # Only grammar and the already-validated airport are removed. The timestamp
    # itself remains literal, and the existing parser compares its exact instant.
    quote = value[start:end]
    relative = re.fullmatch(durations, quote) is not None
    actual, _ = _readiness("ready at " + quote, None if relative else
                          (proposed.isoformat() if isinstance(proposed, datetime) else proposed), quote, received_at)
    return actual


def require_report_evidence(name, args, request, world, selection, received_at, *, preflight=False):
    if name not in REPORTS:
        return
    from .live_operational_commands import (
        Clarification, _airport, _claim, _fragment, _readiness_subject, _resource,
        _require_complete_primitive_report, _unsupported_airport_change,
    )
    from .live_commands import normalized
    try:
        kind = {"incidents.report_arrival": "arrival_report", "resources.report_unavailability": "resource_unavailability",
                "incidents.report_airport_restriction": "airport_restriction"}[name]
        description = args.get("description")
        claim = (_fragment(request.text, description)[0] if kind == "airport_restriction"
                 else _claim(request.text, description, kind))
        if preflight:
            # Even a result-dependent action must ground its facts before any
            # earlier action commits. Scope is checked after references resolve.
            if kind == "airport_restriction":
                from .live_airport_reports import _NEGATIVE_OR_CONDITIONAL
                prefix = request.text[:request.text.casefold().find(claim.casefold())]
                prefix = re.split(r"[.!?;\n]", prefix)[-1]
                if re.search(_NEGATIVE_OR_CONDITIONAL, normalized(prefix)):
                    raise Clarification("State the current airport restriction affirmatively.")
            if kind == "arrival_report":
                _airport(claim, args.get("airport_code"), claim)
                reference = _airport_reference(claim, args.get("airport_code"))
                args["ready_at"] = _ready_at(claim, args.get("ready_at"), reference, received_at)
            return
        case = next((item for item in world.incidents if item.id == args.get("incident_id")), None)
        if kind == "airport_restriction":
            from .live_airport_reports import ground_airport_restriction
            from .providers.ai import LiveAirportRestrictionReport
            if case is None:
                raise Clarification("Choose an existing recovery case for the airport report.")
            report = LiveAirportRestrictionReport(type=kind, incident_id=case.id,
                airport_code=args["airport_code"], airport_reference=_airport_reference(claim, args["airport_code"]),
                operations=args["operations"], action=args["action"], report_reference=claim)
            ground_airport_restriction(world, request, report, already_bound_case=case)
        elif kind == "arrival_report":
            if case is None:
                raise Clarification("Choose an existing recovery case for the passenger report.")
            _airport(claim, args.get("airport_code"), claim)
            reference = _airport_reference(claim, args.get("airport_code"))
            args["ready_at"] = _ready_at(claim, args.get("ready_at"), reference, received_at)
            scoped_claim = claim
            if args.get("group_ids") == [selection.get("group_id")]:
                scoped_claim = re.sub(r"\b(?:the\s+)?(?:selected|this)\s+(?:passenger\s+)?group\b|\bgrupul\s+(?:selectat|acesta)\b|\bacest\s+grup\b",
                                      selection["group_id"], scoped_claim, flags=re.IGNORECASE)
            created = selection.get("created_group_ids")
            if created and args.get("group_ids") == created:
                pattern = (r"\b(?:the\s+)?newly created group\b|\bgrupul (?:nou creat|creat acum)\b" if len(created) == 1 else
                           r"\b(?:the\s+)?newly created groups\b|\bgrupurile (?:nou create|create acum)\b")
                scoped_claim = re.sub(pattern, " and ".join(created), scoped_claim, flags=re.IGNORECASE)
            _readiness_subject(world, case, scoped_claim, args["group_ids"])
            if _unsupported_airport_change(request.text):
                raise Clarification("Report the airport restriction separately; passenger readiness does not establish airport availability.")
        else:
            resource = next((item for item in world.resources if item.id == args.get("resource_id")), None)
            if resource is None:
                raise Clarification("Choose an existing service for the unavailability report.")
            scoped_claim = claim
            if resource.id == selection.get("selected_id"):
                scoped_claim = re.sub(r"\b(?:the\s+)?(?:selected|this)\s+(?:service|bus|flight|resource)\b|\b(?:serviciul|autobuzul|zborul|resursa)\s+(?:selectat[aă]?|acesta)\b",
                                      resource.id, scoped_claim, flags=re.IGNORECASE)
            references = [value for value in (resource.id, resource.name) if value.casefold() in scoped_claim.casefold()]
            _resource(world, scoped_claim, resource.id, references[0] if references else None)
        _require_complete_primitive_report(world, request.text, SimpleNamespace(type=kind, report_reference=claim))
    except (Clarification, ValueError, KeyError) as exc:
        raise DomainError(422, str(exc), {"missing_fields": ["current_report_assertion"]}) from exc
