"""Ground current operator assertions and atomically record optional recovery work.

This module dispatches no providers. Interpretation is already durable; the only
side effects are one versioned report, a possible queued job, and their receipt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from pydantic import ValidationError

from .airport_catalog import lookup_airport
from .hotel_recovery import HotelRecoveryInputs, REQUIRED_CALLS, validate_hotel_job
from .hotel_recovery_profile import HotelRecoveryProfileError, read_hotel_recovery_profile
from .live_commands import (
    _DURATION_NUMBERS, _read_reference_date, _reference_pattern, hotel_input_time_requested, literal_call_limit, mentioned,
    normalized, reference_date,
)
from .operational_reports import (
    ArrivalReportInput, ResourceUnavailabilityInput, apply_arrival_report,
    apply_resource_unavailability,
)
from .providers.ai import LiveArrivalReport, LiveRecoveryIntent, LiveResourceUnavailability
from .recovery_jobs import RecoveryJobRequest
from .store import DomainError, Store


class Clarification(ValueError):
    """Known missing or contradictory current-utterance evidence; apply nothing."""


class RecoveryBlocked(ValueError):
    def __init__(self, code, explanation):
        self.code, self.explanation = code, explanation
        super().__init__(explanation)


def _fragment(text, quote):
    if not isinstance(quote, str) or not quote.strip():
        raise Clarification("Lipsește o declarație explicită în cererea curentă.")
    quote = quote.strip()
    start = text.casefold().find(quote.casefold())
    if start < 0:
        raise Clarification("Referința interpretată nu apare exact în cererea curentă.")
    return text[start:start + len(quote)], start


def _claim(text, quote, kind):
    claim, start = _fragment(text, quote)
    # Include the preceding clause so a model cannot drop 'if'/'do not' by
    # quoting only its affirmative suffix. Do not reinterpret a question as fact.
    boundary = max(text.rfind(char, 0, start) for char in ".!?;\n") + 1
    prefix = normalized(text[boundary:start])
    value = normalized(claim)
    conditional = r"\b(?:if|whether|daca|would|could|might|poate)\b"
    # Romanian 'cannot operate' is an explicit inability, not 'might'.
    assertion = re.sub(r"\bnu (?:mai )?poate (?:functiona|circula)\b", "explicit inability", value)
    if re.search(conditional, prefix + assertion) or "?" in claim or text[start + len(claim):].lstrip().startswith("?"):
        raise Clarification("O întrebare sau ipoteză condițională nu este un raport de operare. Declară explicit situația de planificare.")
    if re.search(r"\b(?:not|never|don't|do not|nu)\b", prefix):
        raise Clarification("Referința omite o negație; nu am înregistrat raportul.")
    if kind == "arrival_report":
        if re.search(r"\b(?:not|hasn't|haven't|isn't|aren't|nu)\b", value) or not re.search(r"\b(?:ready|readiness|pregatit[ai]?|pregatiti|pregatite|gata|disponibili)\b", value):
            raise Clarification("Declară pregătirea pasagerilor la aeroport, cu dată, oră și UTC/offset. Devierea sau ora zborului nu dovedește pregătirea grupului.")
    else:
        negative = r"\b(?:not|isn't|aren't|wasn't|hasn't|never|nu)\b.{0,35}\b(?:unavailable|broken|broke|defect\w*|stricat\w*|indisponibil\w*)\b"
        positive = r"\b(?:unavailable|broken|broke|out of service|defect\w*|stricat\w*|indisponibil\w*|nu functioneaza|nu mai circula|nu (?:mai )?poate (?:functiona|circula)|cannot operate)\b"
        if re.search(negative, value) or not re.search(positive, value):
            raise Clarification("Declară explicit indisponibilitatea serviciului identificat; nu transform o negație sau posibilitate în raport.")
    return claim


def _case(world, text, proposed):
    matches = [case for case in world.incidents if mentioned(text, case.id)]
    if not matches:
        matches = [case for case in world.incidents if mentioned(text, case.requested_flight_number)]
    try:
        matches = [case for case in matches if reference_date(text, (case.id, case.requested_flight_number)) in (None, case.date_utc)]
    except ValueError as exc:
        raise Clarification("Data cazului este ambiguă ori invalidă. Repetă comanda completă cu exact un caz/zbor, data YYYY-MM-DD, grupurile, serviciile și limita totală curentă de apeluri.") from exc
    if len(matches) != 1 or (proposed is not None and proposed != matches[0].id):
        raise Clarification("Numește exact un caz/zbor existent și data sa corectă; istoricul nu autorizează alegerea unui caz.")
    return matches[0]


def _all_groups(text):
    return bool(re.search(r"\b(?:all (?:the |linked )?groups|all (?:the )?passengers|toate grupurile|toti pasagerii|intregul caz)\b", normalized(text)))


def _group_mentions(world, case, text):
    explicit = {group.id for group in world.groups if mentioned(text, group.id)}
    names = {}
    for group in world.groups:
        names.setdefault(normalized(group.name), []).append(group)
    selected = set(explicit)
    for name, matches in names.items():
        if not mentioned(text, name):
            continue
        linked = [group for group in matches if group.incident_id == case.id and group.id in case.group_ids]
        candidates = linked or matches
        if len(candidates) == 1:
            selected.add(candidates[0].id)
        elif not explicit.intersection(group.id for group in candidates):
            raise Clarification("Numele grupului este comun mai multor grupuri. Numește ID-ul exact; nu extind un nume singular la mai multe grupuri.")
        # Exact IDs disambiguate a shared descriptive label without adding IDs.
    return selected


def _groups(world, case, text, ids, all_linked):
    linked = {group.id for group in world.groups if group.incident_id == case.id and group.id in case.group_ids}
    explicit = _group_mentions(world, case, text)
    if len(set(ids)) != len(ids):
        raise Clarification("Numește fiecare grup o singură dată.")
    if all_linked:
        if not _all_groups(text) or (explicit and explicit != linked) or (ids and set(ids) != linked):
            raise Clarification("Întregul caz necesită o cerere explicită pentru toate grupurile; nu extind un subset numit.")
        selected = linked
    else:
        selected = set(ids)
        if not selected or selected != explicit or not selected <= linked or (_all_groups(text) and selected != linked):
            raise Clarification("Numește exact grupurile legate de caz; nu reduc sau extind implicit selecția.")
    if not 1 <= len(selected) <= 24:
        raise Clarification("Selectează între 1 și 24 de grupuri existente ale cazului.")
    return sorted(selected)


def _airport(text, code, quote):
    from .airport_mentions import literal_airport_ids

    reference, start = _fragment(text, quote)
    matches = literal_airport_ids(reference)
    target = lookup_airport(code or "")
    if len(matches) != 1 or not target or matches != {target["id"]}:
        raise Clarification("Numește un aeroport fără ambiguitate, preferabil codul IATA; orașul singur nu selectează un aeroport.")
    other_airports = literal_airport_ids(text, reference_span=(start, start + len(reference)))
    if other_airports - {target["id"]}:
        raise Clarification("Declarația conține mai multe aeroporturi. Raportează separat grupurile aflate în locuri diferite.")
    return target["id"]


def _readiness_subject(world, case, claim, selected):
    """One common readiness predicate may cover a list, not another group's clause."""
    marker = re.search(r"\b(?:ready|readiness|pregatit[ai]?|pregatiti|pregatite|gata|disponibili)\b", normalized(claim))
    if marker is None:
        raise Clarification("Declară pregătirea grupurilor selectate.")
    clauses = re.split(r"[,;.\n]", claim[:marker.start()])
    subject = set()
    for clause in reversed(clauses):
        names = _group_mentions(world, case, clause)
        if not subject and _all_groups(clause):
            return
        if not names:
            if subject:
                break
            continue
        if subject:
            # Comma-separated group lists remain valid; a separate located/timed
            # subject must not inherit the next group's readiness predicate.
            residue = normalized(clause)
            for group in world.groups:
                for identifier in (group.id, group.name):
                    pattern = _reference_pattern(identifier)
                    if pattern:
                        residue = pattern.sub("", residue)
            residue = re.sub(r"\b(?:assume|presupune|passengers?|groups?|grupurile|grupul|pasagerii|si|and)\b", "", residue)
            if residue.strip(" :&-()"):
                break
        subject |= names
    if subject != set(selected):
        raise Clarification("Pregătirea trebuie declarată împreună pentru toate grupurile selectate; nu folosesc ora unui grup pentru altul.")


def _readiness(text, proposed, quote, command_received_at):
    reference, start = _fragment(text, quote)
    value = re.sub(r"(?<=\d)t(?=\d{2}:)", " ", normalized(reference))
    try:
        markers = list(re.finditer(r"\b(?:ready|readiness|pregatit[ai]?|pregatiti|pregatite|gata|disponibili)\b", normalized(text)))
        ready = [match for match in markers if match.end() <= start]
        if len(markers) != 1 or not ready or not re.fullmatch(r"[\s,:]*(?:(?:at|la|ora|from|de la|pe)\s*)?", normalized(text[ready[-1].end():start])):
            raise ValueError("time must explicitly describe passenger readiness, not a flight ETA")
        rest = normalized(text[ready[-1].end():start] + text[start + len(reference):])
        if re.search(r"[0-9]{1,2}:[0-9]{2}|\b(?:in|peste)\s+(?:[+-]?[0-9]+|thirty|treizeci)\s+(?:de\s+)?(?:minutes?|minute)\b", rest):
            raise ValueError("multiple readiness times")
        minute_words = _DURATION_NUMBERS
        duration = re.fullmatch(r"(?:in|peste)\s+([0-9]+|" + "|".join(re.escape(word) for word in sorted(minute_words, key=len, reverse=True)) + r")\s+(?:de\s+)?(?:minutes?|minute)", value)
        if duration:
            minutes = int(duration[1]) if duration[1].isascii() and duration[1].isdigit() else minute_words[duration[1]]
            if not 0 <= minutes <= 10080:
                raise ValueError("duration outside the planning horizon")
            received = datetime.fromisoformat(command_received_at)
            if received.tzinfo is None or received.utcoffset() is None:
                raise ValueError("durable command time required")
            received = received.astimezone(timezone.utc)
            anchor = received.replace(second=0, microsecond=0) + (timedelta(minutes=1) if received.second or received.microsecond else timedelta())
            actual = anchor + timedelta(minutes=minutes)
            if proposed is not None and datetime.fromisoformat(proposed) != actual:
                raise ValueError("model clock cannot replace the persisted command anchor")
            return actual, {"kind": "command_relative_minutes", "reference": reference,
                "command_received_at": command_received_at, "anchor_at": anchor.isoformat(),
                "rounding": "ceil_minute", "minutes": minutes}
        found = _read_reference_date(value)
        if not found:
            raise ValueError("full date required")
        tail = value[found[1]:].strip()
        match = re.fullmatch(r"(?:(?:at|la|ora)\s+)?(\d{2}):(\d{2})(?::00)?\s*(z|utc|gmt|[+-]\d{2}:\d{2})", tail)
        if not match:
            raise ValueError("explicit minute and timezone required")
        zone = "+00:00" if match[3] in ("z", "utc", "gmt") else match[3]
        actual = datetime.fromisoformat(f"{found[0]}T{match[1]}:{match[2]}:00{zone}")
        candidate = datetime.fromisoformat(proposed or "")
        if candidate.tzinfo is None or candidate.utcoffset() is None or candidate.second or candidate.microsecond or candidate != actual:
            raise ValueError("interpreted instant differs")
        return actual.astimezone(timezone.utc), None
    except (ValueError, TypeError) as exc:
        raise Clarification("Declară pregătirea cu data completă, HH:mm și UTC/offset sau explicit «în N minute». Nu folosesc ora zborului, date contradictorii ori ceasul modelului.") from exc


def _resource(world, text, proposed, quote):
    reference, _ = _fragment(text, quote)
    exact = [r for r in world.resources if mentioned(text, r.id)]
    matches = exact or [r for r in world.resources if reference.casefold() == r.name.casefold()]
    if len(matches) != 1 or matches[0].id != proposed or reference.casefold() not in (matches[0].id.casefold(), matches[0].name.casefold()):
        raise Clarification("Numește ID-ul exact al serviciului existent; un nume comun nu identifică una dintre mai multe cotații.")
    names = {}
    for item in world.resources:
        names.setdefault(normalized(item.name), []).append(item)
    unique_names = {items[0].id for name, items in names.items() if len(items) == 1 and mentioned(text, name)}
    if unique_names - {matches[0].id}:
        raise Clarification("Declarația numește mai multe servicii. Citează doar serviciul indisponibil și starea lui; nu mut o afirmație pe alt serviciu.")
    # The selected service's own clause must state the outage. A following
    # 'another bus is unavailable' cannot change 'Q1 is available' into its opposite.
    target_reference = matches[0].id if exact else reference
    target = _reference_pattern(target_reference).search(normalized(text))
    left = max(text.rfind(char, 0, target.start()) for char in ",;.\n") + 1
    ends = [text.find(char, target.end()) for char in ",;.\n"]
    right = min((end for end in ends if end >= 0), default=len(text))
    clause = text[left:right].strip()
    _claim(clause, clause, "resource_unavailability")
    return matches[0]


def _total_limit(text, intent):
    literal = literal_call_limit(text)
    total = min(27, literal) if literal is not None else 8
    quote = intent.call_limit_reference
    if (intent.max_total_calls is not None and quote and normalized(quote) in normalized(text)
            and re.search(r"\b(?:calls?|apeluri|apel)\b", normalized(quote))):
        total = min(total, intent.max_total_calls)
    return total


def _recovery_requested(text):
    value = normalized(text)
    actions = list(re.finditer(r"\b(?:recover|recalculate|replan|search|recupereaza|recalculeaza|replanifica|cauta)\b", value))
    actions += list(re.finditer(r"\b(?:start|begin|run|porneste|incepe|cer)\b.{0,25}\b(?:recovery|search|cautarea|recuperarea)\b", value))
    for action in actions:
        boundary = max(value.rfind(char, 0, action.start()) for char in ".!?;\n") + 1
        prefix = value[boundary:action.start()]
        prefix = re.sub(r"\bnu (?:mai )?(?:functioneaza|circula|poate (?:functiona|circula))\b", "", prefix)
        if re.search(r"\b(?:not|no|don't|dont|never|nu|fara|without|if|whether|daca|might|would)\b", prefix):
            return False
    return bool(actions)


def _job_request(world, request, case, groups, intent, total, report):
    allowance = total - 1
    if allowance < 3:
        raise RecoveryBlocked("insufficient_call_allowance", f"Limita totală {total} include 1 interpretare; rămân {max(0, allowance)} apeluri, sub minimul jobului. Nu am mărit limita.")
    if intent.requires_input_changes or len(request.text) > 2000:
        raise RecoveryBlocked("recovery_prerequisite_failed", "Datele suplimentare sau lungimea cererii necesită revizuire înaintea căutării.")
    if any(group.id in groups and group.status == "confirmed" for group in world.groups) or any(offer.group_id in groups and offer.status == "confirmed" for offer in world.offers):
        raise RecoveryBlocked("recovery_prerequisite_failed", "Grupurile cu rezervări confirmate necesită tratare separată; nu le înlocuiesc rezervările.")
    inputs = None
    report_text, report_start = _fragment(request.text, report.report_reference)
    followup = request.text[:report_start] + " " * len(report_text) + request.text[report_start + len(report_text):]
    hotel = "hotel" in intent.modes or bool(re.search(r"\b(?:hotel\w*|cazare\w*|accommodation)\b", normalized(followup)))
    if hotel:
        if len(groups) != 1 or set(intent.modes) != {"flight", "hotel", "bus"}:
            raise RecoveryBlocked("hotel_setup_required", "Traseul cu hotel necesită exact un grup și hotel, ambele transferuri și zbor.")
        # The report's location/readiness is not a hotel edit. Other requested
        # dates/times remain in the actual follow-up and must not be ignored.
        if hotel_input_time_requested(followup, (case.id, case.requested_flight_number)):
            raise RecoveryBlocked("hotel_setup_required", "Revizuiește schimbările de date/ore în formularul hotelului; nu înlocuiesc cererea cu datele salvate.")
        try:
            group = next(group for group in world.groups if group.id == groups[0])
            inputs = HotelRecoveryInputs.model_validate(read_hotel_recovery_profile(world, group))
        except (HotelRecoveryProfileError, ValidationError) as exc:
            raise RecoveryBlocked("hotel_setup_required", "Configurația hotelului nu mai corespunde poziției și datelor actuale. Revizuiește formularul; nu presupun coordonate, date sau o preluare nouă.") from exc
        if allowance < REQUIRED_CALLS:
            raise RecoveryBlocked("insufficient_call_allowance", f"Hotelul necesită {REQUIRED_CALLS + 1} apeluri totale, inclusiv interpretarea. Limita curentă {total} nu a fost mărită.")
    payload = RecoveryJobRequest(expected_version=world.version,
        idempotency_key="live-command-" + Store.fingerprint({"key": request.idempotency_key, "scenario": world.scenario_id}),
        group_ids=groups, objective=intent.objective, modes=intent.modes,
        max_provider_calls=allowance, instructions=request.text, hotel_recovery=inputs)
    if inputs is not None:
        try:
            validate_hotel_job(world, payload)
        except DomainError as exc:
            raise RecoveryBlocked("hotel_setup_required", "Configurația salvată nu mai este fezabilă pentru limitele actuale. Revizuiește formularul; nu am avansat preluarea.") from exc
    return payload


def _unsupported_airport_change(text, *, allow_diversion_background=False):
    value = normalized(text)
    if re.search(r"\b(?:(?:cannot|can not|can't|can’t|unable to|not able to)\s+(?:safely\s+)?land|nu\s+(?:mai\s+)?(?:poate|pot)\s+(?:sa\s+)?ateriz\w*)\b", value):
        return True
    if allow_diversion_background:
        # Only descriptive past context may accompany an explicit arrival
        # report. Its literal passenger/readiness grounding still runs below.
        # Present movements and instructions ("divert", "is diverting") remain
        # unsupported even when another clause reports ready passengers.
        value = re.sub(r"\b(?:after (?:the )?diversion|dupa (?:deviere|devierea)|(?:was|has been|had been) diverted|a fost (?:deviat|deviata|redirectionat))\b", " ", value)
    if re.search(r"\b(?:divert(?:ed|ing|s)?|diversions?|deviat\w*|deviaz\w*|deviere\w*|divertat\w*|redirection(?:at|eaza)\w*)\b", value):
        return True
    from .airport_mentions import literal_airport_ids
    for clause in re.split(r"[.;!?\n]", text):
        value = normalized(clause)
        if re.search(r"\b(?:closed|closures?|inchis\w*|inchidere\w*)\b", value) and (
                re.search(r"\b(?:airports?|aeroport\w*|runways?|pist[ae]\w*)\b", value)
                # "CLOSED" is itself a catalog name token for an old airport;
                # it cannot identify the subject of an unrelated closure.
                or literal_airport_ids(re.sub(r"\b(?:closed|closures?)\b", " ", clause, flags=re.IGNORECASE))):
            return True
        for state in re.finditer(r"\b(?:unavailable|indisponibil\w*)\b", clause, re.IGNORECASE):
            subject = re.sub(r"\b(?:is|has become|became|remains|este|e|a devenit|devine|ramane|rămâne)\s*$", "",
                             clause[:state.start()], flags=re.IGNORECASE).strip()
            subject = re.split(r",|\b(?:and|but|si|și|dar)\b", subject, flags=re.IGNORECASE)[-1].strip()
            # Keep actual service-outage reports valid, including a bus/hotel
            # described at an airport. Airport names in its location do not
            # turn the service into an airport-unavailability report.
            if re.search(r"\b(?:bus(?:es)?|coaches?|hotel\w*|autobuz\w*|autocar\w*|transfer\w*|services?|servici\w*)\b", normalized(subject)):
                continue
            if re.search(r"\b(?:airports?|aeroport\w*|runways?|pist[ae]\w*)\b", normalized(subject)) or literal_airport_ids(subject):
                return True
    return False


def route_operational_report(commands, request, world, interpreted):
    raw = interpreted.get("operational_report")  # older durable records omit it
    # Airport restrictions and aircraft movements have no live report contract.
    # An omitted clause must not become recovery using unchanged airport facts,
    # even when the model retained a separate, otherwise valid arrival report.
    if _unsupported_airport_change(request.text, allow_diversion_background=isinstance(raw, dict) and raw.get("type") == "arrival_report"):
        return commands._reply(request, world, "Cererea conține un posibil raport operațional despre închiderea aeroportului, imposibilitatea aterizării sau devierea avionului. Actualizarea acestei restricții aeroportuare nu este implementată în acest flux. Nu am înregistrat schimbarea, dedus poziția ori pregătirea pasagerilor sau pornit recuperarea pe condițiile vechi. Raportul explicit de poziție/pregătire poate fi trimis separat. Interpretarea a folosit 1 apel.")
    if raw is None:
        # A dropped report must not become a global event/plan for other groups.
        if (interpreted.get("events") or interpreted.get("strategies") or interpreted.get("recovery")) and re.search(
            r"\b(?:arrived|readiness|unavailable|broken|sosit|ajuns|aterizat|indisponibil\w*|stricat\w*|defect\w*|(?:ready|gata|pregatit(?:a|i|e)?)\s+(?:at|in|la|peste)|nu (?:mai )?(?:functioneaza|circula|poate functiona))\b", normalized(request.text)):
            return commands._reply(request, world, "Cererea conține un posibil raport operațional, dar nu este identificat explicit. Repetă raportul cu serviciul sau cazul/grupurile și pregătirea exactă. Nu am aplicat schimbări ori planuri globale. Interpretarea a folosit 1 apel.")
        return None
    try:
        if not isinstance(raw, dict):
            raise ValueError("report object required")
        model = LiveArrivalReport if raw.get("type") == "arrival_report" else LiveResourceUnavailability
        report = model.model_validate(raw, strict=True)
        if interpreted.get("events") or interpreted.get("strategies"):
            raise Clarification("Nu combin raportul cu evenimente sau planuri globale. Separă celelalte modificări; nimic nu a fost aplicat.")
        claim = _claim(request.text, report.report_reference, report.type)
        if _recovery_requested(claim):
            raise Clarification("Raportul și cererea de căutare trebuie identificate separat; nu omit servicii sau condiții ascunse în citatul raportului.")
        case, groups, readiness_basis = None, [], None
        if isinstance(report, LiveArrivalReport):
            case = _case(world, request.text, report.incident_id)
            groups = _groups(world, case, request.text, report.group_ids, report.all_linked_groups)
            # The assertion itself, not just a different request clause, binds scope.
            _groups(world, case, claim, report.group_ids, report.all_linked_groups)
            _readiness_subject(world, case, claim, groups)
            airport = _airport(claim, report.airport_code, report.airport_reference)
            with commands.store.connect() as conn:
                row = conn.execute("SELECT created_at FROM live_command_intents WHERE request_key=? AND fingerprint=? AND scenario_id=?",
                    ("command:" + request.idempotency_key, Store.fingerprint(request.model_dump()), world.scenario_id)).fetchone()
            ready, readiness_basis = _readiness(claim, report.ready_at, report.ready_at_reference, row[0] if row else "")
            typed = ArrivalReportInput(expected_version=request.expected_version,
                idempotency_key=request.idempotency_key, description=claim,
                group_ids=groups, airport_code=airport, ready_at=ready)
        else:
            resource = _resource(world, claim, report.resource_id, report.resource_reference)
            typed = ResourceUnavailabilityInput(expected_version=request.expected_version,
                idempotency_key=request.idempotency_key, description=claim)
        raw_recovery = interpreted.get("recovery")
        recovery, total = None, None
        if raw_recovery is not None:
            recovery = LiveRecoveryIntent.model_validate(raw_recovery, strict=True)
            if recovery.action != "start" or not _recovery_requested(request.text):
                raise Clarification("Căutarea trebuie cerută explicit în comanda curentă, pentru cazul și grupurile numite.")
            selected_case = _case(world, request.text, recovery.incident_id)
            selected_groups = _groups(world, selected_case, request.text, recovery.group_ids, recovery.all_linked_groups)
            if case is not None and (case.id != selected_case.id or groups != selected_groups):
                raise Clarification("Raportul de poziție și recuperarea trebuie să numească exact același caz și aceleași grupuri; nu reduc sau extind selecția.")
            case, groups = selected_case, selected_groups
            total = _total_limit(request.text, recovery)
        elif _recovery_requested(request.text):
            raise Clarification("Cererea include recuperare, dar interpretarea nu îi stabilește cazul, grupurile și serviciile. Repetă comanda completă; nu am aplicat doar o parte.")
    except (Clarification, ValidationError, ValueError) as exc:
        explanation = str(exc) if isinstance(exc, Clarification) else "Interpretarea raportului este incompletă sau invalidă. Repetă comanda completă cu referințele exacte."
        return commands._reply(request, world, explanation + " Nu am înregistrat un raport sau pornit o căutare. Interpretarea a folosit 1 apel.")

    def apply(current):
        if current.scenario_id != world.scenario_id:
            raise DomainError(409, "Operation changed while interpreting this report")
        result = (apply_arrival_report(current, case.id, typed, readiness_basis=readiness_basis) if isinstance(report, LiveArrivalReport)
                  else apply_resource_unavailability(current, resource.id, typed))
        result.update(mode="ai", command_interpretation_calls=1)
        if case is not None:
            result["incident_id"] = case.id
        result["explanation"] += " Declarație a operatorului, nu confirmare de aterizare, permisiune aeroportuară sau rezervare. Interpretarea a folosit 1 apel."
        if recovery is not None:
            result["recovery_total_call_limit"] = total
        return result

    def enqueue(conn, response):
        if recovery is None:
            return
        current = commands.store._load(conn)
        try:
            job_request = _job_request(current, request, case, groups, recovery, total, report)
            try:
                result = commands.jobs.start_in_transaction(conn, current, case.id, job_request, supersede_active=True)
            except DomainError as exc:
                code = (exc.details or {}).get("code")
                if code not in ("recovery_queue_full", "recovery_group_active", "recovery_handoff_limit"):
                    raise
                raise RecoveryBlocked("recovery_queue_unavailable", "Coada este ocupată sau un job deține deja grupurile. Verifică progresul; nu am trimis o altă căutare.") from exc
            response["recovery_job"] = result["job"]
            response["explanation"] += (f" Recuperarea pentru {case.id}, grupurile {', '.join(groups)}, este în coadă. "
                f"Limita totală {total}: 1 interpretare și cel mult {total - 1} apeluri ale jobului. "
                "Niciun itinerar găsit sau rezervare efectuată nu este confirmat de această înregistrare.")
            if result["job"].get("supersedes_job_ids"):
                response["explanation"] += " Cererea nouă înlocuiește încercările depășite pentru grupurile selectate. Așteaptă încheierea apelurilor deja trimise; un rezultat incert cere revizuire înainte de alte apeluri."
        except RecoveryBlocked as exc:
            response["recovery_blocked"] = {"code": exc.code, "explanation": exc.explanation}
            response["explanation"] += " Raport înregistrat; căutarea nu a pornit. " + exc.explanation

    return commands.store.mutate("command:" + request.idempotency_key, request.model_dump(),
                                 request.expected_version, apply, on_commit=enqueue)
