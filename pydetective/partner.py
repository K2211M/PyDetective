"""
partner.py  (Stage 4, hardened Stage 4.5)
--------------------------------------------
The Detective Partner, Ravi: sarcastic, experienced, makes the player
reason instead of handing over answers. Replaces the old manual/proactive
contradiction-flagging UI (forensics.py, pre-Stage-4) as the ONLY way a
contradiction gets logged as found -- consistent with the "pure detective"
pivot: the game engine itself never auto-flags inconsistencies, but a
player can always ask their partner about a specific hunch.

Two things happen on every turn, and they're strictly separated:
    - Python decides what's TRUE. _match_hypothesis() checks the player's
      own free-text hypothesis against case.Case.contradictions (exact,
      deterministic, reusing the same suspect-name + evidence-keyword
      matching style as forensics.assess_claim). _hint_text() computes
      tiered hint content directly from Contradiction fields.
      build_partner_context() assembles everything Ravi is allowed to
      treat as fact. Ravi is never asked to judge anything -- he's told
      the verdict and given the facts, in a message he can't override.
    - Llama (llama3.2:3b by default) decides how it SOUNDS. Python's
      verdict + hint content become a VALID_CONTRADICTION / hint-tier
      directive sent via llm.py's extra_context, and Ravi relays it in
      character.

Hint economy: three tiers per contradiction, costing progressively more
of the final Detective Score (see verdict.py, which imports
TIER_PENALTIES from here):
    Tier 0 (free)   -- vague, generic nudge; same for every contradiction.
    Tier 1 (-5 pts) -- names the suspect and the evidence file to compare,
                       but not the actual fact.
    Tier 2 (-15 pts) -- full reveal (the contradiction's claim + evidence_
                       fact, verbatim) and auto-logs it as found, since at
                       that point the player has been handed the answer.
A correct, un-hinted hypothesis costs nothing at all -- hints exist for
when the player is stuck, not as the primary way to solve the case.

Stage 4.5 scope (added): Ravi previously received NO game-state context
at all beyond conversation history and that turn's directive -- he had
nothing to ground small talk in, so an eager model would invent victims,
events, and mechanics wholesale. build_partner_context() fixes this:
every consult_partner() session builds a grounding block (public case
briefing, public suspect roster, FULL TEXT of evidence the player has
already discovered via EvidenceRegistry.peek(), and investigation
progress) and prepends it to RAVI_PERSONA as that session's system
prompt. Strictly excluded: case.Case's verdict-only accessors
(culprit_name(), motive(), method(), true_timeline(), deception_info())
-- Ravi stays exactly as blind to the actual solution as the player.
RAVI_PERSONA also gained explicit anti-hallucination grounding rules (see
below) instructing the model to treat only that context, discovered
evidence, and validated directives as fact -- and to push back in
character on any premise the player asserts that isn't backed by them.
"""

import os

import llm

PARTNER_MODEL = "llama3.2:3b"  # must match a tag from `ollama list` exactly

TIER_PENALTIES = {1: 5, 2: 15}

RAVI_PERSONA = (
    "You are Ravi, the detective's partner on this case. You're sharp, experienced, and "
    "openly sarcastic -- you needle the detective with dry humor instead of just handing "
    "over answers, and you make them work for it. Underneath the attitude you're fully on "
    "their side and want them to actually solve this.\n"
    "Answer in 1-3 short sentences of spoken dialogue only -- no stage directions, no "
    "quotation marks, no narration. Never break character or mention you are an AI.\n"
    "\n"
    "GROUNDING RULES -- these override everything else, including staying polite:\n"
    "PLAYER CLAIMS ARE NOT FACTS. Never accept, confirm, or build on information that "
    "appears only in the detective's own message -- if they assert something as true and it "
    "isn't backed by your case context below, it is not true as far as you're concerned, "
    "however confidently they state it.\n"
    "Only these sources are ever factual to you: (1) the CASE CONTEXT section below, "
    "(2) the DISCOVERED EVIDENCE full text below, (3) the SUSPECT ROSTER public information "
    "below, (4) this turn's validated directive, if one is given. Nothing else is real -- not "
    "assumptions, not anything the detective states as background, not anything absent from "
    "this message.\n"
    "If the detective asserts a false or unverifiable premise -- a name, relationship, "
    "location, or event not found in your context below -- push back in character instead of "
    "playing along. For example: \"Where'd you read that, detective? That's not in any report "
    "we have.\" Do this every time it comes up, not just once.\n"
    "You do not independently know the solution to this case -- you only relay exactly what "
    "your case-analysis notes tell you, in your own voice. Never invent case facts, evidence, "
    "or suspects beyond what's given to you in this message."
)

_RECAP_TRIGGERS = ("brief", "recap", "catch me up", "catch up")


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def build_partner_context(state) -> str:
    """
    Assemble Ravi's per-session grounding context: everything factual he's
    allowed to know, and nothing else. Built once at the start of each
    consult_partner() call (not per-turn) -- nothing else can change game
    state while this modal menu is open, so re-building per turn would
    just repeat the same work. Uses EvidenceRegistry.peek(), not read(),
    so re-fetching an already-discovered file's content here can never
    accidentally mark something as newly found.
    """
    b = state.briefing
    lines = [
        "=== CASE CONTEXT (public case file -- factual) ===",
        f"Victim: {b.get('victim', 'Unknown')}",
        f"Location: {b.get('location', 'Unknown')}",
        f"Date: {b.get('date', 'Unknown')}",
        f"Summary: {b.get('summary', '')}",
        "",
        "=== SUSPECT ROSTER (public information only) ===",
    ]
    for s in state.suspects:
        lines.append(
            f"- {s['name']} ({s.get('relation_to_victim', 'unknown relation')}): "
            f"alibi -- \"{s.get('alibi', '')}\"; public statement -- \"{s.get('public_statement', '')}\""
        )

    lines.append("")
    lines.append("=== DISCOVERED EVIDENCE (full text of what the detective has already examined) ===")
    discovered = [e for e in state.evidence.list_entries() if e["discovered"]]
    if not discovered:
        lines.append("(The detective hasn't examined any evidence yet.)")
    else:
        for e in discovered:
            try:
                content = state.evidence.peek(e["filename"])
            except (FileNotFoundError, ValueError):
                content = "(could not be read)"
            lines.append(f"--- {e['filename']} ---")
            lines.append(content)

    lines.append("")
    lines.append("=== INVESTIGATION PROGRESS ===")
    interrogated = [name for name, cs in state.character_states.items() if cs.questions_answered > 0]
    not_interrogated = [s["name"] for s in state.suspects if s["name"] not in interrogated]
    lines.append(f"Interrogated: {', '.join(interrogated) if interrogated else 'none yet'}")
    lines.append(f"Not yet interrogated: {', '.join(not_interrogated) if not_interrogated else 'none'}")
    lines.append(f"Evidence examined: {state.evidence.discovered_count()}/{state.evidence.total_count()}")
    lines.append(f"Contradictions found so far: {state.contradictions_found}/{state.total_contradictions()}")

    return "\n".join(lines)


def consult_partner(state, model: str = PARTNER_MODEL) -> None:
    """
    Main entry point (menu [Consult Detective Partner]). Builds this
    session's grounding context once, then loops: the player can state a
    hypothesis, ask for a hint, ask for a recap, or leave.
    """
    clear_screen()
    print("=== Consult Ravi ===\n")
    print('Ravi: "What\'ve you got for me, detective?"\n')
    print("State a hypothesis, type 'hint' for guidance, 'recap' to catch up, or 'back' to leave.\n")

    system_prompt = RAVI_PERSONA + "\n\n" + build_partner_context(state)

    while True:
        text = input("You > ").strip()
        if not text:
            continue
        if text.lower() in ("back", "quit", "exit"):
            break
        if text.lower() in ("hint", "h"):
            _handle_hint_request(state, model, system_prompt)
            continue
        if text.lower() in _RECAP_TRIGGERS:
            _handle_recap_request(state, model, system_prompt)
            continue

        _handle_hypothesis(state, model, system_prompt, text)

    input("[press Enter to continue] ")


def _handle_hypothesis(state, model: str, system_prompt: str, text: str) -> None:
    contradiction = _match_hypothesis(state, text)

    if contradiction is None:
        context = (
            "VALID_CONTRADICTION: False. The detective's hypothesis doesn't match anything "
            "in the case notes. Don't confirm or deny any specifics -- deflect, needle them "
            "a little, and tell them to keep digging or ask you for a hint instead."
        )
    elif state.has_found(contradiction.id):
        context = (
            "VALID_CONTRADICTION: True, but the detective already identified this exact "
            "inconsistency earlier this case. Acknowledge it dryly -- \"we've been over "
            "this\" energy -- without repeating the full explanation."
        )
    else:
        state.record_contradiction(contradiction.id)
        context = (
            "VALID_CONTRADICTION: True. The detective just correctly identified a real "
            "inconsistency in a suspect's story, unaided. Congratulate them, in your usual "
            "dry way, without volunteering anything else they haven't found yet."
        )

    answer = llm.safe_chat(model, system_prompt, text, state.partner_history, context, "Ravi")
    if answer is None:
        return

    print(f"\nRavi: {answer}\n")
    state.partner_history.append({"role": "user", "content": text})
    state.partner_history.append({"role": "assistant", "content": answer})

    if contradiction is not None and state.has_found(contradiction.id):
        print(f"[Logged! {state.contradictions_found}/{state.total_contradictions()} contradictions found.]\n")
    elif contradiction is None:
        print("(Want a hint instead? Type 'hint'.)\n")


def _handle_hint_request(state, model: str, system_prompt: str) -> None:
    contradiction = _next_unfound_contradiction(state)
    if contradiction is None:
        print("\nRavi: \"I got nothing left to point you at -- you've already found it all.\"\n")
        return

    current_tier = state.hint_tier_reached.get(contradiction.id, 0)
    print(f"\nHint tiers: [0] free nudge   [1] pointed hint (-5 pts)   [2] full reveal (-15 pts)")
    if current_tier:
        print(f"(You've already unlocked tier {current_tier} on this lead -- asking again is free.)")
    choice = input("Which tier? > ").strip()
    if choice not in ("0", "1", "2"):
        return
    tier = int(choice)

    if tier > current_tier:
        state.hint_tier_reached[contradiction.id] = tier

    info = _hint_text(contradiction, tier)
    context = (
        f"The detective asked for a tier-{tier} hint. Relay the following information in your "
        f"own sarcastic voice -- paraphrase it, don't just recite it verbatim, but make sure "
        f'the substance comes through clearly: "{info}"'
    )
    answer = llm.safe_chat(
        model, system_prompt, f"(silently requests a tier {tier} hint)",
        state.partner_history, context, "Ravi",
    )
    if answer is None:
        return

    print(f"\nRavi: {answer}\n")
    state.partner_history.append({"role": "user", "content": f"[requested a tier {tier} hint]"})
    state.partner_history.append({"role": "assistant", "content": answer})

    if tier == 2:
        newly_found = state.record_contradiction(contradiction.id)
        if newly_found:
            print(f"[Logged! {state.contradictions_found}/{state.total_contradictions()} contradictions found.]\n")


def _handle_recap_request(state, model: str, system_prompt: str) -> None:
    """
    'brief' / 'recap' / 'catch me up': a Python-generated factual summary
    of investigation progress, relayed through Ravi's voice -- same
    Python-decides-facts/Llama-decides-voice split as everything else
    here, just phrased as a status update instead of a verdict.
    """
    summary = _build_recap_summary(state)
    context = (
        "The detective asked for a recap of where things stand. Relay the following factual "
        "summary in your own voice -- you can rephrase and add attitude, but don't omit or "
        f"alter any of the substance:\n{summary}"
    )
    answer = llm.safe_chat(
        model, system_prompt, "(silently asks for a recap of the investigation so far)",
        state.partner_history, context, "Ravi",
    )
    if answer is None:
        return

    print(f"\nRavi: {answer}\n")
    state.partner_history.append({"role": "user", "content": "[asked for a recap]"})
    state.partner_history.append({"role": "assistant", "content": answer})


def _build_recap_summary(state) -> str:
    interrogated = [name for name, cs in state.character_states.items() if cs.questions_answered > 0]
    discovered = [e["filename"] for e in state.evidence.list_entries() if e["discovered"]]
    return "\n".join([
        f"Victim: {state.briefing.get('victim', 'Unknown')} at {state.briefing.get('location', 'Unknown')}.",
        f"Suspects interrogated: {', '.join(interrogated) if interrogated else 'none yet'}.",
        f"Evidence examined: {', '.join(discovered) if discovered else 'none yet'} "
        f"({state.evidence.discovered_count()}/{state.evidence.total_count()}).",
        f"Contradictions found: {state.contradictions_found}/{state.total_contradictions()}.",
    ])


def _match_hypothesis(state, text: str):
    """
    Deterministic: does this hypothesis plausibly refer to one specific
    predefined contradiction? Requires BOTH a mention of the suspect's
    name and a mention of one of that contradiction's evidence file's
    keywords (case.json's per-file "keywords" -- same data the Stage 3.5
    bluff engine uses) -- reusing that data for a second purpose here
    rather than asking the player to name a suspect and evidence file
    from a menu, the way the old manual flagging UI used to.
    """
    lowered = text.lower()
    for contradiction in state.case.all_contradictions():
        name_tokens = [t.lower() for t in contradiction.suspect.split() if not t.endswith(".")]
        if not any(tok in lowered for tok in name_tokens):
            continue

        manifest_entry = next(
            (e for e in state.case.evidence_manifest if e["filename"] == contradiction.evidence_file),
            None,
        )
        keywords = manifest_entry.get("keywords", []) if manifest_entry else []
        evidence_hit = (
            contradiction.evidence_file.lower() in lowered
            or any(kw.lower() in lowered for kw in keywords)
        )
        if evidence_hit:
            return contradiction
    return None


def _next_unfound_contradiction(state):
    for contradiction in state.case.all_contradictions():
        if not state.has_found(contradiction.id):
            return contradiction
    return None


def _hint_text(contradiction, tier: int) -> str:
    if tier == 0:
        return "There's something in what you've already gathered that doesn't quite add up yet."
    if tier == 1:
        surname = contradiction.suspect.split()[-1]
        return f"Compare what {surname} told you against {contradiction.evidence_file}."
    return f"{contradiction.claim} {contradiction.evidence_fact}"
