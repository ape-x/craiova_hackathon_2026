"""Literal reuse of separate operator inputs for a planned after-flight pickup.

A saved configuration supplies no fare, vehicle, observed arrival or permission
to change the requested journey. The normal bounded worker always re-sources it.
"""
from __future__ import annotations

import re

from .airport_cab_commands import AirportTransferSetupRequired, _after_flight_transfer_requested
from .after_flight_recovery import REQUIRED_CALLS, validate_after_flight_job
from .store import DomainError


_SAVED = r"\b(?:saved|stored|salvat\w*|memorat\w*)\b"
_LABEL = r"\b(?:after[\s-]+flight|post[\s-]*flight|dupa[\s-]+zbor(?:ul)?|post[\s-]*zbor)\b"
_RECOVERY = r"\b(?:recover|recalculate|replan|search|recupereaza|recalculeaza|replanifica|cauta)\b"
_REUSE = r"\b(?:use|reuse|using|with|foloseste|folositi|folosind|reutilizeaza|reutilizati|reutilizand|utilizeaza|utilizati|utilizand|cu)\b"
_REUSE_ACTION = r"\b(?:use|reuse|using|foloseste|folositi|folosind|reutilizeaza|reutilizati|reutilizand|utilizeaza|utilizati|utilizand)\b"


def _reference_text(world, text):
    from .live_commands import normalized
    value = normalized(text)
    for group in sorted(world.groups, key=lambda item: len(item.name), reverse=True):
        for label in (group.name, group.id):
            if label:
                value = re.sub(r"(?<!\w)"+re.escape(normalized(label))+r"(?!\w)", " ", value)
    return value


def _after_flight_reference(value):
    return _after_flight_transfer_requested(value) or bool(re.search(_LABEL, value)
        and re.search(r"\b(?:configuration|configurations|setup|configurati\w*)\b", value))


def saved_after_flight_instruction(text):
    """Recognition only: report quotes must not swallow even negated reuse."""
    from .live_commands import normalized
    value = re.sub(r"bucharest[.\s-]*cab", "bucharest cab", normalized(text))
    return any(re.search(_SAVED, clause) and _after_flight_reference(clause) and re.search(_REUSE_ACTION, clause)
        for clause in re.split(r"[.;!?\n]", value))


def saved_after_flight_recovery_requested(text):
    """A separately stated imperative can request recovery without 'recalculate'."""
    from .live_commands import normalized
    value = re.sub(r"bucharest[.\s-]*cab", "bucharest cab", normalized(text))
    return any(saved_after_flight_instruction(clause)
        and not re.search(r"\b(?:if|whether|unless|daca|might|would|could|maybe|poate|not|no|don't|dont|never|nu|fara|without)\b", clause)
        and re.match(r"^\s*(?:(?:please|te rog|va rog)\s*[, :]?\s*)?"+_REUSE_ACTION, clause)
        for clause in re.split(r"[.;!?\n]", value))


def after_flight_reuse_explanation(inputs):
    return (" Am copiat exact configurația salvată pentru zbor urmat de transfer: "
        f"preluare între {inputs.pickup_window_start.isoformat()} și {inputs.pickup_window_end.isoformat()}, "
        f"procesare {inputs.airport_processing_minutes} minute, {inputs.suitcases} valize și {inputs.handbags} bagaje de mână. "
        "Zborurile și categoriile de vehicule vor fi căutate din nou în limita curentă; "
        "configurația nu confirmă aterizarea, un tarif actual sau o rezervare.")


def resolve_saved_after_flight_transfer(jobs, world, case, group_ids, modes, text, allowance, *, conn=None):
    """Resolve only an affirmative current saved-after-flight recovery clause.

    Callers remove independently grounded operational report spans and provide
    the post-report world. Case and passenger grounding remains their authority.
    """
    from .after_flight_profile import AfterFlightProfileError
    from .live_commands import _CALL_LIMIT, _TRAILING_LIMIT, _DURATION_COUNT, _reference_dates, hotel_input_time_requested, mentioned, normalized
    from .recovery_jobs import RecoveryJobRequest

    value = _reference_text(world, text)
    if not _after_flight_reference(value):
        return None
    literal_groups = {group.id for group in world.groups if mentioned(text, group.id) or mentioned(text, group.name)}
    if literal_groups != set(group_ids) or len(literal_groups) != 1:
        raise AirportTransferSetupRequired("Numește exact un singur grup pentru configurația salvată după zbor. Nu am omis alte grupuri menționate sau ales un subset presupus.")
    setup = ("Transferul după zbor necesită configurația sa separată în formularul «Flight to OTP + transfer to CRA»: "
        "grupul, intervalul de preluare, procesarea și bagajele explicite. ")
    if not re.search(_SAVED, value):
        raise AirportTransferSetupRequired(setup + "Pentru reutilizare, cere explicit configurația salvată pentru transferul după zbor. Nu am folosit un transfer inițial și nu am presupus aterizarea.")
    if (len(group_ids) != 1 or set(modes) != {"flight", "bus"}
            or re.search(r"\b(?:hotel\w*|cazare\w*|accommodation)\b", value)):
        raise AirportTransferSetupRequired("Configurația salvată după zbor necesită exact un grup și întregul traseu zbor + transfer, fără hotel sau transfer inițial. Nu am redus sau înlocuit serviciile cerute.")
    # Call limits are spending constraints, not negation or route edits. The
    # command dispatcher separately enforces the literal lower limit.
    value = _TRAILING_LIMIT.sub(" ", _CALL_LIMIT.sub(" ", value))
    value = re.sub(r"\b(?:without|no|fara)\s+(?:supplier\s+)?(?:bookings?|reservations?|payments?|rezervari|plati)\b", " ", value)
    clauses = re.split(r"[.;!?\n]", re.sub(r"bucharest[.\s-]*cab", "bucharest cab", value))
    references = [clause for clause in clauses if re.search(_SAVED, clause) and _after_flight_reference(clause)]
    if (re.search(r"\b(?:initial|initiala|initiale|initialul|initialului|initial\s+pickup)\b", value)
            or any(re.search(_SAVED, clause) and re.search(r"\b(?:transfer\w*|transport\w*)\b", clause)
                and not _after_flight_reference(clause) for clause in clauses)):
        raise AirportTransferSetupRequired("Cererea amestecă o configurație după zbor cu alt transfer sau profil. Selectează explicit un singur flux; nu am omis transferul suplimentar.")
    positive = any(re.search(_REUSE, clause) and (re.search(_RECOVERY, clause)
        or re.match(r"^\s*(?:(?:please|te rog|va rog)\s*[, :]?\s*)?"+_REUSE, clause)) for clause in references)
    if not positive:
        raise AirportTransferSetupRequired("Menționarea configurației după zbor nu cere reutilizarea ei. Cere explicit recuperarea folosind configurația salvată pentru transferul după zbor.")
    if re.search(r"\b(?:if|whether|unless|daca|might|would|could|maybe|poate|eventual|not|no|don't|dont|never|nu|fara|without)\b", value):
        raise AirportTransferSetupRequired("Reutilizarea configurației după zbor este negată, condițională sau ambiguă. Nu am pornit o căutare pe baza unei presupuneri.")
    if (re.search(r"\b(?:budget|buget\w*|adults?|adult[ai]|passengers?|pasager\w*|people|persons?|persoane|children|child|infants?|copii|copil\w*|wheelchair|accessib\w*|accesib\w*|dizabil\w*)\b|scaun\s+cu\s+rotile", value)
            or re.search(r"(?:\b(?:eur|euros?|ron|gbp|usd|lei|leu|lire|dolari|pounds?|dollars?)\b|[€£$])", value)):
        raise AirportTransferSetupRequired("Cererea include buget, componența pasagerilor sau accesibilitate. Înregistrează aceste constrângeri explicit înainte de reutilizare; nu am căutat pe datele vechi și nu am presupus capacitate ori asistență.")
    alternatives = r"(?:options?|alternatives?|candidates?|flights?|variant\w*|alternativ\w*|optiun\w*|zboruri)\b"
    if (re.search(r"\b(?:"+_DURATION_COUNT+r"|o|una)\s+(?:(?:incoming|inbound|flight|source|de|sosire)\s+){0,3}"+alternatives, value)
            or re.search(r"\b(?:change|limit|bound|count|schimba|modifica|limita|numar\w*)\b.{0,40}\b"+alternatives, value)
            or "max_inbound_options" in value):
        raise AirportTransferSetupRequired("Cererea schimbă sau precizează numărul variantelor de zbor. Revizuiește limita în formular; nu am înlocuit limita salvată ori mărit numărul căutărilor.")
    if (hotel_input_time_requested(text, (case.id, case.requested_flight_number))
            or re.search(r"\b(?:suitcases?|handbags?|luggage|baggage|bags?|bagaj\w*|valiz\w*|troler\w*|processing|procesar\w*|clearance|immigration|customs|window|interval\w*)\b", value)):
        raise AirportTransferSetupRequired("Cererea menționează separat date, ore, intervale, procesare sau bagaje. Revizuiește aceste intrări în formular; nu le-am înlocuit cu configurația salvată și nu am mutat preluarea.")
    route_value = normalized(text)
    _, spans = _reference_dates(text, (case.id, case.requested_flight_number))
    for start, end in sorted(spans, reverse=True):
        intro = re.search(r"\b(?:from|din(?:\s+data\s+de)?)\s*$", route_value[:start])
        start = intro.start() if intro else start
        route_value = route_value[:start]+" "*(end-start)+route_value[end:]
    route_value = _reference_text(world, route_value)
    route_value = _TRAILING_LIMIT.sub(" ", _CALL_LIMIT.sub(" ", route_value))
    route = (r"\b(?:to|from|via|through|towards|between|spre|catre|din|de la|pana la|intre|prin)\s+"
        r"(?!(?:use|reuse|recover|recalculate|replan|search|get|go|read|review|see)\b)[a-z]")
    if re.search(route, route_value) or re.search(r"(?:->|→|=>)|\b(?:destination|origin|destinati\w*|origine)\b", route_value):
        raise AirportTransferSetupRequired("Cererea specifică o rută împreună cu configurația salvată după zbor. Revizuiește traseul în formular; nu am substituit automat zborul spre OTP și transferul spre CRA.")
    if allowance < REQUIRED_CALLS:
        raise AirportTransferSetupRequired(f"Fluxul după zbor necesită minimum {REQUIRED_CALLS+1} apeluri totale: 1 interpretare și {REQUIRED_CALLS} pentru căutare și decizie. Două variante pot necesita 9 apeluri totale. Limita nu a fost mărită.", "insufficient_call_allowance")
    profiles = getattr(jobs, "after_flight_profiles", None)
    group = next((item for item in world.groups if item.id == group_ids[0]), None)
    try:
        if profiles is None or group is None:
            raise AirportTransferSetupRequired(setup + "Configurația salvată pentru acest grup lipsește; nu am substituit profilul transferului inițial.")
        inputs = profiles.read(world, group, conn=conn)
    except AfterFlightProfileError as exc:
        raise AirportTransferSetupRequired(setup + "Configurația lipsește, este invalidă sau nu mai corespunde identității grupului și traseului. Nu am ales un subset, inventat bagaje sau reutilizat un alt profil.") from exc
    probe = RecoveryJobRequest(expected_version=world.version, idempotency_key="saved-after-flight-validation",
        group_ids=group_ids, modes=modes, max_provider_calls=allowance, after_flight_transfer=inputs)
    try:
        validate_after_flight_job(world, probe, jobs.airport_cab)
    except DomainError as exc:
        if exc.status != 422:
            raise
        raise AirportTransferSetupRequired("Configurația după zbor nu mai este validă pentru condițiile actuale. Revizuiește formularul; intervalul și procesarea nu au fost schimbate. "+exc.message) from exc
    return inputs
