"""Literal grounding of a current case's operator airport constraint.

The model identifies the intent; this boundary checks its action, direction and
scope against the unchanged utterance before any mutation or supplier search.
It neither repairs speech nor infers an aircraft/passenger movement.
"""
from __future__ import annotations

import re

from .live_commands import normalized


_ARRIVALS = r"\b(?:land(?:ing|ings)?|arrivals?|ateriz\w*|sosir\w*)\b"
_DEPARTURES = r"\b(?:depart(?:ures?)?|take[ -]?off|decol\w*|plecar\w*)\b"
_CLOSED = r"\b(?:closed?|closures?|inchis\w*|inchide\w*|unavailable|indisponibil\w*)\b"
_OPEN = r"\b(?:reopen(?:ed)?|open(?:ed)?|redeschis\w*|redeschide\w*|deschis\w*|allowed|permitted|unrestricted|unaffected|permis\w*|neafectat\w*)\b"
_LIFT = r"\b(?:clear|remove|lift|ridica|elimina|sterge|scoate)\b[^.;!?]{0,70}?\b(?:restrictions?|bans?|restricti\w*|interdicti\w*)\b"
_RESTRICT = r"\b(?:restrict\w*|interzis\w*|interdicti\w*|prohibit\w*|ban(?:ned)?|block(?:ed)?|blocat\w*|suspend\w*|suspendat\w*|explicit_restriction)\b"
_INABILITY = (
    r"\b(?:(?:cannot|can not|can't|can’t|unable to|not able to)\s+(?:(?:accept|allow)\s+)?"
    r"(?:land(?:ings?)?|arrivals?|depart(?:ures?)?|take off)"
    r"|(?:does not|doesn't|doesn’t|no longer)\s+(?:accepts?|allows?)\s+(?:arrivals?|departures?|landings?|takeoffs?)"
    r"|no\s+(?:arrivals?|departures?|landings?|takeoffs?)"
    r"|nu\s+(?:mai\s+)?(?:se\s+)?(?:poate|pot|permite|permit|accepta)\s+(?:sa\s+)?(?:ateriz\w*|decol\w*|sosir\w*|plecar\w*))\b"
)
_NEGATIVE_OR_CONDITIONAL = r"\b(?:if|whether|unless|when|whenever|once|while|provided|except\w*|would|could|might|maybe|perhaps|probably|possibly|daca|cand|odata ce|o data ce|indata ce|imediat ce|cat timp|poate|eventual|probabil|not|no|never|don't|dont|nu|fara|without)\b"
_MOVEMENT = r"\b(?:divert(?:ed|ing|s)?|diversions?|deviat\w*|deviaz\w*|deviere\w*|redirection(?:at|eaza)\w*)\b"
_SERVICE = r"\b(?:bus(?:es)?|coaches?|hotel\w*|autobuz\w*|autocar\w*|transfer\w*|office|birou\w*|ticket)\b"
_RECOVERY_START = r"\b(?:(?:and|then|si|apoi)\s+)?(?:(?:do not|don't|nu|please|te rog)\s+)?(?:recover|recalculate|replan|search|recupereaza|recalculeaza|replanifica|cauta)\b"
_TEMPORAL = (
    r"\b(?:tomorrow|today|tonight|later|earlier|yesterday|previously|was|were|during|overnight|at night|maine|azi|astazi|ieri|diseara|noaptea|la noapte|in timpul|ulterior|anterior|until|between|pana|incepand|a fost|au fost)\b"
    r"|\d{1,2}:\d{2}|\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"
    r"|\b(?:for|timp de|pentru|in|peste)\b.{0,30}\b(?:minutes?|hours?|days?|minute|ore|zile)\b"
    r"|\b(?:on|from|pe|din)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|luni|marti|miercuri|joi|vineri|sambata|duminica)\b"
)


def has_airport_restriction_claim(text):
    """Detect omitted reopening/directional clauses as well as whole closures."""
    from .airport_mentions import literal_airport_ids

    sentences = re.split(r"[.;!?\n]", text)
    clauses = [part for sentence in sentences
               for part in [sentence, *re.split(r"\b(?:and|but|si|și|dar)\b", sentence, flags=re.IGNORECASE)]]
    for clause in clauses:
        value = normalized(clause)
        if not (re.search(_CLOSED, value) or re.search(_OPEN, value)
                or re.search(_LIFT, value) or re.search(_INABILITY, value)
                or (re.search(_RESTRICT, value) and (re.search(_ARRIVALS, value) or re.search(_DEPARTURES, value)))):
            continue
        # Service availability is a different report, including at an airport.
        if re.search(_SERVICE, value):
            continue
        airport_words = re.search(r"\b(?:airports?|aeroport\w*|runways?|pist[ae]\w*)\b", value)
        # CLOSED is also an obsolete catalog name, not a subject by itself.
        names = literal_airport_ids(re.sub(r"\b(?:closed|closures?)\b", " ", clause, flags=re.IGNORECASE))
        if airport_words or names:
            return True
    return False


def _mask_closed_predicate(claim, reference_span):
    """Do not mistake the grammatical predicate CLOSED for catalog NLV.

    A quoted airport name, or a separate airport called CLOSED in a list, remains
    untouched; this only disambiguates the predicate following its subject.
    """
    result = list(claim)
    start, end = reference_span
    for match in re.finditer(r"\bclosed\b", claim, re.IGNORECASE):
        if start <= match.start() < end:
            continue
        preceding = claim[:match.start()]
        if ((match.start() >= end and not claim[end:match.start()].strip())
                or re.search(r"\b(?:is|are|was|were|became|remains|now|temporarily|currently)\s*$", preceding, re.IGNORECASE)):
            result[match.start():match.end()] = " " * len(match.group())
    return "".join(result)


def ground_airport_restriction(world, request, report):
    from .airport_restrictions import AirportRestrictionReportInput
    from .live_operational_commands import Clarification, _airport, _case, _fragment, _group_mentions
    from .live_commands import _reference_pattern

    case = _case(world, request.text, report.incident_id)
    claim, start = _fragment(request.text, report.report_reference)
    reference, airport_start = _fragment(claim, report.airport_reference)
    airport = _airport(_mask_closed_predicate(claim, (airport_start, airport_start + len(reference))),
                       report.airport_code, reference)
    # Airport names are data. Their words cannot authorize closing/reopening.
    value = normalized(claim[:airport_start] + " " * len(reference) + claim[airport_start + len(reference):])
    boundary = max(request.text.rfind(char, 0, start) for char in ".!?;\n") + 1
    prefix = normalized(request.text[boundary:start])
    end = start + len(claim)
    sentence_end = min((position for char in ".!?;\n" if (position := request.text.find(char, end)) >= 0), default=len(request.text))
    tail = normalized(request.text[end:sentence_end])
    followup = re.search(_RECOVERY_START, tail)
    if followup:
        tail = tail[:followup.start()]
    # The model cannot omit a narrowing suffix such as 'for arrivals', 'only if',
    # 'tomorrow' or 'for G1' from the quoted predicate and thereby broaden it.
    context_value = prefix + " " + value + " " + tail
    if _group_mentions(world, case, context_value):
        raise Clarification("Restricția aeroportului se aplică întregului caz. Numește grupurile doar în cererea separată de recuperare; nu extind o restricție de grup la întregul caz.")
    protected = re.sub(_INABILITY, " explicit_restriction ", context_value)
    if (re.search(_NEGATIVE_OR_CONDITIONAL, protected)
            or "?" in claim or request.text[start + len(claim):].lstrip().startswith("?")):
        raise Clarification("Declară explicit restricția curentă a cazului; o întrebare, negație sau condiție viitoare nu o modifică.")
    if re.search(_MOVEMENT, context_value):
        raise Clarification("O restricție de planificare nu deviază avionul. Raportează separat situația aeroportului și poziția/pregătirea pasagerilor.")
    dated_context = context_value
    for identifier in (case.id, case.requested_flight_number):
        pattern = _reference_pattern(identifier)
        if pattern:
            dated_context = re.sub(pattern.pattern + r"\s+(?:(?:on|dated|din|data)\s+)?" + re.escape(case.date_utc), " case_reference ", dated_context)
    if re.search(_TEMPORAL, dated_context):
        raise Clarification("Acest raport modifică restricția curentă; intervalele viitoare necesită date distincte și nu sunt aplicate ca restricție imediată.")
    if re.search(_SERVICE, value):
        raise Clarification("Identifică restricția aeroportului separat de indisponibilitatea unui serviciu.")
    operations = set()
    if re.search(_ARRIVALS, context_value):
        operations.add("arrivals")
    if re.search(_DEPARTURES, context_value):
        operations.add("departures")
    clear = bool(re.search(_OPEN, protected) or re.search(_LIFT, protected))
    remaining = re.sub(_LIFT, " ", protected)
    restrict = bool(re.search(_CLOSED, remaining) or re.search(_RESTRICT, remaining))
    if clear == restrict:
        raise Clarification("Numește o singură modificare: restricționare sau eliminarea restricției, cu direcția explicită.")
    if not operations:
        if re.search(_CLOSED, protected) or re.search(_OPEN, protected) or re.search(r"\b(?:all restrictions|toate restricti\w*)\b", protected):
            operations = {"arrivals", "departures"}
        else:
            raise Clarification("Precizează aterizările, decolările sau ambele; nu extind o restricție fără direcție.")
    if len(set(report.operations)) != len(report.operations) or set(report.operations) != operations or report.action != ("clear" if clear else "restrict"):
        raise Clarification("Interpretarea schimbă direcția sau sensul declarației. Repetă aeroportul, aterizări/decolări și restricționare/eliminare.")
    return case, AirportRestrictionReportInput(expected_version=request.expected_version,
        idempotency_key=request.idempotency_key, description=claim,
        airport_code=airport, operations=report.operations, action=report.action)
