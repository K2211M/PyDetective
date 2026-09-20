"""
interrogation.py
-----------------
Live suspect interviews through a local Llama model, via llm.py.

Stage 2 scope: one interactive Q&A loop per suspect, grounded in this
suspect's case-specific data (characters.build_system_prompt), with a
per-suspect conversation history (characters.CharacterState) so answers
stay consistent across multiple questions in the same playthrough.

Stage 2.5 scope (added): prompts are now built from each suspect's bounded
CharacterKnowledge sheet (state.case.knowledge_for) rather than a generic
"answer from what your character would know" instruction.

Stage 3 scope (added): after each answered question, calls
forensics.prompt_new_suggestions() for the proactive contradiction check.

Stage 3.5 scope (added): "hallucination-driven confession" hardening --
    - Every ordinary question is run through forensics.assess_claim()
      first. The result becomes a PER-TURN EVIDENCE_BACKED directive
      (see _bluff_directive) sent alongside that one question, so a
      suspect is told to stay skeptical of unbacked bluffs and may react
      more openly to genuinely examined evidence -- without ever being
      told to confess outright either way.
    - confront_suspect(): a formal "present evidence" exchange. The
      player can only select evidence they've actually discovered, so
      EVIDENCE_BACKED: True is guaranteed by construction rather than
      inferred from keywords. If the presented evidence is one of this
      suspect's own predefined contradictions, the directive tells them
      they may drop that SPECIFIC lie -- still never a full confession.
    - Every suspect reply is run through forensics.flag_possible_
      hallucination() and logged as a structured InterrogationEvent
      (characters.InterrogationEvent) alongside the raw history. The
      flag is a heuristic hint shown to the player, never used to alter,
      hide, or reject what the model actually said.

Stage 4 scope (changed): the "pure detective" pivot removes the proactive
contradiction popup entirely -- interrogation.py no longer calls into
forensics for contradiction suggestions at all. Contradictions are now
only ever surfaced through a player's own hypothesis to their Detective
Partner (see partner.py). The bluff engine and hallucination heuristic
are unaffected -- neither of those is a "contradiction alert," so the
pivot doesn't touch them. Also: LLAMA_MODEL updated to llama3.2:3b, and
the shared error-handled chat call moved to llm.safe_chat() so
partner.py can reuse it too.

Never trusts the LLM as a source of fact: motive/method/true_timeline are
never included in any prompt (case.Case only exposes those through
verdict-only accessors interrogation.py never calls). All failure modes
from llm.py (server not running, timeout, bad response) are caught so a
flaky local model can never crash the game loop.
"""

import os

import forensics
import llm
from characters import get_character, CharacterState, build_system_prompt

LLAMA_MODEL = "llama3.2:3b"  # must match a tag from `ollama list` exactly
WORD_LIMIT = 35


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _first_name(suspect_name: str) -> str:
    """Skip a leading title (e.g. "Dr.") so prompts read "Arjun" not "Dr."."""
    parts = suspect_name.split()
    return parts[1] if parts[0].endswith(".") else parts[0]


def _bluff_directive(evidence_backed, matched_files: list[str]):
    """Build the per-turn EVIDENCE_BACKED system note from assess_claim()'s verdict."""
    if evidence_backed is None:
        return None
    if evidence_backed:
        return (
            "EVIDENCE_BACKED: True. The detective's question references evidence they have "
            f"actually examined ({', '.join(matched_files)}). You may react realistically to "
            "being confronted with real evidence -- getting flustered, contradicting your own "
            "alibi, or admitting a specific detail is true -- but you must still never confess "
            "to the crime itself or state your guilt outright."
        )
    return (
        "EVIDENCE_BACKED: False. The detective's question sounds confrontational or claims to "
        "reference evidence, but they have not actually examined anything that supports it -- "
        "this may be a bluff. Stay evasive, call their bluff, demand proof, or express "
        "skepticism. Do not confirm new facts, confess, or reveal secrets on the strength of an "
        "unverified claim."
    )


def _confrontation_directive(state, suspect_name: str, evidence_file: str) -> str:
    """
    Build the EVIDENCE_BACKED: True directive for a formal confront_suspect()
    exchange -- stronger than the keyword-based bluff directive, since the
    evidence is guaranteed genuinely examined, and includes the specific
    contradicting fact if this evidence happens to implicate this suspect.
    """
    manifest_entry = next(
        (e for e in state.case.evidence_manifest if e["filename"] == evidence_file), None
    )
    description = manifest_entry["description"] if manifest_entry else evidence_file
    contradiction = state.case.find_contradiction(suspect_name, evidence_file)

    lines = [
        "EVIDENCE_BACKED: True. The detective has just formally presented you with real, "
        f'verified evidence they examined themselves: "{evidence_file}" -- {description}',
    ]
    if contradiction is not None:
        lines.append(
            f"This evidence directly contradicts something you've claimed: {contradiction.evidence_fact} "
            "You may react realistically -- get flustered, backpedal, or abandon that SPECIFIC "
            "claim -- but you must still never confess to the crime itself or admit intent, even now."
        )
    else:
        lines.append(
            "This evidence doesn't specifically implicate you. Acknowledge it exists and discuss "
            "it honestly if your character would plausibly know about it, but don't invent a "
            "dramatic reaction to something that isn't actually about you."
        )
    return "\n".join(lines)


def _print_hallucination_hint(flagged: bool, first_name: str) -> None:
    if flagged:
        print(f"[heuristic note: this answer touches on details that may not be grounded in "
              f"what {first_name} actually knows -- a rough hint, not a confirmed fact]\n")


def run_interrogation(state, suspect_name: str, model: str = LLAMA_MODEL) -> None:
    """
    Interactive Q&A loop with one suspect. Mutates `state` (GameState):
    increments state.questions_asked per answered question, keeps the raw
    transcript + structured events in state.character_states, and can
    hand off to confront_suspect() via the 'confront' command.
    """
    public_info = state.get_suspect_public_info(suspect_name)
    profile = get_character(suspect_name)
    if public_info is None or profile is None:
        print(f"'{suspect_name}' isn't a suspect in this case.")
        return

    char_state = state.character_states.setdefault(
        suspect_name, CharacterState(suspect=profile)
    )
    system_prompt = build_system_prompt(
        suspect=profile,
        public_info=public_info,
        knowledge=state.case.knowledge_for(suspect_name),
        is_culprit=state.case.is_culprit(suspect_name),
        victim=state.case.victim_name(),
        word_limit=WORD_LIMIT,
    )

    clear_screen()
    print(f"=== Interrogating {suspect_name} ===")
    print(f"({profile.personality})\n")
    if char_state.questions_answered:
        print(f"[You've already asked {char_state.questions_answered} question(s) this session.]\n")
    print("Type your question, 'confront' to present evidence, or 'back' to end the interview.\n")

    first_name = _first_name(suspect_name)

    while True:
        question = input(f"You ask {first_name} > ").strip()
        if not question:
            continue
        if question.lower() in ("back", "quit", "exit"):
            break
        if question.lower() in ("confront", "c"):
            confront_suspect(state, suspect_name, model=model)
            continue

        evidence_backed, matched_files = forensics.assess_claim(state, question)
        extra_context = _bluff_directive(evidence_backed, matched_files)

        answer = llm.safe_chat(model, system_prompt, question, char_state.history, extra_context, first_name)
        if answer is None:
            continue

        print(f"\n{suspect_name}: {answer}\n")
        flagged = forensics.flag_possible_hallucination(state, suspect_name, answer)
        _print_hallucination_hint(flagged, first_name)

        char_state.record(question, answer)
        char_state.log_event("Detective", "QUESTION", question, evidence_backed=evidence_backed)
        char_state.log_event(suspect_name, "CLAIM", answer, evidence_backed=evidence_backed,
                              flagged_as_hallucination=flagged)
        state.questions_asked += 1

    print(f"\n[Interview with {suspect_name} paused. "
          f"{char_state.questions_answered} question(s) asked so far this playthrough.]")
    input("[press Enter to continue] ")


def confront_suspect(state, suspect_name: str, model: str = LLAMA_MODEL) -> None:
    """
    The [C] Confront / Present Evidence action: pick a piece of evidence
    you've already examined, pose a challenge, and get a reaction with
    EVIDENCE_BACKED: True guaranteed (not inferred) since the player can
    only select evidence they've genuinely discovered. Counts as an
    interrogation turn (state.questions_asked) the same as an ordinary
    question.
    """
    public_info = state.get_suspect_public_info(suspect_name)
    profile = get_character(suspect_name)
    if public_info is None or profile is None:
        print(f"'{suspect_name}' isn't a suspect in this case.")
        return

    entries = [e for e in state.evidence.list_entries() if e["discovered"]]
    if not entries:
        print("\nYou haven't examined any evidence yet -- check the Evidence Locker first.")
        input("[press Enter to continue] ")
        return

    clear_screen()
    print(f"=== Confront {suspect_name} with Evidence ===\n")
    for i, e in enumerate(entries, start=1):
        print(f"[{i}] {e['filename']}")
    print("\n[0] Cancel")
    choice = input("\nPresent which evidence? > ").strip()
    if not choice.isdigit():
        return
    idx = int(choice)
    if idx == 0 or not (1 <= idx <= len(entries)):
        return
    evidence_file = entries[idx - 1]["filename"]

    challenge = input("\nWhat do you say as you present it? > ").strip()
    if not challenge:
        print("(You hesitate and say nothing.)")
        input("[press Enter to continue] ")
        return

    char_state = state.character_states.setdefault(
        suspect_name, CharacterState(suspect=profile)
    )
    system_prompt = build_system_prompt(
        suspect=profile,
        public_info=public_info,
        knowledge=state.case.knowledge_for(suspect_name),
        is_culprit=state.case.is_culprit(suspect_name),
        victim=state.case.victim_name(),
        word_limit=WORD_LIMIT,
    )
    extra_context = _confrontation_directive(state, suspect_name, evidence_file)
    first_name = _first_name(suspect_name)

    answer = llm.safe_chat(model, system_prompt, challenge, char_state.history, extra_context, first_name)
    if answer is None:
        input("[press Enter to continue] ")
        return

    print(f"\n[You present {evidence_file}.]")
    print(f'You: "{challenge}"\n')
    print(f"{suspect_name}: {answer}\n")
    flagged = forensics.flag_possible_hallucination(state, suspect_name, answer)
    _print_hallucination_hint(flagged, first_name)

    char_state.record(challenge, answer)
    char_state.log_event("Detective", "CONFRONTATION", challenge, evidence_backed=True)
    char_state.log_event(suspect_name, "REACTION", answer, evidence_backed=True,
                          flagged_as_hallucination=flagged)
    state.questions_asked += 1

    input("[press Enter to continue] ")


def show_archive(state) -> None:
    """
    Read-only Interview Archive (Stage 4): browse the full structured
    transcript (characters.InterrogationEvent list) of any suspect the
    player has already interviewed at least once. Pure display -- never
    mutates state.
    """
    clear_screen()
    interviewed = [name for name, cs in state.character_states.items() if cs.events]
    if not interviewed:
        print("No interviews on record yet -- go talk to a suspect first.")
        input("[press Enter to continue] ")
        return

    print("=== Interview Archive ===\n")
    for i, name in enumerate(interviewed, start=1):
        count = state.character_states[name].questions_answered
        print(f"[{i}] {name} ({count} question(s))")
    print("\n[0] Back")

    choice = input("\n> ").strip()
    if not choice.isdigit():
        return
    idx = int(choice)
    if idx == 0 or not (1 <= idx <= len(interviewed)):
        return

    name = interviewed[idx - 1]
    clear_screen()
    print(f"=== Transcript: {name} ===\n")
    for event in state.character_states[name].events:
        speaker_label = "You" if event.speaker == "Detective" else event.speaker
        tag = f" ({event.event_type.lower()})" if event.event_type in ("CONFRONTATION", "REACTION") else ""
        print(f"[{event.turn_id}] {speaker_label}{tag}: {event.content}")
        if event.flagged_as_hallucination:
            print("     (heuristic flag: this answer may not be grounded)")
    input("\n[press Enter to continue] ")
