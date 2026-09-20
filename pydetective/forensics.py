"""
forensics.py
------------
Two deterministic, non-LLM-judged checks that feed interrogation.py's
per-turn prompt construction:

    - assess_claim(): the "bluff engine" -- scans the player's own
      question for evidence-related keywords (defined per evidence file
      in case.json's "keywords" list) or generic confrontational
      language, and checks whether the player has actually examined
      evidence that backs it up. Feeds the EVIDENCE_BACKED True/False/
      None directive (Stage 3.5).
    - flag_possible_hallucination(): a bounded, best-effort heuristic that
      flags (never edits or suppresses) a suspect's answer if it shares
      multiple distinctive words with ANOTHER suspect's secrets/lies that
      aren't also part of the speaker's own knowledge sheet. Explicitly
      not a reliable fact-checker -- a coarse tripwire, surfaced as a
      hint, never a verdict (Stage 3.5).

Stage 4 scope (removed): this module used to also own the contradiction-
flagging UI -- a manual "pick a suspect + evidence" screen, and a
proactive "[!] Potential Contradiction Detected" popup that fired
automatically whenever a contradiction's trigger conditions were met.
Both are gone: a "pure detective" design pivot says the engine must
present raw observations (evidence text, interrogation transcripts) and
never auto-flag inconsistencies for the player. Contradiction data
(case.Case.contradictions) still exists and is still exactly as
deterministic as before -- it just moved to being evaluated on demand,
inside a player's hypothesis to their Detective Partner (see partner.py),
which is now the only way a contradiction gets logged as found.
"""

import re
from typing import Optional

GENERIC_CONFRONTATION_WORDS = ["witness", "bribe", "confess", "admit", "lying", "proof"]


def assess_claim(state, question: str) -> tuple[Optional[bool], list[str]]:
    """
    Deterministic "bluff engine": scan the player's own question for
    evidence-related keywords (defined per evidence file in case.json's
    "keywords" list) or generic confrontational language, and check
    whether the matched evidence has actually been examined.

    Returns (evidence_backed, matched_filenames):
        (None, [])       -- no confrontational/evidence language detected;
                             an ordinary question. No bluff framing needed.
        (True, [...])    -- matched evidence the player HAS examined.
        (False, [...])   -- matched evidence the player has NOT examined,
                             or confrontational language with no matching
                             evidence at all. This is the bluff case.

    This only ever looks at the player's own question text -- never at
    the model's prior replies -- so it stays fully deterministic.
    """
    lowered = question.lower()
    matched_files: list[str] = []
    for item in state.case.evidence_manifest:
        for keyword in item.get("keywords", []):
            if keyword.lower() in lowered:
                matched_files.append(item["filename"])
                break

    generic_hit = any(word in lowered for word in GENERIC_CONFRONTATION_WORDS)

    if not matched_files and not generic_hit:
        return None, []

    discovered_matches = [f for f in matched_files if f in state.evidence.discovered]
    if discovered_matches:
        return True, discovered_matches
    return False, matched_files


# ---------------------------------------------------------------------------
# Hallucination heuristic (Stage 3.5)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "about", "after", "again", "their", "there", "these", "those", "which",
    "would", "could", "should", "before", "every", "still", "never", "claim",
    "claims", "think", "guess", "maybe", "actually", "really", "tonight",
}


def _significant_words(text: str) -> set[str]:
    """Lowercase words of 5+ letters, minus a small stopword list -- a
    coarse proxy for "the distinctive content words in this sentence"."""
    words = re.findall(r"[a-zA-Z]{5,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def flag_possible_hallucination(state, speaker_name: str, answer_text: str) -> bool:
    """
    Best-effort heuristic: True if `answer_text` shares 2+ distinctive
    words with another suspect's secrets/lies that AREN'T also present in
    the speaker's own knowledge sheet -- a coarse proxy for "this suspect
    just said something that sounds like it belongs to someone else's
    hidden facts." NOT a reliable fact-checker: paraphrased LLM output
    rarely echoes our source strings closely, so expect this to miss most
    real cases. It is a tripwire for the more blatant ones, nothing more,
    and the caller must only ever use it to show a hint -- never to alter,
    hide, or reject the suspect's actual reply.
    """
    own = state.case.knowledge_for(speaker_name)
    own_words: set[str] = set()
    for bucket in (own.known_facts, own.beliefs, own.secrets, own.lies, own.unknown_information):
        for fact in bucket:
            own_words |= _significant_words(fact)

    answer_words = _significant_words(answer_text)
    if not answer_words:
        return False

    for other in state.suspects:
        other_name = other["name"]
        if other_name == speaker_name:
            continue
        other_knowledge = state.case.knowledge_for(other_name)
        for fact in other_knowledge.secrets + other_knowledge.lies:
            fact_words = _significant_words(fact)
            if not fact_words:
                continue
            overlap = fact_words & answer_words
            # Skip facts that are essentially shared vocabulary the speaker
            # also legitimately has in their own sheet (e.g. both suspects'
            # sheets mention "turnstile" as an ordinary real-world detail).
            if len(overlap) >= 2 and not fact_words.issubset(own_words):
                return True
    return False
