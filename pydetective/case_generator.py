"""
case_generator.py  (Stage 4, updated for the Stage 4.5 nested schema)
------------------------------------------------------------------------
Procedural case generation via Qwen, run once (offline) per new case --
not during live play. Ravi and the suspects never see this module; it
only ever runs before a playthrough starts.

Pipeline:
    1. culprit = random.choice(characters.CAST) -- Python decides FIRST.
       The chosen name is then force-written into the generated
       case_truth.json regardless of what Qwen returns (into
       actual_truth.culprit if Qwen used the nested schema as asked, or
       the top-level culprit key as a defensive fallback if it didn't),
       so the model never actually gets to pick the culprit even if it
       ignores the instruction.
    2. Ask Qwen (via llm.ollama_chat) for one JSON object containing
       "case" (case.json's contents) and "case_truth" (case_truth.json's
       contents, in the Stage 4.5 nested actual_truth/investigation_truth/
       deception schema -- see case_validator.normalize_truth() for the
       exact shape), and "evidence_content" (filename -> raw text/CSV for
       every file case.evidence_files names).
    3. Parse the JSON (defensively stripping markdown fences some models
       add despite instructions not to).
    4. Write cases/CASE_PROCD_<id>/ (case.json, case_truth.json, and each
       evidence file under evidence/) BEFORE validating -- CaseValidator
       checks evidence files against the real evidence/ folder on disk,
       the same way it checks a hand-written case, so validation only
       makes sense once the files actually exist there.
    5. Validate with case_validator.CaseValidator.validate() -- the EXACT
       same schema/reference/solvability/logical-consistency checks a
       hand-written case gets (normalize_truth() makes this schema-
       version-agnostic). On failure, delete the partial folder, feed the
       specific error list back into the next attempt's prompt, and
       retry (up to MAX_ATTEMPTS times) -- so a model that gets something
       wrong (including a logical-consistency miss, e.g. a method_keyword
       that doesn't actually appear in its own evidence) is told exactly
       what to fix, not just asked to try again blind.

Nothing here is trusted blindly: every generated case goes through the
same gate CASE_001 would if it were hand-edited, and case.load() will
refuse to load anything that wouldn't pass CaseValidator on its own.
"""

import json
import random
import re
import shutil
import uuid
from pathlib import Path

import llm
from case_validator import CaseValidator, EVIDENCE_SUBDIR
from characters import CAST

QWEN_MODEL = "qwen3.6"  # must match a tag from `ollama list` exactly
GENERATION_TIMEOUT = 180  # generous -- a one-shot batch generation, not a quick chat turn
MAX_ATTEMPTS = 3


class CaseGenerationError(Exception):
    """Raised when Qwen fails to produce a valid case after MAX_ATTEMPTS tries."""


_SYSTEM_PROMPT = (
    "You generate murder-mystery case files for a detective game engine as a single, strict "
    "JSON object. Output ONLY that JSON object -- no markdown code fences, no commentary "
    "before or after it. Every string should be original content for this specific case; "
    "never leave placeholder text."
)

# A minimal but structurally complete example, embedded in every generation
# prompt so the model sees the exact shape expected -- not just a prose
# description of it. Content is illustrative only.
_SCHEMA_EXAMPLE = {
    "case": {
        "briefing": {
            "title": "string",
            "victim": "string, e.g. 'Jane Doe, 40, occupation'",
            "location": "string",
            "date": "string",
            "summary": "2-4 sentences, no culprit reveal",
            "incident": "1-2 sentences setting up the investigation",
        },
        "suspects": [
            {
                "name": "must be exactly one of the six fixed cast names",
                "relation_to_victim": "string",
                "alibi": "string -- what they publicly claim",
                "public_statement": "string, in quotes-worthy spoken style",
            }
        ],
        "evidence_files": [
            {
                "filename": "example.txt",
                "description": "one sentence, player-facing",
                "keywords": ["lowercase", "terms", "someone", "might", "say"],
            }
        ],
    },
    "case_truth": {
        "actual_truth": {
            "victim": "plain name, matches case.briefing.victim's name portion",
            "incident": "string",
            "culprit": "must be exactly one of the six fixed cast names",
            "motive": "string",
            "method": "string, should include a specific concrete detail (e.g. a substance name)",
            "method_keyword": "optional but recommended -- a short term (e.g. 'thiopental') that "
                               "appears in both `method` above and in every file listed under "
                               "solution_conditions.required_evidence_for_method below",
            "timeline": [
                {"time": "HH:MM", "event": "string", "actor": "optional, a fixed cast name"}
            ],
        },
        "investigation_truth": {
            "contradictions": [
                {
                    "id": "short_snake_case_id",
                    "suspect": "must be exactly one of the six fixed cast names, and should be "
                               "the culprit for at least one entry",
                    "claim": "a false claim this suspect actually makes -- should share real "
                             "wording with their alibi/public_statement/a listed lie, not be an "
                             "invented accusation",
                    "evidence_file": "must match a filename in case.evidence_files",
                    "evidence_fact": "the real fact from that evidence that contradicts the claim",
                    "description": "1-2 sentences explaining the contradiction",
                }
            ],
            "solution_conditions": {
                "required_evidence_for_method": ["filename(s) that prove HOW the crime was done"]
            },
            "supporting_evidence": [
                "filenames whose raw text should literally contain at least one of "
                "actual_truth.timeline's exact time values, e.g. '21:47'"
            ],
        },
        "deception": {
            "enabled": False,
            "planted_target": None,
            "planted_evidence": [],
            "false_narrative": None,
        },
        "character_knowledge": {
            "each of the six fixed cast names": {
                "known_facts": ["string", "..."],
                "beliefs": ["string", "..."],
                "secrets": ["string, can be empty list"],
                "lies": ["string, can be empty list"],
                "unknown_information": ["string", "..."],
            }
        },
    },
    "evidence_content": {
        "example.txt": "the FULL raw text or CSV content of this evidence file"
    },
}


def generate_case(model: str = QWEN_MODEL) -> str:
    """
    Generate, validate, and write a brand-new case. Returns the new
    case_id (cases/<case_id>/) on success. Raises CaseGenerationError if
    Qwen can't produce something that passes validation within
    MAX_ATTEMPTS tries -- callers should catch this (and llm.LLMError,
    for connection/timeout problems) rather than let it crash the menu.
    """
    from game import CASES_DIR  # local import: avoids a game.py <-> case_generator.py cycle

    culprit = random.choice(CAST).name
    case_id = f"CASE_PROCD_{uuid.uuid4().hex[:8].upper()}"
    case_dir = CASES_DIR / case_id

    last_errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        prompt = _build_generation_prompt(culprit, attempt, last_errors)
        raw = llm.ollama_chat(
            model=model, system_prompt=_SYSTEM_PROMPT, user_message=prompt,
            timeout=GENERATION_TIMEOUT,
        )

        try:
            payload = _parse_json(raw)
        except ValueError as exc:
            last_errors = [f"Could not parse Qwen's output as JSON: {exc}"]
            continue

        public, truth, evidence_content = _split_payload(payload)
        if public is None or truth is None:
            last_errors = ["Output is missing top-level 'case' and/or 'case_truth' objects."]
            continue

        # Culprit is Python's decision, not the model's -- enforced here
        # regardless of what Qwen actually wrote. Target whichever schema
        # shape Qwen actually used (nested is what we asked for; flat is
        # a defensive fallback if it didn't comply) -- normalize_truth()
        # downstream handles either.
        if "actual_truth" in truth:
            truth.setdefault("actual_truth", {})["culprit"] = culprit
        else:
            truth["culprit"] = culprit

        missing_content = [
            item["filename"] for item in public.get("evidence_files", [])
            if not evidence_content.get(item["filename"], "").strip()
        ]
        if missing_content:
            last_errors = [f"evidence_content is missing real text for: {', '.join(missing_content)}"]
            continue

        # Write BEFORE validating: CaseValidator checks evidence files
        # against the real evidence/ folder on disk (the same check a
        # hand-written case gets), so validation only makes sense once
        # the files actually exist there.
        _write_case(case_dir, public, truth, evidence_content)

        ok, errors = CaseValidator(case_dir, case_id).validate()
        if ok:
            return case_id
        last_errors = errors
        _cleanup_partial(case_dir)

    raise CaseGenerationError(
        f"Qwen failed to produce a valid case after {MAX_ATTEMPTS} attempts. Last errors:\n"
        + "\n".join(f"  - {e}" for e in last_errors)
    )


def _build_generation_prompt(culprit: str, attempt: int, last_errors: list[str]) -> str:
    cast_names = ", ".join(s.name for s in CAST)
    lines = [
        "Generate a complete murder-mystery case for a detective game.",
        f"The fixed cast is EXACTLY these six suspects, every time, spelled exactly as given: "
        f"{cast_names}.",
        f'CULPRIT = "{culprit}". This suspect committed the crime. Do not change this, and do '
        "not have any narration imply a different culprit.",
        "",
        "Return ONLY a single JSON object, no markdown fences, no commentary before or after, "
        "matching exactly this structure (values below are placeholders describing what "
        "belongs there, not real content -- replace every one of them):",
        json.dumps(_SCHEMA_EXAMPLE, indent=2),
        "",
        "Hard requirements:",
        "- All six suspects must appear, by exactly matching name, in case.suspects and in "
        "case_truth.character_knowledge (one entry each).",
        "- case_truth.investigation_truth.contradictions must include at least one entry where "
        f'"suspect" is exactly "{culprit}".',
        "- Every contradiction's \"evidence_file\" must exactly match a filename that also "
        "appears in case.evidence_files AND in evidence_content.",
        "- case_truth.investigation_truth.solution_conditions.required_evidence_for_method must "
        "list at least one real filename from case.evidence_files.",
        "- Include at least 4 evidence files: one establishing motive, one establishing "
        "opportunity/timeline (e.g. a badge or security log), one establishing method (e.g. an "
        "inventory log or similar physical trail), and one red herring evidence file that "
        "suggests a different suspect but whose alibi actually holds up.",
        "- evidence_content must contain the FULL, detailed raw text (or CSV rows, comma-"
        "separated with a header row) for every filename in case.evidence_files -- these are "
        "read directly by the player, so make them detailed and internally consistent with "
        "case_truth, not vague summaries.",
        "- Give every suspect at least one known_facts entry and at least one belief; secrets "
        "and lies may be empty lists for suspects with nothing to hide, but the culprit should "
        "have both a secret and a lie tied directly to the contradiction(s) above.",
        "- If you set actual_truth.method_keyword, it must appear (case-insensitive) in both "
        "actual_truth.method's own text AND in the full evidence_content of every file listed "
        "under solution_conditions.required_evidence_for_method.",
        "- List in investigation_truth.supporting_evidence the evidence files whose text "
        "corroborates the timeline -- at least one of actual_truth.timeline's exact 'time' "
        "values (e.g. '21:47') should appear verbatim in the combined text of those files.",
        "- Each contradiction's \"claim\" should reuse real wording from that suspect's own "
        "alibi, public_statement, or a listed lie -- it should be something a player could "
        "actually hear that suspect say, not an accusation invented from nowhere.",
        "- Leave deception.enabled as false -- that section is reserved for a future game mode "
        "and isn't used yet.",
    ]
    if last_errors:
        lines.append("")
        lines.append(f"Your previous attempt (#{attempt - 1}) FAILED validation with these exact "
                      "problems -- fix every one of them this time:")
        lines.extend(f"  - {e}" for e in last_errors)
    return "\n".join(lines)


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc


def _split_payload(payload: dict):
    public = payload.get("case")
    truth = payload.get("case_truth")
    evidence_content = payload.get("evidence_content", {})
    if not isinstance(public, dict) or not isinstance(truth, dict):
        return None, None, {}
    if not isinstance(evidence_content, dict):
        evidence_content = {}
    return public, truth, evidence_content


def _write_case(case_dir: Path, public: dict, truth: dict, evidence_content: dict) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / EVIDENCE_SUBDIR).mkdir(exist_ok=True)

    with (case_dir / "case.json").open("w", encoding="utf-8") as f:
        json.dump(public, f, indent=2)
    with (case_dir / "case_truth.json").open("w", encoding="utf-8") as f:
        json.dump(truth, f, indent=2)

    for item in public.get("evidence_files", []):
        filename = item["filename"]
        content = evidence_content.get(filename, "")
        (case_dir / EVIDENCE_SUBDIR / filename).write_text(content, encoding="utf-8")


def _cleanup_partial(case_dir: Path) -> None:
    if case_dir.exists():
        shutil.rmtree(case_dir)
