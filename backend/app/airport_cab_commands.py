"""Explicit reuse of operator transfer inputs, never a saved fare or booking."""
from __future__ import annotations

import re

from .airport_cab_profile import AirportCabProfileError
from .airport_cab_recovery import validate_airport_transfer_job
from .store import DomainError


class AirportTransferSetupRequired(ValueError):
    def __init__(self, message, code="airport_transfer_setup_required"):
        super().__init__(message)
        self.code = code


def _after_flight_transfer_requested(value):
    """Recognize a physical sequence, not merely flight/source vocabulary.

    Called only after group labels are removed; operational callers also remove
    their independently grounded report spans. This guard authorizes nothing.
    Even a conditional/negated future connection must not become a different
    ordinary recovery or an initial pickup through an interpretation omission.
    """
    transfer = r"\b(?:transfers?|transfer(?:ul|ului|uri|urile|urilor)|transport(?:ul|ului)?|taxi(?:ul)?|cab|masin(?:a|ii)|bucharest[.\s-]*cab)\b"
    flight = (r"\b(?:flights?|planes?|aircraft|zbor(?:ul|ului|uri|urile|urilor)?|avion(?:ul|ului)?)\b"
        r"(?!\s+(?:search(?:es)?|quer(?:y|ies)|providers?|suppliers?|sources?|prices?|pricing|status|number|details|results?|availability|cautar\w*|furnizor\w*|surs\w*|pret\w*|rezultat\w*)\b)")
    modifiers = r"(?:(?:the|a|an|our|their|next|new|future|incoming|un|unui|urmator\w*|nou\w*|viitor\w*|ce)\s+){0,4}"
    landing = r"\b(?:landing|lands?|ateriz\w*)\b"
    arrival_of_flight = rf"\b(?:arrival|sosir\w*)\b\s+(?:(?:of|the|a|an|unui|un)\s+){{0,3}}{flight}"
    target = rf"(?:{flight}|{landing}|{arrival_of_flight}|(?:we|they)\s+land\b)"
    after = rf"\b(?:after|following|once|when|dupa|cand)\s+{modifiers}{target}"
    gap = r"[^.;!?\n]{0,140}"
    if re.search(rf"{transfer}{gap}{after}|{after}{gap}{transfer}", value):
        return True
    # An explicitly ordered route can be expressed without the word 'after'.
    # Requiring a travel/arrival phrase excludes 'change flight provider, then…'.
    inbound = rf"(?:{flight}\s+(?:(?:will\s+)?(?:arriv(?:e|es|ing)|land(?:s|ing)?)\s+)?(?:to|into|at|via|towards|spre|catre|la|in)\b|\b(?:fly|flying|land(?:s|ing)?|zboara|zburam|zburati|ateriz\w*)\s+(?:to|into|at|in|spre|catre|la)\b)"
    sequence = r"(?:\b(?:then|afterwards|subsequently|apoi|ulterior)\b|\bfollowed\s+by\b|\burmat\w*\s+de\b|\bdupa\s+aceea\b|\b(?:and\s+)?from\s+there\b|\b(?:si\s+)?de\s+acolo\b)"
    value = re.sub(r"bucharest[.\s-]*cab", "bucharest cab", value)
    return bool(re.search(rf"{inbound}{gap}[.,;]?\s*{sequence}{gap}{transfer}", value))


def resolve_saved_airport_transfers(jobs, world, case, group_ids, modes, text, allowance, *, conn=None):
    """Only the current recovery clause can request exact saved inputs.

    Report callers remove the separately grounded operational assertion first,
    and pass the post-report world. No source I/O, inferred subset or date edits.
    """
    from .live_commands import _CALL_LIMIT, _TRAILING_LIMIT, _reference_dates, hotel_input_time_requested, normalized
    from .recovery_jobs import RecoveryJobRequest

    value = normalized(text)
    # A passenger label is a reference, not an instruction to reuse a source.
    for group in sorted(world.groups, key=lambda item: len(item.name), reverse=True):
        for label in (group.name, group.id):
            if label:
                value = re.sub(r"(?<!\w)" + re.escape(normalized(label)) + r"(?!\w)", " ", value)
    if _after_flight_transfer_requested(value):
        raise AirportTransferSetupRequired(
            "Un transfer după un zbor viitor necesită formularul «Flight to OTP + transfer to CRA»: "
            "grupul exact, intervalul de preluare, minutele de procesare și bagajele explicite. "
            "Reutilizarea vocală a acestui flux nu este încă disponibilă. Nu am substituit un transfer inițial salvat "
            "și nu am presupus aterizarea sau pornit altă căutare.")
    saved = re.search(r"\b(?:saved|stored|salvat\w*|memorat\w*)\b", value)
    transfer_pattern = r"\b(?:transfers?|transfer(?:ul|ului|uri|urile|urilor)|transport(?:ul|ului)?|bucharest[.\s-]*cab)\b"
    transfer = re.search(transfer_pattern, value)
    if not (saved and transfer):
        return []
    hotel = "hotel" in modes or re.search(r"\b(?:hotel\w*|cazare\w*|accommodation)\b", value)
    if hotel and not re.search(r"\bbucharest[.\s-]*cab\b", value):
        # Existing saved-hotel workflows already own their two UK transfers.
        return []
    if hotel or "bus" not in modes:
        raise AirportTransferSetupRequired("Configurația transferului salvat necesită transport terestru și nu poate fi combinată cu fluxul separat al hotelului. Nu am înlocuit serviciile cerute.")
    clauses = re.split(r"[.;!?\n]", re.sub(r"bucharest[.\s-]*cab", "bucharest cab", value))
    reuse_clauses = [clause for clause in clauses if re.search(transfer_pattern, clause)
        and re.search(r"\b(?:saved|stored|salvat\w*|memorat\w*)\b", clause)]
    positive = r"\b(?:use|reuse|using|with|recover|recalculate|replan|folos\w*|reutiliz\w*|utiliz\w*|cu|recupereaz\w*|recalculeaz\w*|replanific\w*)\b"
    if not any(re.search(positive, clause) for clause in reuse_clauses):
        raise AirportTransferSetupRequired("Menționarea unei configurații salvate nu cere reutilizarea ei. Cere explicit recuperarea folosind configurația salvată de transfer.")
    if any(re.search(r"\b(?:if|whether|unless|daca|might|would|could|maybe|poate|eventual)\b", clause) for clause in reuse_clauses):
        raise AirportTransferSetupRequired("Reutilizarea transferului salvat este condițională. Precizează explicit căutarea dorită; nu am pornit-o pe baza unei presupuneri.")
    if re.search(r"\b(?:not|no|don't|dont|never|nu|fara|without|if|whether|daca|might|would)\b[^.;!?\n]{0,60}\b(?:use|reuse|using|folos\w*|reutiliz\w*|saved|salvat\w*|transfer\w*)\b", value):
        raise AirportTransferSetupRequired("Reutilizarea transferului salvat este negată, condițională sau ambiguă. Precizează configurația dorită; nu am pornit o căutare presupusă.")
    if (hotel_input_time_requested(text, (case.id, case.requested_flight_number))
            or re.search(r"\b(?:suitcases?|handbags?|luggage|baggage|bags?|bagaj\w*|valiz\w*|troler\w*)\b", value)):
        raise AirportTransferSetupRequired("Cererea include date, ore sau bagaje suplimentare. Revizuiește-le în formularul transferului; nu le-am înlocuit cu datele salvate și nu am avansat preluarea.")
    # A destination or route clause changes the typed transfer contract even
    # when interpretation missed requires_input_changes. Never quietly search
    # OTP→CRA for an explicitly different transfer route (including city names).
    route = (r"\b(?:to|from|via|through|towards|between|spre|catre|din|de la|pana la|intre|prin)\s+"
        r"(?!(?:use|reuse|recover|recalculate|replan|search|get|go|read|review|see)\b)[a-z]")
    route_value = normalized(text)
    _, date_spans = _reference_dates(text, (case.id, case.requested_flight_number))
    for start, end in sorted(date_spans, reverse=True):
        intro = re.search(r"\b(?:from|din(?:\s+data\s+de)?)\s*$", route_value[:start])
        start = intro.start() if intro else start
        route_value = route_value[:start] + " " * (end - start) + route_value[end:]
    for group in sorted(world.groups, key=lambda item: len(item.name), reverse=True):
        for label in (group.name, group.id):
            if label:
                route_value = re.sub(r"(?<!\w)" + re.escape(normalized(label)) + r"(?!\w)", " ", route_value)
    route_value = _TRAILING_LIMIT.sub(" ", _CALL_LIMIT.sub(" ", route_value))
    if re.search(route, route_value):
        raise AirportTransferSetupRequired("Cererea specifică o rută pentru transferul salvat. Revizuiește destinația în formular; nu am substituit automat traseul OTP–CRA.")
    if allowance < 2 * len(group_ids) + 1:
        raise AirportTransferSetupRequired(f"Transferurile necesită minimum {2 * len(group_ids) + 2} apeluri totale, inclusiv interpretarea. Limita nu a fost mărită.", "insufficient_call_allowance")
    by_id = {group.id: group for group in world.groups}
    try:
        inputs = [jobs.airport_cab_profiles.read(world, by_id[group_id], conn=conn) for group_id in group_ids]
    except (AirportCabProfileError, KeyError) as exc:
        raise AirportTransferSetupRequired("Configurația transferului lipsește, este invalidă sau nu mai corespunde unuia dintre grupurile și traseele selectate. Completează formularul și finalizează prima planificare; nu am ales un subset sau presupus bagaje și preluări.") from exc
    # The saved scope is identity only. Readiness, source support and current
    # passenger constraints must pass against precisely this projected world.
    probe = RecoveryJobRequest(expected_version=world.version, idempotency_key="saved-airport-transfer-validation",
        group_ids=group_ids, modes=modes, max_provider_calls=allowance, airport_transfers=inputs)
    try:
        validate_airport_transfer_job(world, probe, jobs.airport_cab)
    except DomainError as exc:
        raise AirportTransferSetupRequired("Configurația transferului nu mai este validă pentru condițiile actuale. Revizuiește formularul; nu am schimbat preluarea. " + exc.message) from exc
    return inputs
