"""
main.py
-------
PyDetective -- CLI entry point.

Stage 1 scope: load a case, browse the briefing / suspect dossier / evidence,
all backed by real GameState + EvidenceRegistry logic from game.py and
evidence.py.

Stage 2 scope (added): menu option [4] runs a real interrogation through
interrogation.py / llm.py against a local Ollama server.

Stage 2.5/3 scope (added, later removed -- see Stage 4 below): manual and
proactive contradiction-flagging UI, driven by forensics.py.

Stage 3.5 scope (added): a 'confront' command inside an active interview
(menu [4]) presents evidence to a suspect directly.

Stage 4 scope (changed): "pure detective" pivot --
    - Removed: the old "Flag a Contradiction" menu item and the proactive
      "[!] Potential Contradiction Detected" popups. The engine now only
      ever shows raw observations; contradictions are found by consulting
      the Detective Partner (menu [Consult Ravi], partner.py), never
      auto-flagged. A standalone top-level "Confront a Suspect" entry was
      also folded away -- it's reachable via the 'confront' command
      inside an active interview instead, which is where it made sense
      to begin with.
    - Added: [Interview Archive] (interrogation.show_archive) to review
      past transcripts, [Consult Detective Partner] (partner.py), and
      case generation via case_generator.py, offered at the case-picker
      screen alongside any hand-written cases already on disk.
    - Kept, as a deliberate deviation from the spec's proposed leaner
      menu: [Suspect Dossier]. Dropping it looked like an unrelated
      usability cut rather than something the "pure detective" framing
      actually called for -- it's flagged here in case that's wrong.

Stage 4.5 scope (added): `python3 main.py --validate-all` batch-runs
case_validator.CaseValidator against every case under cases/ and reports
pass/fail, without starting the game loop.

Run with:  python3 main.py
           python3 main.py --validate-all
"""

import os
import sys

import case_generator
import game
import interrogation
import llm
import partner
import verdict
from case_validator import CaseValidator
from characters import get_character

BANNER = r"""
 ____        ____       _            _   _
|  _ \ _   _|  _ \  ___| |_ ___  ___| |_(_)_   _____
| |_) | | | | | | |/ _ \ __/ _ \/ __| __| \ \ / / _ \
|  __/| |_| | |_| |  __/ ||  __/ (__| |_| |\ V /  __/
|_|    \__, |____/ \___|\__\___|\___|\__|_| \_/ \___|
       |___/
"""


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause() -> None:
    input("\n[press Enter to continue] ")


def print_menu(state: "game.GameState") -> None:
    print(f"\nCase: {state.case_id}  |  Evidence: "
          f"{state.evidence.discovered_count()}/{state.evidence.total_count()}  |  "
          f"Contradictions: {state.contradictions_found}/{state.total_contradictions()}  |  "
          f"Questions asked: {state.questions_asked}")
    print("-" * 60)
    print("[1] Case Briefing")
    print("[2] Suspect Dossier")
    print("[3] Evidence Browser")
    print("[4] Interrogate a Suspect")
    print("[5] Interview Archive")
    print("[6] Consult Detective Partner (Ravi)")
    print("[7] Make Your Accusation")
    print("[8] Quit")


def show_briefing(state: "game.GameState") -> None:
    b = state.briefing
    clear_screen()
    print(f"=== {b.get('title', 'Case Briefing')} ===\n")
    print(f"Victim:   {b.get('victim', 'Unknown')}")
    print(f"Location: {b.get('location', 'Unknown')}")
    print(f"Date:     {b.get('date', 'Unknown')}\n")
    print(b.get("summary", ""))
    print()
    print(b.get("incident", ""))
    pause()


def show_suspects(state: "game.GameState") -> None:
    clear_screen()
    print("=== Suspect Dossier ===\n")
    for s in state.suspects:
        profile = get_character(s["name"])
        print(f"- {s['name']}  ({profile.personality if profile else 'Unknown'})")
        print(f"    Relation to victim: {s.get('relation_to_victim', 'Unknown')}")
        print(f"    Alibi:              {s.get('alibi', 'Unknown')}")
        print(f"    Statement:          \"{s.get('public_statement', '')}\"")
        print()
    pause()


def examine_evidence(state: "game.GameState") -> None:
    while True:
        clear_screen()
        print("=== Evidence Browser ===\n")
        entries = state.evidence.list_entries()
        for i, e in enumerate(entries, start=1):
            tag = "[FOUND]" if e["discovered"] else "[unopened]"
            missing = "" if e["exists"] else "  (file missing on disk!)"
            print(f"[{i}] {e['filename']:<20} {tag}{missing}")
            print(f"      {e['description']}")
        print("\n[0] Back to main menu")

        choice = input("\nOpen which item? > ").strip()
        if choice == "0":
            return
        if not choice.isdigit() or not (1 <= int(choice) <= len(entries)):
            print("Not a valid item number.")
            pause()
            continue

        filename = entries[int(choice) - 1]["filename"]
        clear_screen()
        print(f"=== {filename} ===\n")
        try:
            print(state.evidence.read(filename))
        except (FileNotFoundError, ValueError) as e:
            print(f"Could not open that file: {e}")
        pause()


def choose_suspect(state: "game.GameState") -> "str | None":
    """Numbered picker over the fixed cast; returns None if the player backs out."""
    clear_screen()
    print("=== Who do you want to interrogate? ===\n")
    for i, s in enumerate(state.suspects, start=1):
        asked = state.character_states.get(s["name"])
        tag = f"  [{asked.questions_answered} question(s) asked]" if asked else ""
        print(f"[{i}] {s['name']}{tag}")
    print("\n[0] Back to main menu")

    choice = input("\n> ").strip()
    if choice == "0" or not choice.isdigit():
        return None
    idx = int(choice)
    if not (1 <= idx <= len(state.suspects)):
        return None
    return state.suspects[idx - 1]["name"]


def interrogate_suspect(state: "game.GameState") -> None:
    suspect_name = choose_suspect(state)
    if suspect_name is None:
        return
    interrogation.run_interrogation(state, suspect_name)


def choose_case() -> str:
    """
    Case picker, shown every run (not just when multiple cases exist),
    since generating a new one is now always an option.
    """
    cases = game.list_available_cases()

    print("Available cases:")
    for i, c in enumerate(cases, start=1):
        print(f"  [{i}] {c}")
    print(f"  [G] Generate a new case with Qwen ({case_generator.QWEN_MODEL})")

    while True:
        choice = input("\nChoose a case number, or 'G' to generate > ").strip()
        if choice.lower() == "g":
            print(f"\nGenerating a new case with {case_generator.QWEN_MODEL} -- this can take "
                  f"a couple of minutes...\n")
            try:
                return case_generator.generate_case()
            except case_generator.CaseGenerationError as exc:
                print(f"\nCase generation failed:\n{exc}\n")
            except llm.LLMError as exc:
                print(f"\nCouldn't reach Qwen: {exc}\n")
            continue
        if choice.isdigit() and cases and 1 <= int(choice) <= len(cases):
            return cases[int(choice) - 1]
        print("Not a valid choice.")


def main() -> None:
    clear_screen()
    print(BANNER)
    case_id = choose_case()

    try:
        state = game.load_case(case_id)
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"Failed to load case '{case_id}': {e}")
        sys.exit(1)

    if not llm.check_model_available(interrogation.LLAMA_MODEL):
        print(f"\n[Heads up: could not confirm '{interrogation.LLAMA_MODEL}' is available on "
              f"the local Ollama server. Interrogation and Ravi need `ollama serve` running "
              f"and `ollama pull {interrogation.LLAMA_MODEL}` done. Everything else works fine "
              f"without it.]")
        pause()

    while True:
        clear_screen()
        print(BANNER)
        print_menu(state)
        choice = input("\n> ").strip()

        if choice == "1":
            show_briefing(state)
        elif choice == "2":
            show_suspects(state)
        elif choice == "3":
            examine_evidence(state)
        elif choice == "4":
            interrogate_suspect(state)
        elif choice == "5":
            interrogation.show_archive(state)
        elif choice == "6":
            partner.consult_partner(state)
        elif choice == "7":
            verdict.run_verdict(state)
        elif choice == "8":
            print("\nCase file closed. See you, detective.")
            break
        else:
            print("Not a valid option.")
            pause()


def validate_all_cases() -> None:
    """
    `python3 main.py --validate-all` -- batch-runs CaseValidator against
    every case folder under cases/ (hand-written and Qwen-generated
    alike) and prints a pass/fail report, without starting the game
    loop. Exits 0 if every case is valid, 1 otherwise -- suitable for a
    CI check or a quick sanity run after hand-editing a case file.
    """
    cases = game.list_available_cases()
    if not cases:
        print(f"No cases found under {game.CASES_DIR}/.")
        sys.exit(0)

    print(f"Validating {len(cases)} case(s) under {game.CASES_DIR}/...\n")
    all_ok = True
    for case_id in cases:
        case_dir = game.CASES_DIR / case_id
        ok, errors = CaseValidator(case_dir, case_id).validate()
        print(f"[{'PASS' if ok else 'FAIL'}] {case_id}")
        if not ok:
            all_ok = False
            for e in errors:
                print(f"    - {e}")

    print()
    print("All cases valid." if all_ok else "Some cases failed validation -- see above.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    if "--validate-all" in sys.argv:
        validate_all_cases()
    else:
        main()
