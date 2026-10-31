"""Durable interpretation and explicit case binding for live voice/text searches.

No source calls occur here except the existing single command interpretation.
The existing recovery worker owns all supplier searches and scoped plan commits.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date

from pydantic import ValidationError

from .providers.ai import LiveRecoveryIntent
from .providers.base import ProviderError
from .recovery_jobs import RecoveryJobRequest
from .hotel_recovery import HotelRecoveryInputs, REQUIRED_CALLS, validate_hotel_job
from .hotel_recovery_profile import HotelRecoveryProfileError, read_hotel_recovery_profile
from .store import DomainError, Store, utcnow


def normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


_NUMBERS = {
    "zero": 0, "one": 1, "un": 1, "unu": 1, "two": 2, "doua": 2, "doi": 2,
    "three": 3, "trei": 3, "four": 4, "patru": 4, "five": 5, "cinci": 5,
    "six": 6, "sase": 6, "seven": 7, "sapte": 7, "eight": 8, "opt": 8,
    "nine": 9, "noua": 9, "ten": 10, "zece": 10,
    "eleven": 11, "unsprezece": 11, "twelve": 12, "doisprezece": 12, "douasprezece": 12,
    "thirteen": 13, "treisprezece": 13, "fourteen": 14, "paisprezece": 14,
    "fifteen": 15, "cincisprezece": 15, "sixteen": 16, "saisprezece": 16,
    "seventeen": 17, "saptesprezece": 17, "eighteen": 18, "optsprezece": 18,
    "nineteen": 19, "nouasprezece": 19, "twenty": 20, "douazeci": 20,
}
for _unit, _english, _romanian in ((1, "one", "unu"), (2, "two", "doua"), (3, "three", "trei"),
                                 (4, "four", "patru"), (5, "five", "cinci"), (6, "six", "sase"), (7, "seven", "sapte")):
    _NUMBERS["twenty " + _english] = 20 + _unit
    _NUMBERS["twenty-" + _english] = 20 + _unit
    _NUMBERS["douazeci si " + _romanian] = 20 + _unit
_NUMBERS.update({"douazeci si un": 21, "douazeci si doi": 22})
_COUNT = r"(\d+|" + "|".join(re.escape(word) for word in sorted(_NUMBERS, key=len, reverse=True)) + r")"
_CALL_UNIT = r"\s*(?:de\s+)?(?:(?:api|provider|supplier|external|total)\s+){0,3}(?:calls?|apeluri|apel)\b"
_CALL_LIMIT = re.compile(r"\b(?:maximum|maxim|max|at most|no more than|cel mult|limit(?:a)?)(?: to| of| de)?\s*" + _COUNT + _CALL_UNIT)
_TRAILING_LIMIT = re.compile(r"\b" + _COUNT + _CALL_UNIT + r"\s+(?:maximum|maxim|max|in total|total|overall)\b")


def literal_call_limit(text):
    """A lower explicit numeric/short spoken limit wins over model extraction."""
    values = [int(m[1]) if m[1].isdigit() else _NUMBERS[m[1]]
              for pattern in (_CALL_LIMIT, _TRAILING_LIMIT) for m in pattern.finditer(normalized(text))]
    return min(values) if values else None


def _reference_pattern(identifier):
    # Spoken flight numbers may contain spaces/hyphens between their characters.
    chars = re.sub(r"[\s-]", "", normalized(identifier))
    return re.compile(r"(?<![a-z0-9])" + r"[\s-]*".join(map(re.escape, chars)) + r"(?![a-z0-9])") if chars else None


def mentioned(text, identifier):
    pattern = _reference_pattern(identifier)
    return bool(pattern and pattern.search(normalized(text)))


_MONTHS = {name: number for number, names in enumerate((
    ("ianuarie", "january"), ("februarie", "february"), ("martie", "march"),
    ("aprilie", "april"), ("mai", "may"), ("iunie", "june"),
    ("iulie", "july"), ("august",), ("septembrie", "september"),
    ("octombrie", "october"), ("noiembrie", "november"), ("decembrie", "december"),
), 1) for name in names}
_MONTH = "(?:" + "|".join(_MONTHS) + ")"
_DATE_PATTERNS = (
    (re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\w)"), "ymd"),
    (re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?!\w)"), "dmy"),
    (re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s*(" + _MONTH + r")\s*,?\s*(\d{4})(?!\w)"), "dmy"),
    (re.compile(r"(" + _MONTH + r")\s*(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})(?!\w)"), "mdy"),
)
_DATE_INTRO = r"(?:(?:din\s+data\s+de|de\s+pe|dated|date|data|din|from|on|pe)\s*)?"
_DATE_RANGE = r"(?:[-–—/.]|to|through|until|and|or|si|sau|pana\s+la)"
_DATE_HINT = re.compile(r"^(?:\d+(?:\s*[-/.–—]|\s*" + _DATE_RANGE + r"\s*\d|\s*" + _MONTH + r"(?![a-z])|\s*[a-z]+\s*,?\s*\d{4})|" + _MONTH + r"\s*\d|(?:today|tomorrow|yesterday|azi|maine|ieri)\b)")


def _read_reference_date(value):
    """Return an attached date and its consumed length; ambiguous dates fail closed."""
    for pattern, order in _DATE_PATTERNS:
        match = pattern.match(value)
        if not match:
            continue
        parts = {key: _MONTHS[part] if part in _MONTHS else int(part)
                 for key, part in zip(order, match.groups())}
        if order == "dmy" and match[2].isdigit() and 1 <= parts["d"] <= 12 and 1 <= parts["m"] <= 12 and parts["d"] != parts["m"]:
            raise ValueError("numeric date has ambiguous month/day order")
        parsed = date(parts["y"], parts["m"], parts["d"]).isoformat()
        # An exact first date must not conceal a following range/alternative.
        remainder = value[match.end():]
        if re.match(r"^\s*" + _DATE_RANGE + r"\s*(?:\d|" + _MONTH + r")", remainder):
            raise ValueError("ambiguous case date")
        if remainder.lstrip().startswith(",") and _DATE_HINT.match(remainder.lstrip()[1:].lstrip()):
            raise ValueError("multiple case dates")
        return parsed, match.end()
    if _DATE_HINT.match(value):
        raise ValueError("invalid or ambiguous case date")
    return None


def _reference_dates(text, identifiers):
    """Only dates attached to a case/flight name bind its identity.

    A later 'search alternatives for DATE' clause remains supplier instructions,
    not a second incident date. This deliberately bounded parser is not a
    general natural-language calendar or ASR confidence estimator.
    """
    value, dates, spans = normalized(text), set(), []
    for identifier in identifiers:
        pattern = _reference_pattern(identifier)
        if pattern is None:
            continue
        for reference in pattern.finditer(value):
            intro = re.match(r"^[\s,():]*" + _DATE_INTRO, value[reference.end():])
            start = reference.end() + intro.end()
            suffix = value[start:]
            found = _read_reference_date(suffix)
            if found:
                dates.add(found[0])
                spans.append((start, start + found[1]))
            # Also support 'flight dated DATE REF', without borrowing a date
            # from an unrelated preceding search or passenger deadline clause.
            prefix = re.search(r"\b(?:flight|zbor(?:ul|ului)?|case|caz(?:ul|ului)?)\s+" + _DATE_INTRO + r"([^,;]{1,70})\s*$", value[:reference.start()])
            if prefix:
                before = prefix[1].strip()
                found = _read_reference_date(before)
                if found and not before[found[1]:].strip():
                    dates.add(found[0])
                    start = prefix.start(1) + len(prefix[1]) - len(prefix[1].lstrip())
                    spans.append((start, start + found[1]))
    if len(dates) > 1:
        raise ValueError("conflicting case dates")
    return next(iter(dates), None), spans


def reference_date(text, identifiers):
    return _reference_dates(text, identifiers)[0]


def hotel_input_time_requested(text, identifiers):
    """Saved inputs cannot silently replace another requested date or time.

    Exact case dates are identity, not hotel edits. Other temporal instructions
    require the typed form, even when the model missed requires_input_changes.
    This is deliberately bounded to saved-profile reuse, not general searches.
    """
    value = normalized(text)
    _, spans = _reference_dates(text, identifiers)
    for start, end in sorted(spans, reverse=True):
        value = value[:start] + " " * (end - start) + value[end:]
    temporal = (
        r"[0-9]{1,4}\s*[-/.]\s*[0-9]{1,2}",
        r"[0-9]{1,2}(?:st|nd|rd|th)?\s*" + _MONTH,
        _MONTH + r"\s*[0-9]{1,2}",
        r"[0-9]{1,2}:[0-9]{2}",
        r"\b(?:tomorrow|today|yesterday|tonight|maine|poimaine|azi|astazi|ieri|diseara|weekend|week|saptamana|monday|tuesday|wednesday|thursday|friday|saturday|sunday|luni|marti|miercuri|joi|vineri|sambata|duminica)\b",
        r"\b(?:at|by|after|before|la|dupa|inainte de|pana la)\s*(?:ora\s*)?" + _COUNT + r"\b",
        r"\b" + _COUNT + r"\s*(?:minutes?|hours?|days?|nights?|minute|ore|zile|nopti)\b",
        r"\b(?:check.?in|check.?out|pickup|preluare)\s*(?:at|la)?\s*" + _COUNT + r"\b",
        r"\b(?:earlier|later|early|late|mai devreme|mai tarziu)\b",
        r"\b(?:change|move|shift|delay|advance|schimba|schimb|muta|aman|devanseaza|modifica)\b.{0,50}\b(?:date|time|data|ora|check.?in|check.?out|pickup|preluare)\b",
    )
    return any(re.search(pattern, value) for pattern in temporal)


class LiveCommands:
    def __init__(self, store, jobs):
        self.store, self.jobs = store, jobs
        with store.connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS live_command_intents (
                request_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                scenario_id TEXT NOT NULL, state TEXT NOT NULL,
                interpretation TEXT, created_at TEXT NOT NULL)""")

    async def interpret(self, request, world, ai):
        key = "command:" + request.idempotency_key
        fingerprint = Store.fingerprint(request.model_dump())
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT fingerprint,scenario_id,state,interpretation FROM live_command_intents WHERE request_key=?", (key,)).fetchone()
            if row:
                if row[0] != fingerprint or row[1] != world.scenario_id:
                    raise DomainError(409, "This command key already identifies a different request or operation")
                if row[2] == "interpreted":
                    return json.loads(row[3])
                raise DomainError(503, "The earlier command interpretation has an unconfirmed outcome or usage. It was not automatically repeated; review before explicitly starting another request.", {"interpretation_outcome": "unknown", "automatic_retry": False}, applied=None)
            conn.execute("INSERT INTO live_command_intents VALUES(?,?,?,?,?,?)", (key, fingerprint, world.scenario_id, "dispatched", None, utcnow().isoformat()))
        # A literal zero allowance must prevent even the first interpretation.
        if literal_call_limit(request.text) == 0:
            result = {"events": [], "strategies": [], "focus_id": None, "recovery": None,
                      "explanation": "Limita de 0 apeluri nu permite interpretarea sau căutarea. Niciun apel nu a fost trimis.",
                      "_zero_allowance": True, "_interpretation_calls": 0}
        else:
            try:
                args = (request.text, world.model_dump(mode="json"))
                result = await ai.interpret(*args, request.context) if request.context else await ai.interpret(*args)
                result = {**result, "_interpretation_calls": 1}
            except BaseException as exc:
                with self.store.connect() as conn:
                    conn.execute("UPDATE live_command_intents SET state='unknown' WHERE request_key=?", (key,))
                if isinstance(exc, ProviderError):
                    raise DomainError(502, "Command interpretation failed; upstream usage may be billable. No recovery job was queued and the same request will not dispatch another interpretation.", {"interpretation_outcome": "unknown", "automatic_retry": False}, applied=None) from exc
                raise
        with self.store.connect() as conn:
            conn.execute("UPDATE live_command_intents SET state='interpreted',interpretation=? WHERE request_key=?", (json.dumps(result, allow_nan=False), key))
        return result

    def _reply(self, request, world, explanation, *, calls=1, **extra):
        return self.store.remember("command:" + request.idempotency_key, request.model_dump(), world.version,
                                   {"applied": False, "mode": "ai", "explanation": explanation,
                                    "command_interpretation_calls": calls, **extra})

    def route(self, request, world, interpreted):
        if interpreted.get("_zero_allowance"):
            return self._reply(request, world, interpreted["explanation"], calls=0)
        from .live_operational_commands import route_operational_report
        reported = route_operational_report(self, request, world, interpreted)
        if reported is not None:
            return reported
        raw = interpreted.get("recovery")
        if raw is None:
            # A dropped recovery intent must never turn an explicitly named
            # case into global planning. Generic event commands and requests
            # without a case/flight binding retain their existing semantics.
            if interpreted.get("strategies") and not interpreted.get("events") and any(
                mentioned(request.text, case.id) or mentioned(request.text, case.requested_flight_number)
                for case in world.incidents
            ):
                return self._reply(request, world, "Cererea numește un caz sau zbor existent, dar interpretarea nu stabilește recuperarea acelui caz. Cere explicit căutarea pentru cazul sau zborul respectiv. Nu am pornit căutarea și nu am aplicat un plan global. Interpretarea a folosit 1 apel.")
            return None
        try:
            intent = LiveRecoveryIntent.model_validate(raw, strict=True)
        except ValidationError as exc:
            raise DomainError(422, "AI returned an invalid scoped recovery intent; no job was queued") from exc
        current = self.store.read()
        if current.version != world.version or current.scenario_id != world.scenario_id:
            raise DomainError(409, "Operation changed while interpreting this request; refresh before starting recovery")
        if interpreted.get("events") or interpreted.get("strategies") or intent.requires_input_changes:
            return self._reply(request, world, "Nu am pornit căutarea și nu am modificat datele. Înregistrează separat noile constrângeri ale cazului, apoi cere recuperarea pentru zborul sau cazul explicit. Interpretarea a folosit 1 apel.")

        matches = [case for case in world.incidents if mentioned(request.text, case.id)]
        if not matches:
            matches = [case for case in world.incidents if mentioned(request.text, case.requested_flight_number)]
        try:
            dated = [(case, reference_date(request.text, (case.id, case.requested_flight_number))) for case in matches]
        except ValueError:
            return self._reply(request, world, "Data asociată cazului sau zborului este ambiguă ori invalidă. Repetă comanda completă corectată: ID-ul exact al cazului/zborului, o singură dată YYYY-MM-DD, grupul/grupurile, serviciile cerute și limita totală explicită de apeluri pentru noua cerere. Nu am pornit căutarea. Interpretarea a folosit 1 apel.")
        matches = [case for case, explicit_date in dated if explicit_date is None or explicit_date == case.date_utc]
        if dated and not matches:
            return self._reply(request, world, "Data asociată cazului sau zborului nu corespunde unui caz existent. Verifică data și identificatorul; nu am pornit căutarea. Interpretarea a folosit 1 apel.")
        if len(matches) != 1:
            choices = "; ".join(f"{case.id}: {case.requested_flight_number}, {case.date_utc}" for case in matches[:5])
            return self._reply(request, world, "Specifică explicit un singur caz sau zbor existent și data dacă există mai multe cazuri. Nu am pornit nicio căutare. Interpretarea a folosit 1 apel." + (" Cazuri: " + choices if choices else ""))
        case = matches[0]
        if intent.incident_id is not None and intent.incident_id.upper() != case.id.upper():
            return self._reply(request, world, "Identitatea propusă de interpretare nu corespunde cazului numit în cerere. Repetă ID-ul exact al cazului; nu am pornit căutarea. Interpretarea a folosit 1 apel.")
        if intent.action == "status":
            jobs = self.jobs.list(case.id)["jobs"]
            job = jobs[0] if jobs else None
            explanation = (f"Caz {case.id}: {job['status']}; {job['message']} Apeluri ale jobului: {job['calls_dispatched']}/{job['max_provider_calls']}." if job else f"Cazul {case.id} nu are niciun job de recuperare înregistrat.")
            return self._reply(request, world, explanation + " Verificarea prin limbaj natural a folosit 1 apel de interpretare.", incident_id=case.id, recovery_job=job)

        literal = literal_call_limit(request.text)
        reference = intent.call_limit_reference
        model_limit = intent.max_total_calls
        # A quoted phrase with an explicit call unit may clarify spoken numbers;
        # an ambiguous reference may lower, but never increase, the default.
        verified_reference = bool(reference and normalized(reference) in normalized(request.text) and re.search(r"\b(?:calls?|apeluri|apel)\b", normalized(reference)))
        total_limit = min(27, literal) if literal is not None else 8
        if model_limit is not None and verified_reference:
            total_limit = min(total_limit, model_limit)
        allowance = total_limit - 1
        if allowance < 3:
            return self._reply(request, world, f"Limita totală este {total_limit} apeluri; interpretarea a folosit 1. Rămân {max(0, allowance)}, sub minimul de 3 al jobului. Nu am pornit căutarea și nu am mărit limita.", recovery_total_call_limit=total_limit)
        if len(request.text) > 2000:
            return self._reply(request, world, "Cererea de recuperare depășește 2000 de caractere. Scurteaz-o fără a omite condiții; nu am pornit căutarea. Interpretarea a folosit 1 apel.")
        linked = {group.id: group for group in world.groups if group.incident_id == case.id and group.id in case.group_ids}
        confirmed = {group.id for group in linked.values() if group.status == "confirmed"} | {offer.group_id for offer in world.offers if offer.status == "confirmed"}
        if intent.all_linked_groups:
            explicit_groups = any(mentioned(request.text, group.id) or mentioned(request.text, group.name) for group in world.groups)
            if intent.group_ids or explicit_groups:
                return self._reply(request, world, "Cererea numește grupuri, dar interpretarea propune întregul caz. Precizează grupurile selectate; nu am extins căutarea și nu am pornit-o. Interpretarea a folosit 1 apel.")
            groups = sorted(set(linked) - confirmed)
        else:
            groups = sorted(set(intent.group_ids))
            if not groups or any(group_id not in linked or not (mentioned(request.text, group_id) or mentioned(request.text, linked[group_id].name)) for group_id in groups):
                return self._reply(request, world, "Numește explicit grupurile existente legate de acest caz; nu am selectat un subset presupus. Nicio căutare pornită; interpretarea a folosit 1 apel.")
        if not groups or len(groups) > 24 or set(groups) & confirmed:
            return self._reply(request, world, "Selectează între 1 și 24 de grupuri legate de caz, fără rezervări confirmate. Nu am pornit căutarea și nu am înlocuit rezervări. Interpretarea a folosit 1 apel.")
        hotel_inputs = None
        hotel_requested = "hotel" in intent.modes or bool(re.search(
            r"\b(?:hotel(?:ul|ului|uri)?|cazare|cazarea|accommodation)\b", normalized(request.text)))
        if hotel_requested:
            if len(groups) != 1 or set(intent.modes) != {"flight", "hotel", "bus"}:
                return self._reply(request, world, "Recuperarea cu hotel salvat necesită un singur grup explicit și întregul traseu: hotel, ambele transferuri și zbor. Nu am extins sau redus cererea. Interpretarea a folosit 1 apel.", recovery_total_call_limit=total_limit)
            if hotel_input_time_requested(request.text, (case.id, case.requested_flight_number)):
                return self._reply(request, world, "Cererea conține date sau ore suplimentare față de identitatea cazului. Revizuiește-le în formularul hotelului; nu le-am ignorat și nu am schimbat configurația salvată. Nicio căutare pornită; interpretarea a folosit 1 apel.", recovery_total_call_limit=total_limit)
            try:
                hotel_inputs = HotelRecoveryInputs.model_validate(read_hotel_recovery_profile(world, linked[groups[0]]))
            except (HotelRecoveryProfileError, ValidationError):
                return self._reply(request, world, "Configurația hotelului lipsește, este invalidă sau nu mai corespunde acestui grup și traseu. Completează formularul și finalizează prima planificare pentru a salva datele. Nu am presupus coordonate sau date și nu am pornit căutarea. Interpretarea a folosit 1 apel.", recovery_total_call_limit=total_limit)
            if allowance < REQUIRED_CALLS:
                return self._reply(request, world, f"Traseul cu hotel necesită minimum {REQUIRED_CALLS + 1} apeluri în total: 1 interpretare și {REQUIRED_CALLS} pentru job. Limita cerută este {total_limit}; interpretarea a folosit 1 și rămân {max(0, allowance)}. Nu am pornit căutarea și nu am mărit limita.", recovery_total_call_limit=total_limit)
        job_request = RecoveryJobRequest(expected_version=world.version,
            idempotency_key="live-command-" + Store.fingerprint({"key": request.idempotency_key, "scenario": world.scenario_id}),
            group_ids=groups, objective=intent.objective, modes=intent.modes,
            max_provider_calls=allowance, instructions=request.text, hotel_recovery=hotel_inputs)
        if hotel_inputs is not None:
            try:
                validate_hotel_job(world, job_request)
            except DomainError as exc:
                return self._reply(request, world, "Configurația salvată nu mai este validă pentru condițiile actuale. Revizuiește formularul înainte de o nouă căutare; nu am avansat preluarea și nu am înlocuit limitele actuale. " + exc.message + " Interpretarea a folosit 1 apel.", recovery_total_call_limit=total_limit)

        def receipt(conn, receipt_world, result):
            job = result["job"]
            explanation = (f"Am înregistrat cererea de recuperare pentru {case.id}, {case.requested_flight_number}: "
                f"{len(groups)} grupuri, job {job['id']}. Limita totală: {total_limit} apeluri — 1 interpretare și cel mult {allowance} apeluri ale jobului. "
                "Urmărește progresul în panoul cazului; această confirmare nu dovedește găsirea unui itinerar sau efectuarea unei rezervări.")
            skipped = sorted(set(linked) & confirmed)
            if hotel_inputs is not None:
                explanation += (f" Am reutilizat exact configurația hotelului: intrare {hotel_inputs.checkin_at.isoformat()}, "
                    f"ieșire {hotel_inputs.checkout_at.isoformat()}, preluare {hotel_inputs.pickup_at.isoformat()}. "
                    "Sunt date declarate, nu rezervări sau tarife salvate.")
            if skipped:
                explanation += f" {len(skipped)} grupuri cu rezervări confirmate au rămas în afara căutării."
            response = {"world": self.store._with_observations(conn, receipt_world).model_dump(mode="json"),
                "replayed": result["replayed"], "applied": False, "mode": "ai", "explanation": explanation,
                "incident_id": case.id, "recovery_job": job, "command_interpretation_calls": 1,
                "recovery_total_call_limit": total_limit}
            conn.execute("INSERT INTO idempotency VALUES(?,?,?,?)", ("command:" + request.idempotency_key,
                Store.fingerprint(request.model_dump()), json.dumps(self.store._without_observations(response)), utcnow().isoformat()))
            return response

        return self.jobs.start(case.id, job_request, on_created=receipt)
