"""Literal, scope-bound reuse of separately saved Romanian package inputs.

Profiles contain operator inputs, never current supplier inventory. The bounded
worker re-sources every service; source names alone authorize no search.
"""
from __future__ import annotations

import re
import unicodedata

from .store import DomainError


SETUP_EXPLANATION = (
    "Pachetul OTP–Hotel Aro Palace–OTP necesită configurarea explicită în formularul "
    "Romanian coach + hotel: datele șederii, bagajele și marjele de conexiune. "
    "Configurația UK salvată nu a fost substituită. Nicio căutare de furnizor nu a fost pornită."
)
_SAVED = r"\b(?:saved|stored|salvat\w*|memorat\w*)\b"
_CONFIGURATION = r"\b(?:configuration|settings?|setup|profile|inputs?|configurati\w*|setari\w*|profil\w*)\b"
_REUSE = r"\b(?:use|reuse|using|with|foloseste|folositi|folosind|reutilizeaza|reutilizati|reutilizand|utilizeaza|utilizati|utilizand|cu)\b"
_ACTION = r"\b(?:use|reuse|using|choose|select|include|prefer|book|foloseste|folositi|folosind|reutilizeaza|reutilizati|reutilizand|utilizeaza|utilizati|utilizand|alege|selecteaza|rezerva)\b"
_RECOVERY = r"\b(?:recover|recalculate|replan|search|recupereaza|recalculeaza|replanifica|cauta)\b"


class RomanianHotelSetupRequired(ValueError):
    def __init__(self, explanation, code="romanian_hotel_setup_required"):
        super().__init__(explanation)
        self.code = code


def _normalized(text):
    return "".join(char for char in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(char)).lower()


def romanian_hotel_requested(text: str) -> bool:
    # Recognition is a bounded guard, not permission or inferred spending.
    return bool(re.search(
        r"\b(?:transfero|direct[\s-]*aeroport|aro[\s-]*palace|romanian\s+(?:coach\s*(?:\+|and|plus)\s*)?hotel|"
        r"hotel(?:ul|ului|uri)?\s+romanesc|(?:hotel(?:ul|ului|uri)?|cazare(?:a)?)\s+"
        r"(?:(?:in|din|la|at|near)\s+)?(?:romania|brasov))\b", _normalized(text)))


def romanian_hotel_instruction(text: str) -> bool:
    """Recognition only: report quotes must not swallow even negated reuse."""
    return any(romanian_hotel_requested(clause) and re.search(_ACTION, clause)
               for clause in re.split(r"[.;!?\n]", _normalized(text)))


def _reference_text(world, text):
    value = _normalized(text)
    for group in sorted(world.groups, key=lambda item: len(item.name), reverse=True):
        for label in (group.name, group.id):
            if label:
                value = re.sub(r"(?<!\w)"+re.escape(_normalized(label))+r"(?!\w)", " ", value)
    return value



def _input_change_kind(value, identifiers):
    """One set of input-change predicates for plain and report-masked reuse."""
    from .live_commands import _CALL_LIMIT, _TRAILING_LIMIT, _reference_dates, hotel_input_time_requested

    separate_references = re.split(r"[.;!?\n]|\b(?:and|si|also|plus)\b", value)
    extra_profile = any(re.search(_SAVED, part)
        and (re.search(_CONFIGURATION, part) or re.search(r"\b(?:transfer\w*|transport\w*)\b", part))
        and not romanian_hotel_requested(part) for part in separate_references)
    if extra_profile or re.search(r"\b(?:uk|apl|british|britanic\w*|bucharest[.\s-]*cab|after[\s-]+flight|post[\s-]*flight|dupa\s+zbor|initial\w*)\b", value):
        return "profil sau transfer suplimentar"
    if (re.search(r"\b(?:other|another|different|alt|alta|altul|diferit\w*)\b.{0,30}\b(?:hotel\w*|property|proprietat\w*|cazare)\b", value)
            or re.search(r"\b(?:stay|cazare|stai|dormi)\s+(?:at|in|la)\s+", value)):
        return "altă proprietate"
    if (hotel_input_time_requested(value, identifiers)
            or re.search(r"\b(?:rooms?|camer\w*|rest|odihn\w*|suitcases?|handbags?|luggage|baggage|bags?|bagaj\w*|valiz\w*|troler\w*|processing|procesar\w*|clearance|immigration|customs|window|interval\w*|margins?|marj\w*|buffers?|check[\s-]?in|check[\s-]?out|pickup|preluar\w*|boarding|imbarcar\w*)\b", value)):
        return "ședere, camere, bagaje sau marje de conexiune"
    if (re.search(r"\b(?:budget|buget\w*|adults?|adult[ai]|passengers?|pasager\w*|people|persons?|persoane|children|child|infants?|copii|copil\w*|wheelchair|accessib\w*|accesib\w*|dizabil\w*)\b|scaun\s+cu\s+rotile", value)
            or re.search(r"(?:\b(?:eur|euros?|ron|gbp|usd|lei|leu|lire|dolari|pounds?|dollars?)\b|[€£$])", value)):
        return "buget, componența grupului sau accesibilitate"
    route_value = value
    _, spans = _reference_dates(value, identifiers)
    for start, end in sorted(spans, reverse=True):
        intro = re.search(r"\b(?:from|din(?:\s+data\s+de)?)\s*$", route_value[:start])
        start = intro.start() if intro else start
        route_value = route_value[:start]+" "*(end-start)+route_value[end:]
    route_value = _TRAILING_LIMIT.sub(" ", _CALL_LIMIT.sub(" ", route_value))
    if (re.search(r"\b(?:to|from|via|through|towards|between|spre|catre|din|de la|pana la|intre|prin)\s+(?!(?:use|reuse|recover|recalculate|replan|search)\b)[a-z]", route_value)
            or re.search(r"(?:->|→|=>)|\b(?:destination|origin|destinati\w*|origine)\b", route_value)):
        return "rută"
    return None


def require_complete_hotel_report_inputs(world, case, groups, followup, reports, *, hotel_requested=False):
    """Report masking may remove facts, never separate package constraints.

    The report router has already grounded these assertions. Exclude only the
    selected entities' literal labels and the exact validated readiness phrase;
    extra bag/stay/budget instructions still require typed review.
    """
    from .live_commands import _DURATION_COUNT

    romanian = romanian_hotel_requested(_reference_text(world, followup))
    if not romanian and not hotel_requested:
        return
    for report in reports:
        value = _normalized(report.report_reference or "")
        labels = [label for group in world.groups if group.id in groups for label in (group.id, group.name)]
        resource = next((item for item in world.resources if item.id == getattr(report, "resource_id", None)), None)
        if resource is not None:
            for label in sorted({resource.id, resource.name, resource.source}, key=len, reverse=True):
                if label:
                    value = re.sub(r"(?<!\w)"+re.escape(_normalized(label))+r"(?!\w)", " known_service ", value)
        if report.type == "arrival_report" and report.ready_at_reference:
            # This exact phrase has already passed _readiness, including its
            # relation to the passenger subject and explicit date/offset.
            ready = _normalized(report.ready_at_reference)
            value = value.replace(ready, " " * len(ready), 1)
        for label in sorted(set(labels), key=len, reverse=True):
            if label:
                value = re.sub(r"(?<!\w)"+re.escape(_normalized(label))+r"(?!\w)", " selected_group ", value)
        generic_party = r"(?:adults?|adult[ai]|passengers?|pasager\w*|people|persons?|persoane)"
        counted_party = r"\b"+_DURATION_COUNT+r"\s+"+generic_party+r"\b"
        # 'Passengers G1 are ready' is a grounded subject, not a party edit.
        # A stated quantity remains visible to the same guard as plain reuse.
        if report.type == "arrival_report" and not re.search(counted_party, value):
            value = re.sub(r"\b"+generic_party+r"\s+(?:(?:of|din)\s+)?selected_group\b", " selected_group ", value)
            value = re.sub(r"\bselected_group\s+"+generic_party+r"\b", " selected_group ", value)
        change = _input_change_kind(value, (case.id, case.requested_flight_number))
        if change:
            raise RomanianHotelSetupRequired("Raportul include și "+change+". "
                "Declarația operațională rămâne înregistrată, dar revizuiește acele intrări în formular înainte de reutilizare; "
                "nu am eliminat condițiile din citat și nu am pornit căutarea pe configurația veche.",
                "romanian_hotel_setup_required" if romanian else "hotel_setup_required")


def romanian_hotel_reuse_explanation(inputs):
    return (" Am copiat exact configurația românească salvată: Hotel Aro Palace, "
        f"intrare {inputs.checkin_at.isoformat()}, ieșire {inputs.checkout_at.isoformat()}, "
        f"{inputs.rooms} camere, {inputs.suitcases} valize și {inputs.handbags} bagaje de mână; "
        f"marjă aeroport {inputs.airport_processing_minutes} minute și hotel {inputs.hotel_connection_minutes} minute. "
        "Serviciile Transfero, hotelul și zborul vor fi căutate din nou în limita curentă. "
        "Configurația nu confirmă locuri actuale, tarife sau rezervări.")


def resolve_saved_romanian_hotel(jobs, world, case, group_ids, modes, text, allowance, *, conn=None):
    """Resolve only an explicit current affirmative unchanged saved package.

    Callers supply the current or projected post-report world and remove only
    independently grounded report spans. Unexpected validation failures escape
    so a report transaction cannot commit under an unknown storage/code failure.
    """
    from .live_commands import _CALL_LIMIT, _TRAILING_LIMIT, mentioned
    from .recovery_jobs import RecoveryJobRequest
    from .romanian_hotel_profile import RomanianHotelProfileError
    from .romanian_hotel_recovery import REQUIRED_CALLS, validate_romanian_hotel_job

    value = _reference_text(world, text)
    if not romanian_hotel_requested(value):
        return None
    literal_groups = {group.id for group in world.groups if mentioned(text, group.id) or mentioned(text, group.name)}
    if any(other.id != case.id and (mentioned(text, other.id)
            or (other.requested_flight_number != case.requested_flight_number
                and mentioned(text, other.requested_flight_number))) for other in world.incidents):
        raise RomanianHotelSetupRequired("Cererea numește mai multe cazuri sau zboruri. Selectează un singur caz; nu am ignorat o referință pentru a reutiliza profilul.")
    if literal_groups != set(group_ids) or len(literal_groups) != 1:
        raise RomanianHotelSetupRequired("Numește exact un singur grup pentru pachetul românesc salvat. Nu am omis grupuri menționate sau ales un subset presupus.")
    if len(group_ids) != 1 or set(modes) != {"flight", "hotel", "bus"}:
        raise RomanianHotelSetupRequired("Configurația românească salvată necesită exact un grup și întregul traseu: autocar, hotel, autocar și zbor. Nu am extins sau redus serviciile cerute.")
    value = _TRAILING_LIMIT.sub(" ", _CALL_LIMIT.sub(" ", value))
    value = re.sub(r"\b(?:without|no|fara)\s+(?:supplier\s+)?(?:bookings?|reservations?|payments?|rezervari|plati)\b", " ", value)
    clauses = re.split(r"[.;!?\n]", value)
    references = [clause for clause in clauses if romanian_hotel_requested(clause)
                  and re.search(_SAVED, clause) and re.search(_CONFIGURATION, clause)]
    positive = any(re.search(_REUSE, clause) and (re.search(_RECOVERY, clause)
        or re.match(r"^\s*(?:(?:please|te rog|va rog)\s*[, :]?\s*)?"+_REUSE, clause)) for clause in references)
    if not positive:
        raise RomanianHotelSetupRequired(SETUP_EXPLANATION + " Pentru reutilizare, cere explicit recuperarea folosind configurația românească salvată; simpla menționare a furnizorului nu o autorizează.")
    if re.search(r"\b(?:if|whether|unless|daca|might|would|could|maybe|poate|eventual|not|no|don't|dont|never|nu|fara|without)\b", value):
        raise RomanianHotelSetupRequired("Reutilizarea pachetului românesc este negată, condițională sau ambiguă. Nu am pornit căutarea pe baza unei presupuneri.")
    change = _input_change_kind(value, (case.id, case.requested_flight_number))
    if change:
        raise RomanianHotelSetupRequired("Cererea precizează separat "+change+". Revizuiește aceste intrări în formular; nu le-am ignorat sau înlocuit cu configurația salvată.")
    if allowance < REQUIRED_CALLS:
        raise RomanianHotelSetupRequired(f"Pachetul românesc necesită minimum {REQUIRED_CALLS+1} apeluri totale: 1 interpretare și {REQUIRED_CALLS} pentru căutare și decizie. Limita nu a fost mărită.", "insufficient_call_allowance")
    profiles = getattr(jobs, "romanian_hotel_profiles", None)
    group = next((item for item in world.groups if item.id == group_ids[0]), None)
    try:
        if profiles is None or group is None:
            raise RomanianHotelSetupRequired(SETUP_EXPLANATION + " Configurația românească salvată pentru grup lipsește.")
        inputs = profiles.read(world, group, conn=conn)
    except RomanianHotelProfileError as exc:
        raise RomanianHotelSetupRequired(SETUP_EXPLANATION + " Configurația lipsește, este invalidă sau nu mai corespunde grupului și cazului. Nu am reutilizat alt profil.") from exc
    probe = RecoveryJobRequest(expected_version=world.version, idempotency_key="saved-romanian-hotel-validation",
        group_ids=group_ids, modes=modes, max_provider_calls=allowance, romanian_hotel_recovery=inputs)
    try:
        validate_romanian_hotel_job(world, probe)
    except DomainError as exc:
        if exc.status != 422:
            raise
        raise RomanianHotelSetupRequired("Configurația românească nu mai este validă pentru condițiile actuale. Revizuiește formularul; nu am mutat șederea sau modificat marjele. "+exc.message) from exc
    return inputs
