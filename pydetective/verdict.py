"""
verdict.py
----------
The end-game accusation screen and scoring engine (Stage 2.5, pulled
forward from the original Stage 4 plan).

Flow: accuse one of the six fixed suspects, then submit the one piece of
evidence you believe links them to the method. Both are compared against
case.Case's hidden accessors (culprit_name(), required_evidence_for_method())
-- the only two places in the whole codebase allowed to call those before
this screen runs.

Score (100 points total, before hint penalties):
    Correct culprit          -- 50 pts, all-or-nothing
    Evidence discovered      -- up to 20 pts, scaled by fraction of the
                                 case's evidence manifest examined
    Contradictions uncovered -- up to 20 pts, scaled by fraction of the
                                 case's predefined contradictions found
    Efficiency (questions)   -- up to 10 pts, full credit at or below
                                 EFFICIENT_QUESTION_THRESHOLD questions
                                 asked, decaying to 0 by ZERO_CREDIT_THRESHOLD

Stage 4 scope (added): a hint penalty, subtracted from the sum of the
four buckets above. Every hint tier the player unlocked via Ravi
(partner.py, state.hint_tier_reached) costs partner.TIER_PENALTIES[tier]
points -- using the HIGHEST tier reached per contradiction, not summed
across tiers (going straight to tier 2 costs the same as working up
through tier 1 first). A correct, un-hinted hypothesis to Ravi costs
nothing at all -- the penalty only applies to contradictions the player
needed help with.

Note on the "primary evidence" submission: the weight breakdown above
sums to 100 without it, so it's reported as a correct/incorrect line in
the post-mortem rather than folded into the numeric score -- narrative
payoff for careful reasoning, without double-counting evidence you may
have already gotten credit for finding. If you'd rather it carry its own
point weight, that's a one-line change to `score_verdict()` below.
"""

import os
from typing import Optional

import partner

EFFICIENT_QUESTION_THRESHOLD = 12  # full 10 efficiency points at or below this
ZERO_CREDIT_THRESHOLD = 30         # 0 efficiency points at or above this


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def run_verdict(state) -> None:
    """
    Entry point for menu option [Make Your Accusation]. If the player has
    already made their final accusation this playthrough, re-displays that
    same verdict instead of letting them change their answer.
    """
    if state.accusation_made is not None:
        clear_screen()
        print("=== Case Closed ===\n")
        print(f"You already made your final accusation: {state.accusation_made}\n")
        _print_report(state)
        input("\n[press Enter to continue] ")
        return

    accused = _choose_accused(state)
    if accused is None:
        return

    evidence_file = _choose_method_evidence(state)
    if evidence_file is None:
        return

    state.accusation_made = accused
    state.accused_evidence_file = evidence_file

    clear_screen()
    print("=== Verdict ===\n")
    _print_report(state)
    input("\n[press Enter to continue] ")


def _choose_accused(state) -> Optional[str]:
    clear_screen()
    print("=== Make Your Accusation ===\n")
    print("This is final -- choose carefully.\n")
    for i, s in enumerate(state.suspects, start=1):
        print(f"[{i}] {s['name']}")
    print("\n[0] Cancel, I'm not ready")

    choice = input("\nWho did it? > ").strip()
    if not choice.isdigit():
        return None
    idx = int(choice)
    if idx == 0 or not (1 <= idx <= len(state.suspects)):
        return None
    return state.suspects[idx - 1]["name"]


def _choose_method_evidence(state) -> Optional[str]:
    entries = [e for e in state.evidence.list_entries() if e["discovered"]]
    clear_screen()
    print("=== Submit Your Primary Evidence ===\n")
    print("Which piece of evidence you've examined best links your suspect to the method?\n")
    if not entries:
        print("(You haven't examined any evidence -- this will count against you.)")
        input("[press Enter to continue] ")
        return "none"

    for i, e in enumerate(entries, start=1):
        print(f"[{i}] {e['filename']}")
    print("\n[0] Cancel, I'm not ready")

    choice = input("\n> ").strip()
    if not choice.isdigit():
        return None
    idx = int(choice)
    if idx == 0 or not (1 <= idx <= len(entries)):
        return None
    return entries[idx - 1]["filename"]


def score_verdict(state) -> dict:
    """
    Pure scoring function -- no printing, so it's easy to unit-test or
    reuse (e.g. a future high-score list). Returns a breakdown dict; see
    module docstring for the weighting rationale.
    """
    correct_culprit = state.accusation_made == state.case.culprit_name()
    culprit_score = 50 if correct_culprit else 0

    total_evidence = state.evidence.total_count()
    evidence_fraction = (state.evidence.discovered_count() / total_evidence) if total_evidence else 1.0
    evidence_score = round(20 * evidence_fraction)

    total_contradictions = state.total_contradictions()
    contradiction_fraction = (state.contradictions_found / total_contradictions) if total_contradictions else 1.0
    contradiction_score = round(20 * contradiction_fraction)

    asked = state.questions_asked
    if asked <= EFFICIENT_QUESTION_THRESHOLD:
        efficiency_score = 10
    elif asked >= ZERO_CREDIT_THRESHOLD:
        efficiency_score = 0
    else:
        span = ZERO_CREDIT_THRESHOLD - EFFICIENT_QUESTION_THRESHOLD
        efficiency_score = round(10 * (ZERO_CREDIT_THRESHOLD - asked) / span)

    correct_method_evidence = state.accused_evidence_file in state.case.required_evidence_for_method()

    hint_penalty = sum(partner.TIER_PENALTIES.get(tier, 0) for tier in state.hint_tier_reached.values())
    raw_total = culprit_score + evidence_score + contradiction_score + efficiency_score
    total_score = max(0, raw_total - hint_penalty)

    return {
        "correct_culprit": correct_culprit,
        "culprit_score": culprit_score,
        "evidence_discovered": state.evidence.discovered_count(),
        "evidence_total": total_evidence,
        "evidence_score": evidence_score,
        "contradictions_found": state.contradictions_found,
        "contradictions_total": total_contradictions,
        "contradiction_score": contradiction_score,
        "questions_asked": asked,
        "efficiency_score": efficiency_score,
        "correct_method_evidence": correct_method_evidence,
        "hint_penalty": hint_penalty,
        "raw_total": raw_total,
        "total_score": total_score,
    }


def _print_report(state) -> None:
    breakdown = score_verdict(state)
    case = state.case

    print(f"You accused: {state.accusation_made}")
    if breakdown["correct_culprit"]:
        print("VERDICT: Correct. Case solved.\n")
    else:
        print(f"VERDICT: Incorrect. The real culprit was {case.culprit_name()}.\n")

    print(f"Motive: {case.motive()}")
    print(f"Method: {case.method()}\n")

    timeline = case.true_timeline()
    if timeline:
        print("True timeline:")
        for entry in timeline:
            print(f"  {entry.get('time', '?'):>6}  {entry.get('event', '')}")
        print()

    method_note = "correct" if breakdown["correct_method_evidence"] else "not the key piece"
    print(f"Primary evidence submitted: {state.accused_evidence_file}  ({method_note})")
    print()

    print("--- Detective Score ---")
    print(f"  Correct culprit:          {breakdown['culprit_score']:>3} / 50")
    print(f"  Evidence discovered:      {breakdown['evidence_score']:>3} / 20  "
          f"({breakdown['evidence_discovered']}/{breakdown['evidence_total']})")
    print(f"  Contradictions uncovered: {breakdown['contradiction_score']:>3} / 20  "
          f"({breakdown['contradictions_found']}/{breakdown['contradictions_total']})")
    print(f"  Efficiency (questions):   {breakdown['efficiency_score']:>3} / 10  "
          f"({breakdown['questions_asked']} asked)")
    if breakdown["hint_penalty"]:
        print(f"  Subtotal:                 {breakdown['raw_total']:>3} / 100")
        print(f"  Hint penalty (Ravi):      -{breakdown['hint_penalty']:>2}")
    print(f"  {'TOTAL':<26}{breakdown['total_score']:>3} / 100")
