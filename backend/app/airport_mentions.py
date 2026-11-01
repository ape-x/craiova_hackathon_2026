"""Literal airport mentions for operational reports, not ranked place search.

An exact catalog name is one mention: its city is not a second destination.
Only case, accents and word separators are normalized. No spelling, speech or
geographic correction is performed, and duplicate catalog names stay ambiguous.
"""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

from .airport_catalog import _catalog, _CITY_ALIASES, _COMMON_WORDS, _normalize, lookup_airport, match_airports


MAX_MENTION_TEXT = 8000
_NAME_SUFFIXES = {"airport", "airports", "international", "regional", "municipal", "aerodrome", "airfield"}
_PROSE_CODE_WORDS = _COMMON_WORDS | {"air", "all", "also", "bus", "but", "can", "car", "had", "has", "may", "not", "now", "our", "van", "was", "were", "will"}
_LIST_JOIN = r"\s*(?:[,/|&]|\b(?:and|or|si|și|şi|sau)\b)\s*"
_CODE_ITEM = r"(?:(?P<label>airport|aeroport(?:ul|ului)?|iata|icao)\s+)?(?P<code>\b[A-Za-z]{3,4}\b)"
_NEXT_CODE = re.compile(_LIST_JOIN + _CODE_ITEM, re.IGNORECASE)
_PREVIOUS_CODE = re.compile(_CODE_ITEM + _LIST_JOIN + r"$", re.IGNORECASE)


def _literal_spelling(value):
    folded = "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                     if not unicodedata.combining(char))
    return " ".join(folded.split())


@lru_cache(maxsize=1)
def _catalog_spellings():
    return frozenset(_literal_spelling(item["name"]) for item in _catalog()[1])


@lru_cache(maxsize=1)
def _name_forms():
    """Index full names and literal suffix/city-language variants with collisions."""
    records = _catalog()[1]
    cities = {_normalize(re.split(r"[,(]", item["municipality"], maxsplit=1)[0]) for item in records}
    aliases = {tuple(_normalize(alias).split()): tuple(_normalize(city).split())
               for alias, city in _CITY_ALIASES.items()}
    cities.update(" ".join(alias) for alias in aliases)
    forms = {}
    for item in records:
        full = tuple(_normalize(item["name"]).split())
        variants = {full}
        shorter = full
        while shorter and shorter[-1] in _NAME_SUFFIXES:
            shorter = shorter[:-1]
            # Dropping "International Airport" must not turn London into an
            # alias for YXU, or choose one airport from another shared city.
            if shorter and " ".join(shorter) not in cities:
                variants.add(shorter)
        for words in tuple(variants):
            for alias, city in aliases.items():
                if words[:len(city)] == city and len(words) > len(city):
                    variants.add(alias + words[len(city):])
        for words in variants:
            if words:
                forms.setdefault(words, set()).add(item["id"])
    by_first = {}
    for words, ids in forms.items():
        by_first.setdefault(words[0], []).append((words, frozenset(ids)))
    return {first: sorted(items, key=lambda item: len(item[0]), reverse=True)
            for first, items in by_first.items()}


def _adjacent_code_ids(text, spans):
    """Read a code list only beside the literal reference or a known full name.

    Lowercase prose elsewhere is never scanned for codes. Ordinary words such
    as bus/car/for stay prose unless explicitly introduced as an airport/code.
    """
    found = set()
    for start, end in spans:
        for forward in (True, False):
            cursor = end if forward else start
            while True:
                match = (_NEXT_CODE.match(text, cursor) if forward
                         else _PREVIOUS_CODE.search(text[:cursor]))
                if match is None:
                    break
                code = match["code"]
                airport = lookup_airport(code)
                if airport is None or (code.casefold() in _PROSE_CODE_WORDS and match["label"] is None):
                    break
                found.add(airport["id"])
                cursor = match.end() if forward else match.start()
    return found


def literal_airport_ids(text: str, *, reference_span: tuple[int, int] | None = None) -> set[str]:
    """Keep every distinct mention; suppress city hits only inside named spans.

    The existing broad search remains responsible for codes, short distinctive
    names and unresolved city references outside those spans. In particular,
    identifying Heathrow never suppresses a separate Gatwick or Paris mention.
    """
    if not isinstance(text, str) or len(text) > MAX_MENTION_TEXT:
        raise ValueError("Airport mention text is invalid or exceeds its bound")
    if reference_span is not None and not 0 <= reference_span[0] < reference_span[1] <= len(text):
        raise ValueError("Literal airport reference is outside the report")
    exact = lookup_airport(text)
    if exact is not None:
        return {exact["id"]}
    tokens = []
    for match in re.finditer(r"[^\W_]+", text):
        words = _normalize(match.group()).split()
        tokens.extend((word, match.start(), match.end()) for word in words)
    names = _name_forms()
    matches = []
    for index, (first, start, _) in enumerate(tokens):
        for words, ids in names.get(first, []):
            following = tokens[index:index + len(words)]
            if tuple(item[0] for item in following) != words:
                continue
            end = following[-1][2]
            # Do not combine names across a clause or a separate spoken turn.
            # Literal catalog punctuation (e.g. L.F. Wade) is part of a name;
            # inserting a sentence boundary into "London. Heathrow" is not.
            if (re.search(r"[.;:!?/|\n]", text[start:end])
                    and _literal_spelling(text[start:end]) not in _catalog_spellings()):
                continue
            matches.append((start, end, ids))
    # A complete longer name wins only over a contained name/city span, not
    # another mention elsewhere. Equal-span catalog collisions retain all IDs.
    maximal = [item for item in matches if not any(
        other[0] <= item[0] and item[1] <= other[1] and other[:2] != item[:2]
        for other in matches)]
    found = set()
    remainder = list(text)
    for start, end, ids in maximal:
        found.update(ids)
        remainder[start:end] = " " * (end - start)
    found.update(item["id"] for item in match_airports("".join(remainder), limit=50))
    spans = [(start, end) for start, end, _ in maximal]
    if reference_span is not None:
        spans.append(reference_span)
    found.update(_adjacent_code_ids(text, spans))
    return found
