"""
characters.py
-------------
Defines the FIXED CAST of six suspects that appears in every PyDetective
case. Only their names and personality traits are constant -- their motive,
alibi, relationship to the victim, and guilt change per generated case
(see case.json / case_truth.json).

Stage 1 scope:
    - Static cast data + simple lookup helpers used by the CLI menu.

Stage 2 scope (added):
    - CharacterState: per-suspect conversation history for one playthrough,
      so answers stay consistent across multiple questions.
    - build_system_prompt(): constructs the grounded Llama system prompt
      from personality + the case-specific slice of data this character
      is allowed to know, per the word-limit / persona constraints.

Stage 2.5 scope (added):
    - CharacterKnowledge: a bounded "knows" / "must_hide" fact sheet per
      suspect, loaded from case_truth.json via case.py. Replaces the old
      generic "answer from what your character would know" instruction
      with concrete, specific facts -- this is what stops the model from
      inventing details or accidentally leaking things it shouldn't know.
    - build_system_prompt() now takes that knowledge sheet directly
      instead of a raw case_truth dict, so it's structurally impossible
      to accidentally pass motive/method/timeline into the prompt.

Stage 3 scope (added):
    - CharacterKnowledge expanded from a flat knows/must_hide pair into
      five distinct categories (known_facts / beliefs / secrets / lies /
      unknown_information), so suspects can express opinions and small
      talk without it reading as either "confirmed fact" or "confession."
      The model is told explicitly which category is which, so a belief
      can be shared as a guess without becoming case-canon, and a lie can
      be told confidently without becoming a secret the model has to
      protect from being asked about directly.

Stage 3.5 scope (added):
    - InterrogationEvent: a structured record of one turn (question/claim/
      confrontation/reaction), kept alongside -- not instead of -- the raw
      API-shaped history, for transcript/hallucination-flag bookkeeping.
    - CharacterState.log_event(): appends one InterrogationEvent.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Suspect:
    """Static personality profile for one of the six fixed characters."""
    name: str
    personality: str
    voice_notes: str  # short guidance on how this character tends to speak


# The six characters are ALWAYS present, in every case. Python (not the
# LLM) owns this list. case_generator.py will later attach per-case facts
# (alibi, motive, relation to victim) to these same six names.
CAST: list[Suspect] = [
    Suspect(
        name="Dr. Arjun Mehta",
        personality="Calm / Analytical",
        voice_notes="Speaks precisely, references facts and logic, rarely raises his voice.",
    ),
    Suspect(
        name="Maya Kapoor",
        personality="Nervous / Defensive",
        voice_notes="Answers quickly, over-explains, gets flustered under pressure.",
    ),
    Suspect(
        name="Kabir Rana",
        personality="Sarcastic / Confident",
        voice_notes="Dry humor, deflects with wit, acts unbothered even when cornered.",
    ),
    Suspect(
        name="Rhea Singh",
        personality="Friendly / Observant",
        voice_notes="Warm and cooperative, volunteers small details she noticed.",
    ),
    Suspect(
        name="Vikram Oberoi",
        personality="Aggressive / Impatient",
        voice_notes="Short temper, blunt answers, pushes back on being questioned.",
    ),
    Suspect(
        name="Nisha Verma",
        personality="Quiet / Evasive",
        voice_notes="Gives minimal answers, avoids specifics, long pauses implied.",
    ),
]

_CAST_BY_NAME = {s.name: s for s in CAST}


def get_character(name: str) -> Optional[Suspect]:
    """Look up a fixed-cast character by exact name. Returns None if unknown."""
    return _CAST_BY_NAME.get(name)


def cast_names() -> list[str]:
    """Return the six fixed character names, in canonical order."""
    return [s.name for s in CAST]


def is_valid_suspect(name: str) -> bool:
    """True if `name` matches one of the six fixed characters exactly."""
    return name in _CAST_BY_NAME


@dataclass
class InterrogationEvent:
    """
    One structured turn in an interrogation transcript (Stage 3.5). This
    is a SEPARATE record from CharacterState.history: history stays in
    Ollama's raw {"role","content"} shape because that's what every API
    call needs verbatim; events exist for our own bookkeeping -- future
    transcripts, UI, and the hallucination heuristic in forensics.py --
    without reformatting the API-facing history on every turn.

    turn_id                : 1-based position within this suspect's event log
    speaker                : the suspect's name, or "Detective" for the player
    event_type              : "QUESTION" | "CLAIM" | "CONFRONTATION" | "REACTION"
                              (QUESTION/CONFRONTATION are the player's side of
                              a turn; CLAIM/REACTION are the suspect's)
    content                 : the turn's text, verbatim
    evidence_backed         : True/False/None -- forensics.assess_claim()'s
                              verdict for this turn (None = not applicable,
                              e.g. plain small talk with no confrontational
                              or evidence-referencing language)
    grounded_in_knowledge   : always True in this codebase -- every CLAIM/
                              REACTION here was generated from a prompt built
                              by characters.build_system_prompt(), which is
                              structurally incapable of including motive/
                              method/timeline. The field exists so a future
                              code path that ever bypassed that construction
                              could honestly mark itself False.
    flagged_as_hallucination : a best-effort heuristic flag (see
                              forensics.flag_possible_hallucination) -- NOT
                              a verified fact-check. It only ever adds a
                              hint for the player; it never edits, hides,
                              or suppresses the suspect's actual reply.
    """
    turn_id: int
    speaker: str
    event_type: str
    content: str
    evidence_backed: Optional[bool] = None
    grounded_in_knowledge: bool = True
    flagged_as_hallucination: bool = False


@dataclass
class CharacterState:
    """
    Per-suspect conversation memory for one playthrough. One instance per
    suspect the player has spoken to, held in GameState.character_states.
    Not persisted to disk -- lives only for the session.
    """
    suspect: Suspect
    history: list[dict] = field(default_factory=list)  # [{"role": "user"/"assistant", "content": ...}]
    questions_answered: int = 0
    events: list[InterrogationEvent] = field(default_factory=list)

    def record(self, question: str, answer: str) -> None:
        """Append one Q&A turn (raw API shape) and bump the question counter."""
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        self.questions_answered += 1

    def log_event(self, speaker: str, event_type: str, content: str,
                   evidence_backed: Optional[bool] = None,
                   flagged_as_hallucination: bool = False) -> InterrogationEvent:
        """Append one structured InterrogationEvent; turn_id auto-increments."""
        event = InterrogationEvent(
            turn_id=len(self.events) + 1,
            speaker=speaker,
            event_type=event_type,
            content=content,
            evidence_backed=evidence_backed,
            flagged_as_hallucination=flagged_as_hallucination,
        )
        self.events.append(event)
        return event


@dataclass
class CharacterKnowledge:
    """
    One suspect's bounded fact sheet -- everything characters.build_system_prompt
    is allowed to tell the model this character personally knows, believes,
    hides, lies about, or is ignorant of. Hidden data: lives in
    case_truth.json's "character_knowledge" section, loaded via case.py.

    known_facts        : concrete facts this character has first-hand and
                          will state plainly if asked.
    beliefs             : opinions, suspicions, impressions -- the model
                          may share these, but must frame them as guesses,
                          never as confirmed fact. This is what lets a
                          suspect speculate ("I think Vikram was acting
                          odd") without that speculation becoming canon.
    secrets             : facts this character is actively hiding and will
                          deflect/dodge on if pressed, without admitting
                          them. For the culprit these are crime-related;
                          for innocent suspects they can be unrelated but
                          embarrassing (red-herring fuel).
    lies                : specific false claims this character will assert
                          confidently, as if true, if asked directly --
                          distinct from secrets: a lie is actively stated,
                          a secret is merely withheld.
    unknown_information : facts this character genuinely does NOT know --
                          the model is told to say "I don't know" / "I
                          didn't see that" here rather than improvising an
                          answer, which is what stops confident-sounding
                          hallucination on questions outside their scope.

    Deliberately does NOT include motive, method, the true timeline, or
    the master culprit name -- only this one character's own slice of
    the truth.
    """
    known_facts: list[str] = field(default_factory=list)
    beliefs: list[str] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    lies: list[str] = field(default_factory=list)
    unknown_information: list[str] = field(default_factory=list)


def build_system_prompt(suspect: Suspect, public_info: dict, knowledge: CharacterKnowledge,
                         is_culprit: bool, victim: str, word_limit: int = 35) -> str:
    """
    Build the grounded Llama system prompt for one interrogation turn.

    suspect     : this character's static personality profile (from CAST)
    public_info : this suspect's case-specific public data (relation to
                  victim, alibi, statement) -- taken from case.json, safe
                  for the character to "know" about themselves
    knowledge   : this suspect's bounded CharacterKnowledge sheet -- the
                  ONLY case-truth-derived input this function accepts, so
                  motive/method/timeline structurally cannot leak into the
                  prompt by accident
    is_culprit  : whether this suspect is the true culprit (a bool, not the
                  culprit's name -- so this function never even receives
                  who the culprit is when building an innocent suspect's
                  prompt)
    victim      : the victim's plain name, for the opening line
    word_limit  : max words the reply should run, enforced via instruction
                  (not hard-truncated -- the model is asked to comply)

    The prompt never contains motive, method, the true timeline, or any
    other suspect's knowledge sheet -- only this one character's own
    alibi/statement/known_facts/beliefs/secrets/lies/unknown_information,
    so a suspect can't "know" facts their character wouldn't realistically
    have, and can't accidentally state a hunch as though it were evidence.
    """
    lines = [
        f"You are {suspect.name}, a suspect being interviewed about the death of {victim}.",
        f"Personality: {suspect.personality}. {suspect.voice_notes}",
        f"Your relation to the victim: {public_info.get('relation_to_victim', 'unknown')}.",
        f'Your stated alibi, which you must stay consistent with: "{public_info.get("alibi", "")}"',
        f'Your public statement so far: "{public_info.get("public_statement", "")}"',
    ]

    if knowledge.known_facts:
        lines.append("Things you know for certain and will state plainly if asked:")
        lines.extend(f"  - {fact}" for fact in knowledge.known_facts)

    if knowledge.beliefs:
        lines.append("Your personal beliefs or suspicions -- you may share these, but always as "
                      "your own guess or impression, phrased like an opinion, never as confirmed fact:")
        lines.extend(f"  - {belief}" for belief in knowledge.beliefs)

    if knowledge.secrets:
        lines.append("Secrets you are actively hiding -- deflect, dodge, or change the subject if "
                      "pressed on these, without admitting them and without contradicting your alibi:")
        lines.extend(f"  - {secret}" for secret in knowledge.secrets)

    if knowledge.lies:
        lines.append("Specific false claims you will assert confidently, as if they were true, if asked directly:")
        lines.extend(f"  - {lie}" for lie in knowledge.lies)

    if knowledge.unknown_information:
        lines.append("Things you genuinely do NOT know -- if asked about these, say you don't know "
                      "or didn't see it, rather than guessing or inventing an answer:")
        lines.extend(f"  - {fact}" for fact in knowledge.unknown_information)

    lines.append(
        f"Answer strictly in character, in {word_limit} words or fewer, as spoken dialogue "
        f"only -- no stage directions, no quotation marks, no narration."
    )
    lines.append(
        "You may improvise small, casual, non-case-critical flavor (mood, mannerisms, brief "
        "small talk) to sound natural. But never invent case-relevant facts beyond what's listed "
        "above, never state a belief as if it were a confirmed fact, never admit a secret, and "
        "never claim knowledge of something listed as unknown to you."
    )
    lines.append("Never break character or mention you are an AI or a language model.")

    if is_culprit:
        lines.append(
            "You committed this crime, but you must never confess or state your guilt "
            "outright, even under pressure."
        )
    else:
        lines.append(
            "You are innocent. You do not know for certain who is responsible -- do not "
            "accuse anyone by name unless it's listed in your beliefs above."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# TODO (Stage 3+ - case_generator.py, not yet implemented):
#   - Qwen must output a "character_knowledge" section with all five
#     categories per suspect (known_facts/beliefs/secrets/lies/
#     unknown_information) and a "contradictions" list alongside
#     case_truth, matching the shape case.py.load() and case_validator.py
#     expect -- see case_generator.py's docstring.
# ---------------------------------------------------------------------------
