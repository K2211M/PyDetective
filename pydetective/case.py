"""
case.py
-------
Centralized Case domain object (Stage 2.5). Replaces the Stage 1/2 pattern
of GameState juggling two loose dicts (case_data / case_truth) with one
object that owns everything about a single case: the public briefing, the
six suspects' public info, the hidden ground truth, per-character
knowledge sheets, the evidence manifest, the predefined contradiction
pairings, and the conditions verdict.py checks at accusation time.

Python remains the sole source of truth. Nothing in this module is ever
populated by an LLM -- Qwen only ever produces the JSON that load()
parses, and everything below the "hidden" line is exposed through
narrow, purpose-labeled accessors rather than a raw dict, so a careless
`print(case_truth)` elsewhere in the codebase simply isn't possible
anymore -- there is no raw case_truth dict floating around to print.

Stage 4.5 scope (added): case_truth.json can now be written in either the
original flat schema or a newer nested one (actual_truth /
investigation_truth / deception groups) -- see
case_validator.normalize_truth()'s docstring for the exact field mapping
and the reasoning behind it. load() normalizes before building anything,
so everything below this point never has to know which schema version a
given case file was actually written in. Case also gained three new
narrow accessors: supporting_evidence(), method_keyword() (both used by
case_validator's logical-consistency checks, and available for future
gameplay use), and deception_info() (reserved entirely for a future
deception/framing mechanic -- nothing in this codebase reads it yet).
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from characters import CharacterKnowledge
from case_validator import CaseValidator, normalize_truth


@dataclass
class Contradiction:
    """
    One predefined, deterministic contradiction the player can find: a
    suspect's claim that conflicts with a fact recorded in an evidence
    file. Matching is exact (suspect name + evidence filename) -- Stage
    2.5 does not attempt fuzzy/semantic contradiction detection, on
    purpose, so results are always fair and reproducible.
    """
    id: str
    suspect: str
    claim: str
    evidence_file: str
    evidence_fact: str
    description: str


@dataclass
class Case:
    """Everything about one case, loaded from case.json + case_truth.json."""
    case_id: str
    case_dir: Path

    # --- Public: safe to print to the player before the verdict ---------
    briefing: dict
    suspects: list[dict]            # each: name, relation_to_victim, alibi, public_statement
    evidence_manifest: list[dict]   # each: filename, description

    # --- Hidden: gated behind the accessors below, never printed as-is --
    _victim: str
    _culprit: str
    _motive: str
    _method: str
    _true_timeline: list[dict]
    _character_knowledge: dict[str, CharacterKnowledge]
    _contradictions: list[Contradiction]
    _solution_conditions: dict = field(default_factory=dict)
    _supporting_evidence: list[str] = field(default_factory=list)
    _method_keyword: Optional[str] = None
    _deception: dict = field(default_factory=dict)

    # ---- public, spoiler-free lookups -----------------------------------
    def get_suspect_public_info(self, name: str) -> Optional[dict]:
        """This suspect's case.json entry (relation/alibi/statement), or None."""
        for s in self.suspects:
            if s["name"] == name:
                return s
        return None

    def all_contradictions(self) -> list[Contradiction]:
        """
        The full predefined contradiction list, for computing totals (e.g.
        the verdict score's denominator). Do NOT print this list itself to
        the player before the verdict -- it's the answer key. Use
        find_contradiction() to check one specific guess instead.
        """
        return list(self._contradictions)

    def find_contradiction(self, suspect: str, evidence_file: str) -> Optional[Contradiction]:
        """The Contradiction matching this exact (suspect, evidence_file) pair, if any."""
        for c in self._contradictions:
            if c.suspect == suspect and c.evidence_file == evidence_file:
                return c
        return None

    # ---- grounding-only accessors (see characters.build_system_prompt) --
    def knowledge_for(self, suspect_name: str) -> CharacterKnowledge:
        """This suspect's bounded CharacterKnowledge sheet (empty if none defined)."""
        return self._character_knowledge.get(suspect_name, CharacterKnowledge())

    def is_culprit(self, suspect_name: str) -> bool:
        """Whether `suspect_name` is the true culprit -- a bool, never the culprit's name."""
        return suspect_name == self._culprit

    def victim_name(self) -> str:
        return self._victim

    # ---- verdict-only accessors (see verdict.py) -------------------------
    # Nothing else in the codebase should call these before the accusation
    # is actually made -- they're the spoilers.
    def culprit_name(self) -> str:
        return self._culprit

    def motive(self) -> str:
        return self._motive

    def method(self) -> str:
        return self._method

    def true_timeline(self) -> list[dict]:
        return list(self._true_timeline)

    def required_evidence_for_method(self) -> list[str]:
        """
        Evidence filenames that count as "primary evidence linking the
        accused to the method" at the accusation screen. Any one of these
        submitted counts as correct -- see verdict.py.
        """
        return list(self._solution_conditions.get("required_evidence_for_method", []))

    def supporting_evidence(self) -> list[str]:
        """
        Evidence filenames the case author marked as genuinely proving the
        true solution (as opposed to red herrings). Currently used only by
        case_validator's logical-consistency checks; not wired into any
        gameplay UI yet.
        """
        return list(self._supporting_evidence)

    def method_keyword(self) -> Optional[str]:
        """The short substance/method string case_validator cross-checks -- may be None."""
        return self._method_keyword

    def deception_info(self) -> dict:
        """
        Reserved for a future deception/framing mechanic (planted evidence,
        a false narrative pointing at an innocent suspect). Nothing in this
        codebase reads or acts on this yet -- it's schema-ready, not
        feature-ready.
        """
        return dict(self._deception)


def load(case_dir: Path, case_id: str) -> Case:
    """
    Load one case folder into a Case object.

    Expects, under case_dir:
        case.json         - public briefing, suspects, evidence manifest
        case_truth.json   - hidden ground truth, in either the flat or the
                             nested Stage 4.5 schema (see
                             case_validator.normalize_truth())
        evidence/          - the raw evidence files named in the manifest
                             (see case_validator.EVIDENCE_SUBDIR)

    Runs the full CaseValidator suite (schema/reference/solvability/
    logical-consistency checks) before building anything, and raises a
    single ValueError listing every problem found if validation fails --
    so a malformed case (hand-written typo, or a bad Qwen generation)
    fails loudly and completely at load time instead of confusingly
    mid-playthrough.
    """
    case_path = case_dir / "case.json"
    truth_path = case_dir / "case_truth.json"

    if not case_path.exists():
        raise FileNotFoundError(f"No case.json found for '{case_id}' in {case_dir}")
    if not truth_path.exists():
        raise FileNotFoundError(f"No case_truth.json found for '{case_id}' in {case_dir}")

    with case_path.open(encoding="utf-8") as f:
        public = json.load(f)
    with truth_path.open(encoding="utf-8") as f:
        raw_truth = json.load(f)

    is_valid, errors = CaseValidator(case_dir, case_id).validate_data(public, raw_truth)
    if not is_valid:
        bullet_list = "\n".join(f"  - {e}" for e in errors)
        raise ValueError(f"Case '{case_id}' failed validation:\n{bullet_list}")

    # From here on, validation has already confirmed every reference is
    # sound -- no need to re-check suspect names / evidence filenames /
    # duplicate ids while building the dataclasses below. normalize_truth()
    # collapses either schema version into the same flat shape.
    truth = normalize_truth(raw_truth)

    knowledge: dict[str, CharacterKnowledge] = {
        name: CharacterKnowledge(
            known_facts=sheet.get("known_facts", []),
            beliefs=sheet.get("beliefs", []),
            secrets=sheet.get("secrets", []),
            lies=sheet.get("lies", []),
            unknown_information=sheet.get("unknown_information", []),
        )
        for name, sheet in truth.get("character_knowledge", {}).items()
    }

    contradictions = [Contradiction(**c) for c in truth.get("contradictions", [])]

    return Case(
        case_id=case_id,
        case_dir=case_dir,
        briefing=public["briefing"],
        suspects=public["suspects"],
        evidence_manifest=public["evidence_files"],
        _victim=truth["victim"],
        _culprit=truth["culprit"],
        _motive=truth["motive"],
        _method=truth["method"],
        _true_timeline=truth.get("true_timeline", []),
        _character_knowledge=knowledge,
        _contradictions=contradictions,
        _solution_conditions=truth.get("solution_conditions", {}),
        _supporting_evidence=truth.get("supporting_evidence", []),
        _method_keyword=truth.get("method_keyword"),
        _deception=truth.get("deception", {}),
    )
