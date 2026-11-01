"""Exactly two grounded operator assertions, committed before one scoped search.

This is a passenger-recovery report, not aircraft control. Restriction and arrival
retain separate provenance, while their transaction, call cap and receipt remain
one. No provider is called here and no passenger fact is inferred from a flight.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from .airport_restrictions import apply_airport_restriction_report, enqueue_airport_restriction_recovery
from .live_airport_reports import (
    _CLOSED, _INABILITY, _LIFT, _NEGATIVE_OR_CONDITIONAL, _OPEN, _RECOVERY_START,
    _RESTRICT, ground_airport_restriction,
)
from .live_commands import normalized
from .live_operational_commands import (
    Clarification, RecoveryBlocked, _airport, _case, _claim, _fragment, _groups,
    _job_request, _readiness, _readiness_subject, _recovery_requested, _total_limit,
    _unsupported_airport_change,
)
from .operational_reports import ArrivalReportInput, apply_arrival_report
from .providers.ai import LiveAirportRestrictionAndArrival, LiveRecoveryIntent
from .store import DomainError, Store


_EXTRA_REPORT = (
    r"\b(?:ready|readiness|arrived|landed|sosit|ajuns|aterizat|pregatit\w*|gata|"
    r"disponibili|unavailable|broken|broke|out of service|defect\w*|stricat\w*|"
    r"indisponibil\w*|nu (?:mai )?(?:functioneaza|circula|poate (?:functiona|circula)))\b"
)
_OUTAGE = r"\b(?:unavailable|broken|broke|out of service|defect\w*|stricat\w*|indisponibil\w*|nu (?:mai )?(?:functioneaza|circula|poate (?:functiona|circula)))\b"
_OTHER_ASSERTION = r"\b(?:ready|readiness|arrived|landed|sosit|ajuns|aterizat|pregatit\w*|gata|disponibili|broken|broke|out of service|defect\w*|stricat\w*)\b"
_CONDITIONAL = r"\b(?:if|whether|unless|when|whenever|once|while|provided|except\w*|would|could|might|maybe|perhaps|probably|possibly|daca|cand|odata ce|o data ce|indata ce|imediat ce|cat timp|poate|eventual|probabil)\b"


def _unique_span(text, quote):
    claim, start = _fragment(text, quote)
    matches = list(re.finditer(re.escape(claim), text, re.IGNORECASE))
    if len(matches) != 1:
        raise Clarification("Citează fiecare declarație o singură dată; referințele repetate nu stabilesc un raport unic.")
    matched = matches[0]
    return matched.group(), matched.start(), matched.end()


def _mask(text, *spans):
    for _, start, end in sorted(spans, key=lambda item: item[1], reverse=True):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def _arrival_context(text, span):
    """Keep unquoted prefix/tail qualifiers; the grounded restriction is blank."""
    claim, start, end = span
    boundary = max(text.rfind(char, 0, start) for char in ".!?;\n") + 1
    sentence_end = min((position for char in ".!?;\n" if (position := text.find(char, end)) >= 0), default=len(text))
    tail = normalized(text[end:sentence_end])
    followup = re.search(_RECOVERY_START, tail)
    if followup:
        tail = tail[:followup.start()]
    context = normalized(text[boundary:start] + " " + claim) + " " + tail
    if re.search(_NEGATIVE_OR_CONDITIONAL, context) or "?" in claim or text[end:].lstrip().startswith("?"):
        raise Clarification("Pregătirea pasagerilor trebuie afirmată explicit, fără negație, întrebare sau condiție comună celor două declarații.")


def route_diversion_reports(commands, request, world, interpreted):
    try:
        bundle = LiveAirportRestrictionAndArrival.model_validate(interpreted["operational_report"], strict=True)
        if interpreted.get("events") or interpreted.get("strategies"):
            raise Clarification("Cele două rapoarte nu se combină cu modificări sau planuri globale; nimic nu a fost aplicat.")
        restriction_span = _unique_span(request.text, bundle.restriction.report_reference)
        arrival_span = _unique_span(request.text, bundle.arrival.report_reference)
        if max(restriction_span[1], arrival_span[1]) < min(restriction_span[2], arrival_span[2]):
            raise Clarification("Restricția și pregătirea pasagerilor necesită două declarații distincte, fără citate suprapuse.")
        from .after_flight_commands import saved_after_flight_instruction
        if any(_recovery_requested(span[0]) or saved_after_flight_instruction(span[0]) for span in (restriction_span, arrival_span)):
            raise Clarification("Separă citatele celor două rapoarte de cererea de recuperare; nu omit condiții din căutare.")
        case = _case(world, request.text, bundle.restriction.incident_id)
        arrival_case = _case(world, request.text, bundle.arrival.incident_id)
        if case.id != arrival_case.id:
            raise Clarification("Ambele rapoarte trebuie să numească același caz existent.")

        # First prove the arrival assertion's own group, airport and readiness.
        # Its text may only be hidden from restriction grounding after these
        # checks; surrounding shared qualifiers are never hidden with it.
        claim = _claim(arrival_span[0], arrival_span[0], "arrival_report")
        groups = _groups(world, case, request.text, bundle.arrival.group_ids, bundle.arrival.all_linked_groups)
        _groups(world, case, claim, bundle.arrival.group_ids, bundle.arrival.all_linked_groups)
        _readiness_subject(world, case, claim, groups)
        airport = _airport(claim, bundle.arrival.airport_code, bundle.arrival.airport_reference)
        if _unsupported_airport_change(claim, allow_diversion_background=True) or re.search(_OUTAGE, normalized(claim)):
            raise Clarification("Citatul pregătirii include o altă schimbare de aeroport sau serviciu. Păstrează declarațiile separate și complete.")
        with commands.store.connect() as conn:
            row = conn.execute("SELECT created_at FROM live_command_intents WHERE request_key=? AND fingerprint=? AND scenario_id=?",
                ("command:" + request.idempotency_key, Store.fingerprint(request.model_dump()), world.scenario_id)).fetchone()
        ready, readiness_basis = _readiness(claim, bundle.arrival.ready_at,
            bundle.arrival.ready_at_reference, row[0] if row else "")
        arrival_input = ArrivalReportInput(expected_version=request.expected_version,
            idempotency_key=request.idempotency_key, description=claim, group_ids=groups,
            airport_code=airport, ready_at=ready)

        restriction_view = request.model_copy(update={"text": _mask(request.text, arrival_span)})
        grounded_case, restriction_input = ground_airport_restriction(world, restriction_view,
            bundle.restriction, already_bound_case=case)
        if grounded_case.id != case.id:
            raise Clarification("Restricția și sosirea nu identifică același caz.")
        if re.search(_OTHER_ASSERTION, normalized(restriction_span[0])):
            raise Clarification("Citatul restricției include o altă declarație de poziție sau serviciu.")

        # Now the restriction is independently grounded. Remove precisely that
        # assertion while checking the arrival's original shared prefix/tail.
        arrival_view = _mask(request.text, restriction_span)
        _claim(arrival_view, bundle.arrival.report_reference, "arrival_report")
        _arrival_context(arrival_view, arrival_span)
        remaining = _mask(request.text, restriction_span, arrival_span)
        shared_prefix = request.text[:min(restriction_span[1], arrival_span[1])]
        if (re.search(_CONDITIONAL, normalized(remaining))
                or re.search(_NEGATIVE_OR_CONDITIONAL, normalized(shared_prefix))):
            raise Clarification("O condiție sau negație comună rămâne în afara citatelor. Repetă cele două declarații explicit și necondiționat; nu elimin contextul lor.")
        if (_unsupported_airport_change(remaining)
                or any(re.search(pattern, normalized(remaining)) for pattern in (_CLOSED, _OPEN, _LIFT, _RESTRICT, _INABILITY, _EXTRA_REPORT))):
            raise Clarification("Cererea conține o a treia schimbare necuprinsă în cele două rapoarte. Nu înregistrez doar o parte.")

        raw_recovery = interpreted.get("recovery")
        recovery, total = None, None
        if raw_recovery is not None:
            recovery = LiveRecoveryIntent.model_validate(raw_recovery, strict=True)
            if recovery.action != "start" or not _recovery_requested(remaining):
                raise Clarification("Recuperarea trebuie cerută explicit, separat de cele două rapoarte.")
            selected_case = _case(world, request.text, recovery.incident_id)
            selected = _groups(world, case, remaining, recovery.group_ids, recovery.all_linked_groups)
            if selected_case.id != case.id or selected != groups:
                raise Clarification("Recuperarea trebuie să numească exact cazul și grupurile din raportul de pregătire.")
            total = _total_limit(request.text, recovery)
        elif _recovery_requested(remaining):
            raise Clarification("Cererea include recuperare, dar interpretarea nu îi stabilește scopul. Nu aplic doar rapoartele.")
    except (Clarification, ValidationError, ValueError) as exc:
        explanation = str(exc) if isinstance(exc, Clarification) else "Interpretarea celor două rapoarte este incompletă sau invalidă. Repetă declarațiile și referințele exacte."
        return commands._reply(request, world, explanation + " Niciun raport sau job nou nu a fost înregistrat. Interpretarea a folosit 1 apel.")

    def apply(current):
        if current.scenario_id != world.scenario_id:
            raise DomainError(409, "Operation changed while interpreting the two reports")
        restriction = apply_airport_restriction_report(current, case.id, restriction_input,
            recovery_group_ids=groups if recovery is not None else None)
        arrival = apply_arrival_report(current, case.id, arrival_input, readiness_basis=readiness_basis)
        result = {**restriction, "report": arrival["report"], "reports": [restriction["report"], arrival["report"]],
            "focus_id": arrival_input.airport_code, "mode": "ai", "command_interpretation_calls": 1,
            "explanation": ("Am înregistrat împreună restricția de planificare a cazului și poziția/pregătirea declarată a grupurilor. "
                "Sunt două rapoarte ale operatorului, nu confirmări de aterizare, permisiune aeroportuară sau rezervare. "
                "Rezervările confirmate au fost păstrate. Interpretarea a folosit 1 apel.")}
        if restriction["manual_review_required"]:
            result["explanation"] += " Restricțiile cazului intră în conflict cu transport confirmat; este necesară revizuirea manuală."
        if recovery is not None:
            result["recovery_total_call_limit"] = total
        return result

    def enqueue(conn, response):
        if recovery is None:
            return
        current = commands.store._load(conn)
        job_request, failure = None, None
        try:
            job_request = _job_request(current, request, case, groups, recovery, total, bundle.arrival,
                jobs=commands.jobs, conn=conn, additional_reports=(bundle.restriction,))
        except RecoveryBlocked as exc:
            failure = {"code": exc.code, "explanation": exc.explanation}
        # The existing helper owns fixed-confirmation checks and SAVEPOINT queue
        # rollback. Its primary report is the restriction only for this call;
        # persisted public receipts always retain arrival as their primary.
        primary = response["report"]
        response["report"] = response["reports"][0]
        try:
            enqueue_airport_restriction_recovery(conn, commands.store, commands.jobs, response,
                restriction_input, job_request=job_request, recovery_blocked=failure,
                interpretation_calls=1, total_call_limit=total)
        finally:
            response["report"] = primary
        if response.get("recovery_blocked"):
            response["explanation"] += " Ambele rapoarte rămân înregistrate; căutarea nu a pornit."

    return commands.store.mutate("command:" + request.idempotency_key, request.model_dump(),
        request.expected_version, apply, on_commit=enqueue)
