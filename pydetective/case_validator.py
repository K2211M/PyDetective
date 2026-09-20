"""
case_validator.py
------------------
Deterministic, stdlib-only sanity checks for one case folder -- run
BEFORE case.py builds a Case object from it, so a malformed hand-written
or Qwen-generated case fails loudly with a clear, aggregated error list
instead of a confusing exception partway through play.

Nothing here is fuzzy or LLM-judged in the sense of "ask a model whether
this is consistent" -- every check is a structural, referential, or plain
substring comparison. Two categories of check are explicitly heuristic
rather than exact (see _check_logical_consistency's docstring for why),
and are documented as such rather than oversold.

Evidence files are expected under cases/CASE_XXX/evidence/ -- see
EVIDENCE_SUBDIR. game.py points EvidenceRegistry at that same folder, so
this is the one place that convention is spelled out.

Stage 4.5 scope (added):
    - normalize_truth(): case_truth.json can now be written in either the
      original flat schema (culprit/motive/method/true_timeline directly
      at the top level) or the newer nested schema (actual_truth /
      investigation_truth / deception groups, see case.py's docstring for
      the field mapping). Both case.py and this module normalize to the
      same flat internal shape before doing anything else, so every
      existing check below is schema-version-agnostic and didn't need to
      change at all.
    - _check_logical_consistency(): three new checks that go beyond pure
      structure -- see its docstring for exactly what each one checks and,
      importantly, what it deliberately does NOT attempt.
"""

import json
import re
from pathlib import Path
from typing import Optional

from characters import CAST

EVIDENCE_SUBDIR = "evidence"

_CAST_NAMES = {s.name for s in CAST}

_DEFAULT_DECEPTION = {
    "enabled": False,
    "planted_target": None,
    "planted_evidence": [],
    "false_narrative": None,
}


def normalize_truth(raw: dict) -> dict:
    """
    Normalize case_truth.json into one flat shape, regardless of which
    schema it was written in:

        Stage <=4 (flat):    {"victim":..., "culprit":..., "motive":...,
                               "method":..., "true_timeline":[...],
                               "character_knowledge":{...},
                               "contradictions":[...],
                               "solution_conditions":{...}}

        Stage 4.5 (nested):  {"actual_truth": {"victim":..., "culprit":...,
                                "motive":..., "method":..., "method_keyword":...,
                                "timeline":[...]},
                               "investigation_truth": {"contradictions":[...],
                                "solution_conditions":{...},
                                "supporting_evidence":[...]},
                               "deception": {"enabled":..., "planted_target":...,
                                "planted_evidence":[...], "false_narrative":...},
                               "character_knowledge": {...}}

    character_knowledge stays a top-level sibling in both schemas -- it's
    per-suspect data, not part of "what actually happened" or "how the
    investigation proves it," so it didn't fit cleanly into either new
    group and the driving spec's example didn't show where it should go.
    victim/incident moved under actual_truth in the nested schema (they
    describe the actual crime, same as culprit/motive/method) -- also not
    shown in the spec's abbreviated example, so this is a judgment call,
    not a literal requirement.

    Returns a flat dict with keys: victim, incident, culprit, motive,
    method, method_keyword, true_timeline, character_knowledge,
    contradictions, solution_conditions, supporting_evidence, deception.
    """
    if "actual_truth" in raw:
        actual = raw.get("actual_truth", {}) or {}
        investigation = raw.get("investigation_truth", {}) or {}
        return {
            "victim": actual.get("victim"),
            "incident": actual.get("incident"),
            "culprit": actual.get("culprit"),
            "motive": actual.get("motive"),
            "method": actual.get("method"),
            "method_keyword": actual.get("method_keyword"),
            "true_timeline": actual.get("timeline", []),
            "character_knowledge": raw.get("character_knowledge", {}),
            "contradictions": investigation.get("contradictions", []),
            "solution_conditions": investigation.get("solution_conditions", {}),
            "supporting_evidence": investigation.get("supporting_evidence", []),
            "deception": raw.get("deception", dict(_DEFAULT_DECEPTION)),
        }

    # Older flat schema -- still fully supported.
    return {
        "victim": raw.get("victim"),
        "incident": raw.get("incident"),
        "culprit": raw.get("culprit"),
        "motive": raw.get("motive"),
        "method": raw.get("method"),
        "method_keyword": raw.get("method_keyword"),
        "true_timeline": raw.get("true_timeline", []),
        "character_knowledge": raw.get("character_knowledge", {}),
        "contradictions": raw.get("contradictions", []),
        "solution_conditions": raw.get("solution_conditions", {}),
        "supporting_evidence": raw.get("supporting_evidence", []),
        "deception": raw.get("deception", dict(_DEFAULT_DECEPTION)),
    }


_STOPWORDS = {
    "about", "after", "again", "their", "there", "these", "those", "which",
    "would", "could", "should", "before", "every", "still", "never", "claim",
    "claims", "public", "statement", "around", "alone", "night", "reason",
}


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z]{5,}", (text or "").lower()) if w not in _STOPWORDS}


class CaseValidator:
    """
    One-shot validator for a single case folder.

    Usage against files on disk:
        ok, errors = CaseValidator(case_dir, case_id).validate()

    Usage against already-parsed JSON (avoids reading the files twice --
    this is what case.py's load() uses, since it needs the parsed dicts
    either way). `truth` may be either schema shape; it's normalized
    internally:
        ok, errors = CaseValidator(case_dir, case_id).validate_data(public, truth)
    """

    def __init__(self, case_dir: Path, case_id: str):
        self.case_dir = case_dir
        self.case_id = case_id
        self.errors: list[str] = []

    def validate(self) -> tuple[bool, list[str]]:
        """Load case.json/case_truth.json from case_dir, then validate them."""
        self.errors = []
        public, truth = self._load_json()
        if public is None or truth is None:
            return False, list(self.errors)
        return self.validate_data(public, truth)

    def validate_data(self, public: dict, truth: dict) -> tuple[bool, list[str]]:
        """Validate already-parsed case.json/case_truth.json dicts (either schema)."""
        self.errors = []
        normalized = normalize_truth(truth)
        self._check_required_keys(public, normalized)
        # Later checks assume the keys they need are present -- skip them
        # if the case is already known to be unusable, so one missing key
        # doesn't cascade into a screen full of KeyError-shaped noise.
        if not self.errors:
            self._check_suspect_names(public, normalized)
            self._check_evidence_files(public, normalized)
            self._check_contradictions(public, normalized)
            self._check_knowledge_sheets(normalized)
            self._check_solvability(public, normalized)
            self._check_logical_consistency(public, normalized)
        return (len(self.errors) == 0), list(self.errors)

    # ---- loading ----------------------------------------------------------
    def _load_json(self) -> tuple[Optional[dict], Optional[dict]]:
        public = self._read_json(self.case_dir / "case.json")
        truth = self._read_json(self.case_dir / "case_truth.json")
        return public, truth

    def _read_json(self, path: Path) -> Optional[dict]:
        if not path.exists():
            self.errors.append(f"Missing file: {path}")
            return None
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            self.errors.append(f"{path.name}: invalid JSON ({exc})")
            return None

    def _read_evidence_text(self, filename: str) -> str:
        """Best-effort read of one evidence file's raw text; '' if missing/unreadable."""
        path = self.case_dir / EVIDENCE_SUBDIR / filename
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # ---- checks (operate on the NORMALIZED flat truth dict) --------------
    def _check_required_keys(self, public: dict, truth: dict) -> None:
        for key in ("briefing", "suspects", "evidence_files"):
            if key not in public:
                self.errors.append(f"case.json: missing required key '{key}'")
        for key in ("victim", "culprit", "motive", "method"):
            if truth.get(key) is None:
                self.errors.append(f"case_truth.json: missing required value for '{key}'")

    def _check_suspect_names(self, public: dict, truth: dict) -> None:
        """
        Every place a suspect name appears must exactly match one of the
        six fixed cast names -- this is the check that catches "Kabir"
        vs "Kabir Rana"-style mismatches.
        """
        for s in public.get("suspects", []):
            name = s.get("name")
            if name not in _CAST_NAMES:
                self.errors.append(f"case.json suspects: '{name}' is not one of the fixed cast.")

        culprit = truth.get("culprit")
        if culprit is not None and culprit not in _CAST_NAMES:
            self.errors.append(f"case_truth.json: culprit '{culprit}' is not one of the fixed cast.")

        for name in truth.get("character_knowledge", {}):
            if name not in _CAST_NAMES:
                self.errors.append(f"character_knowledge: '{name}' is not one of the fixed cast.")

        for c in truth.get("contradictions", []):
            suspect = c.get("suspect")
            if suspect not in _CAST_NAMES:
                self.errors.append(
                    f"contradiction '{c.get('id', '?')}': suspect '{suspect}' is not one of the fixed cast."
                )

        for entry in truth.get("true_timeline", []):
            actor = entry.get("actor")
            if actor is not None and actor not in _CAST_NAMES:
                self.errors.append(
                    f"true_timeline entry at {entry.get('time', '?')}: "
                    f"actor '{actor}' is not one of the fixed cast."
                )

    def _check_evidence_files(self, public: dict, truth: dict) -> None:
        evidence_dir = self.case_dir / EVIDENCE_SUBDIR
        manifest_names: set[str] = set()

        for item in public.get("evidence_files", []):
            filename = item.get("filename")
            if not filename:
                self.errors.append("case.json evidence_files: an entry is missing 'filename'.")
                continue
            manifest_names.add(filename)
            if not (evidence_dir / filename).exists():
                self.errors.append(
                    f"Evidence file listed in the manifest but missing on disk: "
                    f"{EVIDENCE_SUBDIR}/{filename}"
                )
            if "keywords" in item and not isinstance(item["keywords"], list):
                self.errors.append(f"case.json evidence_files['{filename}']['keywords'] must be a list.")

        for c in truth.get("contradictions", []):
            ev = c.get("evidence_file")
            if ev and ev not in manifest_names:
                self.errors.append(
                    f"contradiction '{c.get('id', '?')}' references '{ev}', which is not "
                    f"in case.json's evidence_files manifest."
                )

        for fname in truth.get("solution_conditions", {}).get("required_evidence_for_method", []):
            if fname not in manifest_names:
                self.errors.append(
                    f"solution_conditions.required_evidence_for_method references '{fname}', "
                    f"which is not in case.json's evidence_files manifest."
                )

        for fname in truth.get("supporting_evidence", []):
            if fname not in manifest_names:
                self.errors.append(
                    f"investigation_truth.supporting_evidence references '{fname}', which is "
                    f"not in case.json's evidence_files manifest."
                )

    def _check_contradictions(self, public: dict, truth: dict) -> None:
        seen_ids: set[str] = set()
        for c in truth.get("contradictions", []):
            cid = c.get("id")
            if not cid:
                self.errors.append("A contradiction entry is missing an 'id'.")
                continue
            if cid in seen_ids:
                self.errors.append(f"Duplicate contradiction id: '{cid}'")
            seen_ids.add(cid)
            for required_field in ("suspect", "claim", "evidence_file", "evidence_fact", "description"):
                if required_field not in c:
                    self.errors.append(f"contradiction '{cid}' is missing '{required_field}'.")

    def _check_knowledge_sheets(self, truth: dict) -> None:
        """Light type-safety check -- each present field must be a list of strings."""
        list_fields = ("known_facts", "beliefs", "secrets", "lies", "unknown_information")
        for name, sheet in truth.get("character_knowledge", {}).items():
            if not isinstance(sheet, dict):
                self.errors.append(f"character_knowledge['{name}'] must be an object.")
                continue
            for lf in list_fields:
                if lf in sheet and not isinstance(sheet[lf], list):
                    self.errors.append(f"character_knowledge['{name}']['{lf}'] must be a list.")

    def _check_solvability(self, public: dict, truth: dict) -> None:
        """
        Deterministic proxy for solvability: does the engine's own scoring
        logic (verdict.py) actually have something to reward? Checks that
        (a) at least one contradiction implicates the culprit, and (b) at
        least one required-method-evidence file is defined. This is NOT a
        general logical solver -- it's the minimum verdict.py relies on.
        """
        culprit = truth.get("culprit")
        contradictions = truth.get("contradictions", [])
        if culprit is not None and not any(c.get("suspect") == culprit for c in contradictions):
            self.errors.append(
                f"Solvability: no contradiction implicates the culprit ('{culprit}') -- "
                f"there is no evidence-backed way to pin them down."
            )

        required = truth.get("solution_conditions", {}).get("required_evidence_for_method", [])
        if not required:
            self.errors.append(
                "Solvability: solution_conditions.required_evidence_for_method is empty -- "
                "no evidence file is defined as proof of method."
            )

    def _check_logical_consistency(self, public: dict, truth: dict) -> None:
        """
        Stage 4.5: three checks that go beyond pure structure, catching
        cases where the pieces are all individually well-formed but don't
        actually agree with each other. Two of these are deliberately
        NARROWER than the driving spec's literal wording, for an honest
        reason spelled out at each one -- doing real free-text entity
        extraction (a "substance name", an "alibi location") from
        arbitrary generated prose isn't something deterministic Python
        can do reliably, so rather than fake it with regex that mostly
        won't fire correctly, each check below uses a genuinely reliable
        signal instead.

        1. Method/substance consistency. Rather than parsing prose to
           find "the substance," this uses an explicit, human- or
           Qwen-authored `actual_truth.method_keyword` -- a short string
           (e.g. "thiopental") that must appear (case-insensitive
           substring) in both the method description AND in every file
           listed under solution_conditions.required_evidence_for_method.
           Skipped entirely if method_keyword isn't set, since it's
           optional -- this check only fires when there's something
           concrete to check.

        2. Timeline/evidence coherence. Every true_timeline entry has a
           structured "time" field already (no extraction needed). This
           checks that AT LEAST ONE of those times appears as a substring
           somewhere in the raw text of a file listed in
           investigation_truth.supporting_evidence -- not ALL of them,
           deliberately: a culprit sneaking through a camera blind spot
           is realistically NOT going to show up in a security log at
           that exact time, and requiring every step to be evidenced
           would punish exactly the kind of case that has a believable
           "how they got away with it" thread. This only checks that the
           timeline isn't floating completely disconnected from the
           evidence -- there's at least one anchor point.

        3. Alibi/claim alignment. For each contradiction, checks that its
           "claim" text shares at least one distinctive word with that
           suspect's own alibi, public_statement, or character_knowledge
           lies (case.json + character_knowledge combined). This is a
           coarse sanity net, not semantic understanding -- it exists to
           catch a contradiction whose "false claim" is about something
           the suspect never actually said anywhere, which would mean
           the contradiction doesn't actually correspond to anything a
           player could discover through normal conversation.
        """
        self._check_method_consistency(truth)
        self._check_timeline_evidence_coherence(truth)
        self._check_alibi_claim_alignment(public, truth)

    def _check_method_consistency(self, truth: dict) -> None:
        keyword = truth.get("method_keyword")
        if not keyword:
            return
        keyword_lower = keyword.lower()

        method_text = truth.get("method") or ""
        if keyword_lower not in method_text.lower():
            self.errors.append(
                f"Logical consistency: method_keyword '{keyword}' does not appear in "
                f"case_truth's own method description -- they should refer to the same thing."
            )

        for fname in truth.get("solution_conditions", {}).get("required_evidence_for_method", []):
            text = self._read_evidence_text(fname)
            if text and keyword_lower not in text.lower():
                self.errors.append(
                    f"Logical consistency: method_keyword '{keyword}' does not appear anywhere "
                    f"in '{fname}', but that file is listed as proof of method."
                )

    def _check_timeline_evidence_coherence(self, truth: dict) -> None:
        timeline = truth.get("true_timeline", [])
        supporting = truth.get("supporting_evidence", [])
        if not timeline or not supporting:
            return  # nothing to cross-check

        combined_text = " ".join(self._read_evidence_text(f) for f in supporting)
        if not combined_text.strip():
            return  # evidence files unreadable -- _check_evidence_files already flags this

        times = [entry.get("time") for entry in timeline if entry.get("time")]
        if times and not any(t in combined_text for t in times):
            self.errors.append(
                "Logical consistency: none of true_timeline's event times appear anywhere in "
                "the supporting evidence files -- the timeline isn't anchored to any evidence "
                "at all. (Not every step needs a record -- e.g. a culprit avoiding cameras "
                "realistically leaves no trace for that specific moment -- but at least one "
                "timeline event should be corroborated by something the player can find.)"
            )

    def _check_alibi_claim_alignment(self, public: dict, truth: dict) -> None:
        suspects_by_name = {s["name"]: s for s in public.get("suspects", [])}
        knowledge = truth.get("character_knowledge", {})

        for c in truth.get("contradictions", []):
            suspect_name = c.get("suspect")
            claim = c.get("claim", "")
            claim_words = _significant_words(claim)
            if not claim_words:
                continue

            suspect_info = suspects_by_name.get(suspect_name, {})
            sheet = knowledge.get(suspect_name, {})
            own_text = " ".join([
                suspect_info.get("alibi", ""),
                suspect_info.get("public_statement", ""),
                " ".join(sheet.get("lies", [])),
            ])
            own_words = _significant_words(own_text)

            if own_words and not (claim_words & own_words):
                self.errors.append(
                    f"Logical consistency: contradiction '{c.get('id', '?')}''s claim doesn't "
                    f"share any distinctive wording with what {suspect_name} actually says "
                    f"(alibi/public_statement/lies) -- the contradiction may not correspond to "
                    f"anything a player could actually hear from this suspect."
                )
