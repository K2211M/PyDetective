"""
game.py
-------
Core game state. Python is the single source of truth for everything --
no LLM ever touches case_truth.json directly, and nothing here trusts
LLM output as fact.

GameState now wraps a single case.Case (see case.py) plus everything
that's specific to *this playthrough* rather than the case data itself:
question/contradiction counters, per-suspect conversation history, and
the eventual accusation. The case data itself -- public or hidden -- is
never duplicated onto GameState; callers go through state.case for that,
which keeps exactly one source of truth for it (case.py's job) and one
source of truth for session progress (this module's job).

Stage 2.5 scope (added):
    - Delegates case loading to case.load() instead of hand-rolling JSON
      parsing here.
    - found_contradiction_ids / record_contradiction(): dedup-safe
      contradiction tracking for forensics.py.

Stage 3 scope (added):
    - Evidence now lives under cases/CASE_XXX/evidence/ (case_validator's
      EVIDENCE_SUBDIR convention) rather than flat in the case folder;
      load_case() points EvidenceRegistry there.

Stage 4 scope (changed): the "pure detective" pivot removed the proactive
contradiction-suggestion system, so suggested_contradiction_ids is gone --
contradictions are only ever found through a player's hypothesis to their
Detective Partner (see partner.py). Added in its place:
    - partner_history: Ollama-shaped conversation history for Ravi, kept
      separately from any suspect's CharacterState.history.
    - hint_tier_reached: per-contradiction-id -> highest hint tier (0-2)
      the player has paid for via partner.py, used by verdict.py to
      compute the Detective Score's hint penalty.
"""

from pathlib import Path
from typing import Optional

import case as case_module
from case_validator import EVIDENCE_SUBDIR
from evidence import EvidenceRegistry
from characters import CharacterState

CASES_DIR = Path(__file__).parent / "cases"


class GameState:
    """Holds the active Case plus everything specific to this playthrough."""

    def __init__(self, case: "case_module.Case", evidence: EvidenceRegistry):
        self.case = case
        self.evidence = evidence

        # Session counters, used by verdict.py's Detective Score.
        self.questions_asked = 0
        self.contradictions_found = 0
        self.accusation_made: Optional[str] = None
        self.accused_evidence_file: Optional[str] = None

        # Dedup set backing contradictions_found -- see record_contradiction().
        self._found_contradiction_ids: set[str] = set()

        # Detective Partner (Ravi) state -- see partner.py.
        self.partner_history: list[dict] = []
        self.hint_tier_reached: dict[str, int] = {}

        # Per-suspect conversation memory, keyed by suspect name. Populated
        # lazily by interrogation.py the first time each suspect is questioned.
        self.character_states: dict[str, CharacterState] = {}

    # ---- thin passthroughs, so main.py/interrogation.py don't need to ----
    # ---- reach into state.case for the handful of things used everywhere -
    @property
    def case_id(self) -> str:
        return self.case.case_id

    @property
    def briefing(self) -> dict:
        return self.case.briefing

    @property
    def suspects(self) -> list[dict]:
        return self.case.suspects

    def get_suspect_public_info(self, name: str) -> Optional[dict]:
        return self.case.get_suspect_public_info(name)

    # ---- contradiction tracking (Stage 2.5) -------------------------------
    def record_contradiction(self, contradiction_id: str) -> bool:
        """
        Mark a contradiction as found this playthrough. Returns True if it
        was newly found, False if the player had already flagged it (so
        callers can tell "nice work" from "you already found that one").
        """
        if contradiction_id in self._found_contradiction_ids:
            return False
        self._found_contradiction_ids.add(contradiction_id)
        self.contradictions_found += 1
        return True

    def total_contradictions(self) -> int:
        return len(self.case.all_contradictions())

    def has_found(self, contradiction_id: str) -> bool:
        """Whether this contradiction has already been logged this playthrough."""
        return contradiction_id in self._found_contradiction_ids


def list_available_cases() -> list[str]:
    """Return case_ids (folder names) under cases/ that contain a case.json."""
    if not CASES_DIR.exists():
        return []
    return sorted(
        p.name for p in CASES_DIR.iterdir()
        if p.is_dir() and (p / "case.json").exists()
    )


def load_case(case_id: str) -> GameState:
    """
    Load a case folder into a fresh GameState. See case.load() for the
    expected file layout and validation rules.
    """
    case_dir = CASES_DIR / case_id
    loaded_case = case_module.load(case_dir, case_id)
    evidence_dir = case_dir / EVIDENCE_SUBDIR
    registry = EvidenceRegistry(case_dir=evidence_dir, manifest=loaded_case.evidence_manifest)
    return GameState(case=loaded_case, evidence=registry)


# ---------------------------------------------------------------------------
# case_generator.py (Stage 4) calls case.load() the same way load_case()
# does above -- generated cases are just another folder under CASES_DIR,
# so nothing here needs to change to support them.
# ---------------------------------------------------------------------------
