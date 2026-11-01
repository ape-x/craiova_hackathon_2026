"""One explicit service outage and one passenger report in one transaction.

The service report keeps its global dependency scope; arrival and subsequent
recovery retain exactly one selected case/group scope. No source is called here.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from .confirmed_allocations import has_confirmed_groups
from .live_airport_reports import _NEGATIVE_OR_CONDITIONAL
from .live_commands import mentioned, normalized
from .live_diversion_commands import _CONDITIONAL, _EXTRA_REPORT, _OUTAGE, _arrival_context, _mask, _unique_span
from .live_operational_commands import (
    Clarification, RecoveryBlocked, _airport, _case, _claim, _fragment, _groups, _job_request,
    _readiness, _readiness_subject, _recovery_requested, _resource, _total_limit,
    _service_airport_guard_text, _unsupported_airport_change,
    has_passenger_position_claim, has_unrepresented_operational_claim,
)
from .operational_reports import (
    ArrivalReportInput, ResourceUnavailabilityInput, apply_arrival_report,
    apply_resource_unavailability,
)
from .providers.ai import LiveRecoveryIntent, LiveResourceUnavailabilityAndArrival
from .store import DomainError, Store


_ARRIVAL_ASSERTION = r"\b(?:ready|readiness|arrived|landed|sosit|ajuns|aterizat|pregatit\w*|gata|disponibili)\b"


def _one_service_assertion(world, text, reference):
    """An unnamed second failed service cannot be swallowed by the first quote."""
    literal, start = _fragment(text, reference)
    end = start+len(literal)
    boundaries = [match for match in re.finditer(r"[,;.!?\n]|\b(?:and|but|si|și|dar)\b", text, re.IGNORECASE)
        if not (match.start() < end and match.end() > start)]
    cursor = 0
    for boundary in [*boundaries, None]:
        stop = boundary.start() if boundary is not None else len(text)
        clause = text[cursor:stop]
        if mentioned(clause, reference):
            # Repeating this exact service is still one outage, provided every
            # independently stated description actually affirms its outage.
            _claim(clause, clause, "resource_unavailability")
        elif has_unrepresented_operational_claim(world, clause):
            raise Clarification("Citatul serviciului conține o altă declarație operațională. Raportează un singur serviciu și o singură poziție/pregătire.")
        cursor = boundary.end() if boundary is not None else len(text)


def route_service_arrival_reports(commands, request, world, interpreted):
    try:
        bundle = LiveResourceUnavailabilityAndArrival.model_validate(interpreted["operational_report"], strict=True)
        if interpreted.get("events") or interpreted.get("strategies"):
            raise Clarification("Cele două rapoarte nu se combină cu modificări sau planuri globale.")
        service_span = _unique_span(request.text, bundle.unavailability.report_reference)
        arrival_span = _unique_span(request.text, bundle.arrival.report_reference)
        if max(service_span[1], arrival_span[1]) < min(service_span[2], arrival_span[2]):
            raise Clarification("Indisponibilitatea și pregătirea pasagerilor necesită două citate distincte, fără suprapunere.")
        from .after_flight_commands import saved_after_flight_instruction
        if any(_recovery_requested(span[0]) or saved_after_flight_instruction(span[0]) for span in (service_span, arrival_span)):
            raise Clarification("Separă rapoartele de cererea de recuperare; nu omit o căutare sau condițiile ei dintr-un citat.")
        case = _case(world, request.text, bundle.arrival.incident_id)
        claim = _claim(arrival_span[0], arrival_span[0], "arrival_report")
        groups = _groups(world, case, request.text, bundle.arrival.group_ids, bundle.arrival.all_linked_groups)
        _groups(world, case, claim, bundle.arrival.group_ids, bundle.arrival.all_linked_groups)
        _readiness_subject(world, case, claim, groups)
        airport = _airport(claim, bundle.arrival.airport_code, bundle.arrival.airport_reference)
        if _unsupported_airport_change(claim, allow_diversion_background=True) or re.search(_OUTAGE, normalized(claim)):
            raise Clarification("Citatul pregătirii include și o altă schimbare de aeroport sau serviciu.")
        with commands.store.connect() as conn:
            row = conn.execute("SELECT created_at FROM live_command_intents WHERE request_key=? AND fingerprint=? AND scenario_id=?",
                ("command:"+request.idempotency_key, Store.fingerprint(request.model_dump()), world.scenario_id)).fetchone()
        ready, readiness_basis = _readiness(claim, bundle.arrival.ready_at,
            bundle.arrival.ready_at_reference, row[0] if row else "")
        arrival_input = ArrivalReportInput(expected_version=request.expected_version,
            idempotency_key=request.idempotency_key, description=claim, group_ids=groups,
            airport_code=airport, ready_at=ready)

        service_claim = _claim(service_span[0], service_span[0], "resource_unavailability")
        resource = _resource(world, service_claim, bundle.unavailability.resource_id, bundle.unavailability.resource_reference)
        _one_service_assertion(world, service_claim, bundle.unavailability.resource_reference)
        if (_unsupported_airport_change(_service_airport_guard_text(service_claim, resource))
                or re.search(_ARRIVAL_ASSERTION, normalized(service_claim))
                or has_passenger_position_claim(world, service_claim)):
            raise Clarification("Citatul serviciului include o altă declarație de poziție sau aeroport.")
        # Mask only an independently grounded opposite assertion; common prefix
        # and trailing qualifiers still belong to the literal current request.
        _claim(_mask(request.text, arrival_span), bundle.unavailability.report_reference, "resource_unavailability")
        arrival_view = _mask(request.text, service_span)
        _claim(arrival_view, bundle.arrival.report_reference, "arrival_report")
        _arrival_context(arrival_view, arrival_span)
        service_input = ResourceUnavailabilityInput(expected_version=request.expected_version,
            idempotency_key=request.idempotency_key, description=service_claim)
        remaining = _mask(request.text, service_span, arrival_span)
        prefix = request.text[:min(service_span[1], arrival_span[1])]
        if (re.search(_CONDITIONAL, normalized(remaining))
                or re.search(_NEGATIVE_OR_CONDITIONAL, normalized(prefix))):
            raise Clarification("O condiție sau negație comună rămâne în afara citatelor; repetă ambele declarații explicit.")
        if (_unsupported_airport_change(remaining) or re.search(_EXTRA_REPORT, normalized(remaining))
                or has_unrepresented_operational_claim(world, remaining)):
            raise Clarification("Cererea conține o altă schimbare necuprinsă în cele două rapoarte. Nu aplic doar o parte.")
        recovery, total = None, None
        if interpreted.get("recovery") is not None:
            recovery = LiveRecoveryIntent.model_validate(interpreted["recovery"], strict=True)
            if recovery.action != "start" or not _recovery_requested(remaining):
                raise Clarification("Recuperarea trebuie cerută explicit, separat de cele două rapoarte.")
            selected_case = _case(world, request.text, recovery.incident_id)
            selected = _groups(world, case, remaining, recovery.group_ids, recovery.all_linked_groups)
            if selected_case.id != case.id or selected != groups:
                raise Clarification("Recuperarea trebuie să numească exact cazul și grupurile din raportul de pregătire.")
            total = _total_limit(request.text, recovery)
        elif _recovery_requested(remaining):
            raise Clarification("Recuperarea cerută nu este reprezentată complet. Nu aplic doar rapoartele.")
    except (Clarification, ValidationError, ValueError) as exc:
        explanation = str(exc) if isinstance(exc, Clarification) else "Interpretarea celor două rapoarte este incompletă sau invalidă; repetă referințele exacte."
        return commands._reply(request, world, explanation+" Niciun raport sau job nou nu a fost înregistrat. Interpretarea a folosit 1 apel.")

    def apply(current):
        if current.scenario_id != world.scenario_id:
            raise DomainError(409, "Operation changed while interpreting the two reports")
        # Invalidation must not erase an original active association to a booked
        # service before the arrival primitive can see that booking protection.
        if has_confirmed_groups(current, groups):
            raise DomainError(409, "Selected passengers have externally confirmed bookings. Operator review is required; neither report was applied.")
        unavailable = apply_resource_unavailability(current, resource.id, service_input)
        arrived = apply_arrival_report(current, case.id, arrival_input, readiness_basis=readiness_basis)
        reports = [unavailable["report"], arrived["report"]]
        return {"applied": True, "incident_id": case.id, "report": arrived["report"],
            "reports": reports, "focus_id": airport,
            "invalidated_offer_ids": sorted({identity for report in reports for identity in report["affected_offer_ids"]}),
            "affected_group_ids": sorted({identity for report in reports for identity in report["affected_group_ids"]}),
            "mode": "ai", "command_interpretation_calls": 1,
            **({"recovery_total_call_limit": total} if recovery is not None else {}),
            "explanation": ("Am înregistrat împreună indisponibilitatea serviciului și poziția/pregătirea declarată a grupurilor. "
                "Toate propunerile dependente de serviciu au fost invalidate; numai grupurile declarate au fost relocate. "
                "Sunt rapoarte ale operatorului, nu confirmări de aterizare, permisiune aeroportuară sau rezervare. "
                "Rezervările confirmate au fost păstrate. Interpretarea a folosit 1 apel.")}

    def enqueue(conn, response):
        if recovery is None:
            return
        current = commands.store._load(conn)
        try:
            job_request = _job_request(current, request, case, groups, recovery, total, bundle.arrival,
                jobs=commands.jobs, conn=conn, additional_reports=(bundle.unavailability,))
            if not commands.jobs.ai or not commands.jobs.ai.configured:
                raise RecoveryBlocked("recovery_service_unavailable", "Serviciul real de recuperare nu este disponibil; nu am înlocuit căutarea cu un plan scriptat.")
            conn.execute("SAVEPOINT service_arrival_recovery")
            try:
                result = commands.jobs.start_in_transaction(conn, current, case.id, job_request, supersede_active=True)
            except DomainError as exc:
                conn.execute("ROLLBACK TO service_arrival_recovery")
                conn.execute("RELEASE service_arrival_recovery")
                if exc.status == 422 and job_request.romanian_hotel_recovery is not None:
                    raise RecoveryBlocked("romanian_hotel_setup_required", exc.message) from exc
                if exc.status == 422 and (job_request.airport_transfers or job_request.after_flight_transfer is not None):
                    raise RecoveryBlocked("airport_transfer_setup_required", exc.message) from exc
                if (exc.details or {}).get("code") not in ("recovery_queue_full", "recovery_group_active", "recovery_handoff_limit"):
                    raise
                raise RecoveryBlocked("recovery_queue_unavailable", "Coada nu poate primi înlocuirea. Nu am păstrat anulări parțiale sau un job incomplet.") from exc
            conn.execute("RELEASE service_arrival_recovery")
            response["recovery_job"] = result["job"]
            response["explanation"] += (f" Recuperarea pentru {case.id}, grupurile {', '.join(groups)}, este în coadă. "
                f"Limita totală {total}: 1 interpretare și cel mult {total-1} apeluri ale jobului. "
                "Această înregistrare nu confirmă un itinerar fezabil sau o rezervare.")
            if job_request.airport_transfers:
                response["explanation"] += " Configurațiile salvate de transfer au fost copiate exact; tarifele vor fi căutate din nou în limita curentă."
            if job_request.after_flight_transfer is not None:
                from .after_flight_commands import after_flight_reuse_explanation
                response["explanation"] += after_flight_reuse_explanation(job_request.after_flight_transfer)
            if job_request.romanian_hotel_recovery is not None:
                from .romanian_hotel_commands import romanian_hotel_reuse_explanation
                response["explanation"] += romanian_hotel_reuse_explanation(job_request.romanian_hotel_recovery)
            if result["job"].get("supersedes_job_ids"):
                response["explanation"] += " Încercările suprapuse trebuie să încheie apelurile deja trimise; rezultatele incerte cer revizuire înainte de alte apeluri."
        except RecoveryBlocked as exc:
            response["recovery_blocked"] = {"code": exc.code, "explanation": exc.explanation}
            response["explanation"] += " Ambele rapoarte rămân înregistrate; căutarea nu a pornit. "+exc.explanation

    return commands.store.mutate("command:"+request.idempotency_key, request.model_dump(),
        request.expected_version, apply, on_commit=enqueue)
