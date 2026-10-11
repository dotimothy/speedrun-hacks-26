"""Verify a model's quoted evidence against the transcript it came from.

A probe of the local model showed it will happily return a plausible-sounding
quote, so a quote is treated as a *claim about the transcript* and checked. A
quote that cannot be located is not evidence, and the prediction that rests on
it is downgraded to ``unknown`` by ``confidence.py``.

Matching is tried in three escalating tiers so that harmless reformatting
(added punctuation, changed case, collapsed whitespace) does not throw away a
genuine quote, while an invented one still fails:

1. ``exact``      -- the quote appears verbatim.
2. ``normalized`` -- it appears after lowercasing, collapsing whitespace and
                     dropping punctuation.
3. ``fuzzy``      -- a window of the transcript is >= ``fuzzy_threshold``
                     similar to it (handles a dropped or swapped word).

Offsets returned always index the ORIGINAL transcript text, so the UI can
highlight the real characters the dispatcher is reading.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .config import settings
from .schemas import EvidenceSpan

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")

# A quote shorter than this is not evidence of anything -- "fire" matches half
# the transcript and highlights nothing useful.
MIN_QUOTE_CHARS = 8


def _normalize(text: str) -> tuple[str, list[int]]:
    """Lowercase, drop punctuation, collapse whitespace.

    Returns the normalized string plus a map from each normalized character
    index back to its index in ``text``, so a match found in normalized space
    can be reported against the original.
    """
    out: list[str] = []
    index_map: list[int] = []
    prev_space = True  # leading whitespace is dropped

    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            index_map.append(i)
            prev_space = True
            continue
        if _PUNCT.match(ch):
            continue
        out.append(ch.lower())
        index_map.append(i)
        prev_space = False

    # Trailing separator would offset-shift an end-anchored match.
    while out and out[-1] == " ":
        out.pop()
        index_map.pop()

    return "".join(out), index_map


def _span_from_norm(
    start_norm: int, end_norm: int, index_map: list[int], text: str, match: str, source: str
) -> EvidenceSpan | None:
    if start_norm < 0 or end_norm <= start_norm or end_norm > len(index_map):
        return None
    start = index_map[start_norm]
    end = index_map[end_norm - 1] + 1
    return EvidenceSpan(text=text[start:end], start=start, end=end, source=source, match=match)  # type: ignore[arg-type]


def locate(quote: str, transcript: str, source: str = "original") -> EvidenceSpan | None:
    """Locate ``quote`` in ``transcript``, or return None if it is not there."""
    if not quote or not transcript:
        return None

    cleaned = quote.strip().strip('"\u201c\u201d\'')
    if len(_PUNCT.sub("", cleaned).strip()) < MIN_QUOTE_CHARS:
        return None

    # Tier 1: verbatim.
    idx = transcript.find(cleaned)
    if idx != -1:
        return EvidenceSpan(
            text=transcript[idx : idx + len(cleaned)],
            start=idx,
            end=idx + len(cleaned),
            source=source,  # type: ignore[arg-type]
            match="exact",
        )

    hay, index_map = _normalize(transcript)
    needle, _ = _normalize(cleaned)
    if not needle or not hay:
        return None

    # Tier 2: same text modulo case, punctuation and whitespace.
    idx = hay.find(needle)
    if idx != -1:
        return _span_from_norm(idx, idx + len(needle), index_map, transcript, "normalized", source)

    # Tier 3: anchor on every shared run, take the transcript window they
    # bracket, and score it. This tolerates a swapped or dropped word ("the
    # kitchen" for "my kitchen") while still rejecting a quote that simply is
    # not in the transcript, because an invented quote shares no long runs.
    matcher = SequenceMatcher(None, hay, needle, autojunk=False)
    blocks = [b for b in matcher.get_matching_blocks() if b.size >= 3]
    if not blocks:
        return None

    matched = sum(b.size for b in blocks)
    if matched < MIN_QUOTE_CHARS or matched / len(needle) < 0.5:
        return None

    start = blocks[0].a
    end = blocks[-1].a + blocks[-1].size
    ratio = SequenceMatcher(None, hay[start:end], needle, autojunk=False).ratio()
    if ratio < settings.fuzzy_threshold:
        return None

    return _span_from_norm(start, end, index_map, transcript, "fuzzy", source)


def resolve(quote: str | None, original: str, english: str) -> EvidenceSpan | None:
    """Find a quote in either transcript.

    The original is checked first because that is what the caller actually
    said; the translation is the fallback, since a generative extractor reads
    the English text and may quote from it.
    """
    if not quote:
        return None
    span = locate(quote, original, "original")
    if span is not None:
        return span
    if english and english.strip() != original.strip():
        return locate(quote, english, "english")
    return None


# Sentence-ish boundaries. Kept simple on purpose: this only has to find a
# readable span to highlight, not parse grammar.
_BOUNDARY = re.compile(r"[.!?\n]")


def locate_term(term: str, transcript: str, source: str = "original") -> EvidenceSpan | None:
    """Locate a short domain term and widen it to the clause containing it.

    ``locate`` deliberately rejects anything under ``MIN_QUOTE_CHARS`` so a
    model cannot pass off "fire" as evidence. But when *we* go looking for a
    known term in order to highlight something, a short term is exactly what we
    have -- so this finds it and then expands to the surrounding sentence,
    which is both legal to highlight and more useful to read than five
    characters in the middle of a paragraph.
    """
    if not term or not transcript:
        return None

    hay, index_map = _normalize(transcript)
    needle, _ = _normalize(term)
    if not needle:
        return None

    at = hay.find(needle)
    if at == -1:
        return None

    start = index_map[at]
    end = index_map[min(at + len(needle), len(index_map)) - 1] + 1

    # Widen outwards to the nearest sentence boundary on each side.
    left = 0
    for match in _BOUNDARY.finditer(transcript, 0, start):
        left = match.end()
    right_match = _BOUNDARY.search(transcript, end)
    right = right_match.start() + 1 if right_match else len(transcript)

    span_text = transcript[left:right].strip()
    offset = transcript.index(span_text, left) if span_text else left
    return EvidenceSpan(
        text=span_text,
        start=offset,
        end=offset + len(span_text),
        source=source,  # type: ignore[arg-type]
        match="exact",
    )
