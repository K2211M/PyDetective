# PyDetective

A CLI-based procedural murder-mystery engine in pure Python. Python owns
all game state (the "state author"); local LLMs via Ollama only ever act
as dynamic actors (Llama, for suspect interrogation and the detective
partner) and offline case generators (Qwen), never as the source of
truth.

## Status: Stage 4.5 of 4 -- hardening pass after the "planned" roadmap

| Stage | Contents | Status |
|---|---|---|
| 1 | Directory structure, mock case, core CLI menu | **Done** |
| 2 | Live suspect interrogation via Ollama/Llama | **Done** |
| 2.5 | Ground-truth hardening: `Case` object, bounded knowledge, contradictions, accusation & scoring | **Done** |
| 3 | Case validation engine, richer knowledge schema, proactive contradiction suggestions | **Done**, later revised |
| 3.5 | Bluff engine, evidence-backed confrontation, structured events, hallucination heuristic | **Done** |
| 4 | Pure-detective pivot, Detective Partner "Ravi", Interview Archive, real Qwen case generation | **Done** |
| 4.5 | Ravi grounding fix, deception-ready schema, logical-consistency validation, `--validate-all` | **Done** |

Stage 4 finished the original roadmap; 4.5 is a hardening pass that fixed
a real bug (Ravi had no game-state context at all) and prepared the
schema for a future framing/deception mechanic without building that
mechanic's gameplay yet. CASE_001 is still the only hand-tuned case,
procedural generation is only as good as the Qwen model actually pulled,
and there's plenty of room for further polish if you want to keep going.

## What changed in Stage 4

This stage was a genuine design pivot, not just an addition:

- **"Pure detective" philosophy.** The engine no longer auto-flags
  contradictions. Stage 3's proactive "[!] Potential Contradiction
  Detected" popups and the old manual "Flag a Contradiction" menu are
  both gone. `case.Case.contradictions` still exists and is still just
  as deterministic -- it's now evaluated only when the player brings a
  specific hypothesis to their Detective Partner.
- **Detective Partner "Ravi"** (`partner.py`) -- sarcastic, makes you
  work for it. State a hypothesis and Python checks it against the real
  contradictions; ask for a hint and pay an escalating score penalty for
  it. See "The Detective Partner" below.
- **Interview Archive** (`interrogation.show_archive`) -- a read-only
  transcript viewer built on the structured `InterrogationEvent` log
  Stage 3.5 already kept.
- **Real procedural generation** (`case_generator.py`) -- no longer a
  stub. Generates, validates, and writes a brand-new case via Qwen, with
  a validation-driven retry loop.
- **Model updates**: `interrogation.py` and `partner.py` both default to
  `llama3.2:3b`; `case_generator.py` defaults to `qwen3.6`. (`qwen3.6`
  is what was requested when this stage was built -- verify it's a real
  tag on your Ollama install with `ollama list` before relying on it;
  these are one-line constants either way.)

One deliberate deviation, flagged for visibility: the driving spec's
proposed menu dropped **Suspect Dossier**. It's kept here -- removing it
looked like an unrelated usability cut, not something the "pure
detective" framing specifically called for. Say the word if you'd rather
it go.

## What changed in Stage 4.5

Playtesting surfaced a real architectural gap: Ravi had **no game-state
context at all** beyond conversation history and that turn's directive
(`VALID_CONTRADICTION`/hint content). He had nothing to ground small talk
in, so he'd invent victims, events, and mechanics wholesale. This stage
fixed that and used the same pass to prepare the schema for a future
deception mechanic:

- **`partner.build_partner_context(state)`** -- assembled once per
  `consult_partner()` session and prepended to `RAVI_PERSONA` as that
  session's system prompt: the public case briefing, the public suspect
  roster, the **full text** of every evidence file the player has
  already discovered (via the new non-mutating `EvidenceRegistry.peek()`
  -- `read()` now delegates to it), and a plain investigation-progress
  summary. Deliberately excludes `case.Case`'s verdict-only accessors
  (`culprit_name()`, `motive()`, `method()`, `true_timeline()`,
  `deception_info()`) -- Ravi stays exactly as blind to the solution as
  the player.
- **`RAVI_PERSONA` grounding rules** -- explicit instructions that only
  the case context, discovered evidence, suspect roster, and a validated
  Python directive are ever factual, and that Ravi should push back in
  character on any premise the player states that isn't backed by them.
- **`brief` / `recap` / `catch me up`** -- a Python-generated, factual
  investigation-progress summary relayed through Ravi's voice, same
  Python-decides-facts/Llama-decides-voice split as everything else here.
- **Nested `case_truth.json` schema** (`actual_truth` /
  `investigation_truth` / `deception`) -- see "The new schema" below.
  `case.py`'s public API (`culprit_name()`, `motive()`, etc.) didn't
  change at all; only `case_validator.normalize_truth()` and `case.load()`
  needed to know about the new shape, which is exactly the payoff of
  having narrow accessors instead of a raw dict in the first place.
- **Three new logical-consistency checks** in `CaseValidator` -- method/
  substance consistency, timeline/evidence coherence, and alibi/claim
  alignment. All three are deliberately narrower than a literal "verify
  chemical names match" or "verify alibi locations correspond to
  evidence" reading, for an honest reason: real free-text entity
  extraction from arbitrary generated prose isn't something deterministic
  Python can do reliably. See "How validation works" below for exactly
  what each one checks instead, and why.
- **`python3 main.py --validate-all`** -- batch-validates every case
  under `cases/` and exits non-zero if any fail.

`CASE_001` was migrated to the new nested schema as part of this pass
(with `method_keyword` and `supporting_evidence` populated) so the new
checks are actually exercised, not just present but dormant.

## Run it

Requires only the standard library -- no pip installs needed anywhere in
the project (the Ollama wrapper in `llm.py` uses `urllib`, not `requests`).

```bash
python3 main.py

# Batch-validate every case on disk without starting the game loop:
python3 main.py --validate-all
```

You need a local [Ollama](https://ollama.com) install for interrogation,
Ravi, and case generation:

```bash
ollama serve                 # if not already running
ollama pull llama3.2:3b      # interrogation + Ravi
ollama pull qwen3.6          # case generation (verify this tag exists for you)
```

If Ollama isn't reachable, everything except live conversation still
works fine -- you'll get a one-line heads-up at startup, and a friendly
in-context message if you open an interrogation or Ravi consultation
anyway.

At startup you'll always see a case picker, even with only `CASE_001` on
disk, since generating a new one is always an option:

```
Available cases:
  [1] CASE_001
  [G] Generate a new case with Qwen (qwen3.6)
```

Generating writes a new `cases/CASE_PROCD_<id>/` folder and plays exactly
like a hand-written one -- nothing downstream cares how a case was made.

From the main menu:

- **[1] Case Briefing** -- victim, location, and the setup.
- **[2] Suspect Dossier** -- the six fixed suspects, their relation to the
  victim, alibi, and public statement. (Kept despite the driving spec's
  leaner proposed menu -- see above.)
- **[3] Evidence Browser** -- read the raw evidence files under
  `cases/<case_id>/evidence/`. `.csv` files render as aligned tables.
  Nothing here is flagged or annotated for you -- what you make of it is
  up to you and, if you want a nudge, Ravi.
- **[4] Interrogate a Suspect** -- pick one of the six, ask free-form
  questions, or type `confront` to present a specific piece of examined
  evidence directly. Type `back` to end the interview.
- **[5] Interview Archive** -- review the full structured transcript of
  any suspect you've already interviewed.
- **[6] Consult Detective Partner (Ravi)** -- state a hypothesis or ask
  for a hint. See "The Detective Partner" below.
- **[7] Make Your Accusation** -- pick the culprit, submit the evidence
  you think proves the method, and get a full post-mortem with a
  Detective Score. This is final: re-selecting it afterward just
  redisplays your verdict rather than letting you change your answer.
- **[8] Quit**

### Configuring models

Each model is one constant near the top of its module:

```python
# interrogation.py
LLAMA_MODEL = "llama3.2:3b"
# partner.py
PARTNER_MODEL = "llama3.2:3b"
# case_generator.py
QWEN_MODEL = "qwen3.6"
```

Change any of them to whatever you've actually pulled -- `ollama list` to
check; tags matter (`llama3.2:3b` vs `llama3.2:1b` are different tags).

## Project layout

```
main.py              Menu loop / CLI entry point, case picker (incl. generation)
game.py              GameState -- wraps one Case + session counters + Ravi state
case.py              Case domain object: public data + hidden truth, narrow accessors
case_validator.py    Deterministic schema/reference/solvability/logical-consistency checks + schema normalization
evidence.py          Evidence manifest, file reading + non-mutating peek(), CSV rendering
characters.py        Fixed suspects, CharacterState, CharacterKnowledge, InterrogationEvent, prompt builder
llm.py               Ollama API wrapper (chat + safe_chat + model availability check)
interrogation.py     Live suspect Q&A loop, bluff engine, confrontation, Interview Archive
forensics.py         Bluff-engine keyword matching + hallucination heuristic (no contradiction UI anymore)
partner.py           Detective Partner "Ravi" -- grounded context, hypothesis validation, hint economy, recap
verdict.py           Accusation screen + Detective Score (incl. hint penalty)
case_generator.py    Procedural case generation via Qwen (nested schema), with validation-driven retry
cases/
  CASE_001/
    case.json               Public briefing, suspects, evidence manifest (with per-file "keywords")
    case_truth.json         Hidden, nested schema: actual_truth / investigation_truth /
                             deception (unused, schema-ready) / character_knowledge
    evidence/
      security_log.txt        Opportunity -- keycard log
      access_record.csv       Server access log (secondary motive trail)
      email_01.txt            Motive -- email thread re: falsified data
      toxicology_report.txt   Physical evidence -- cause/time of death, sedative trace
      lab_inventory_log.csv   Method -- ties culprit's staff ID to the missing vial
      maya_overtime_note.txt  Red herring -- apparent motive, validated alibi
  CASE_PROCD_<id>/    Same shape, written by case_generator.py
```

## The fixed cast

Every case features the same six suspects; only their alibi, relation to
the victim, knowledge sheet, and guilt change per case.

- **Dr. Arjun Mehta** -- Calm / Analytical
- **Maya Kapoor** -- Nervous / Defensive
- **Kabir Rana** -- Sarcastic / Confident
- **Rhea Singh** -- Friendly / Observant
- **Vikram Oberoi** -- Aggressive / Impatient
- **Nisha Verma** -- Quiet / Evasive

## How grounding works

`characters.build_system_prompt()` builds each suspect's Llama system
prompt from their static personality, their case-specific alibi/
statement, and their bounded `CharacterKnowledge` in five categories the
model is told to treat differently:

- **`known_facts`** -- stated plainly if asked
- **`beliefs`** -- shareable, but only framed as opinion/guess, never as
  confirmed fact
- **`secrets`** -- deflected on, never admitted
- **`lies`** -- asserted confidently as if true, if asked directly
- **`unknown_information`** -- met with "I don't know" rather than
  improvisation

Plus a plain `is_culprit` bool (never the culprit's *name*), and explicit
permission to improvise small non-case-critical flavor so suspects don't
read as robotic fact machines. Motive, method, and the true timeline are
never passed to `build_system_prompt()` at all -- structurally absent
from its inputs, not filtered out.

`llm.py` is stdlib-only (`urllib`) and raises typed exceptions instead of
failing silently; `llm.safe_chat()` wraps those into a shared
error-handled call used by both `interrogation.py` and `partner.py`, so
a flaky or offline Ollama server can never crash the game loop.
`ollama_chat()`'s `extra_context` parameter carries per-turn directives
(the bluff engine's `EVIDENCE_BACKED`, Ravi's `VALID_CONTRADICTION`)
without rebuilding the whole session's base prompt on every message.

## Bluff engine & confrontation

A player could otherwise claim non-existent evidence ("I have camera
footage of you") and an eager model might play along. Two deterministic
mechanisms close that gap -- neither ever asks the model to judge its
own bluff:

- **`forensics.assess_claim(state, question)`** scans the player's own
  question (never the model's replies) for evidence keywords
  (`case.json`'s per-file `"keywords"`) or generic confrontation words,
  then checks whether the player has actually examined matching
  evidence. The verdict becomes a per-turn `EVIDENCE_BACKED: True/False`
  directive -- unbacked claims get evasion and skepticism; backed ones
  let the suspect react more realistically; ordinary questions get no
  directive at all.
- **`interrogation.confront_suspect()`** (type `confront` mid-interview)
  is the formal version: the player can only pick evidence they've
  already discovered, so `EVIDENCE_BACKED: True` is *guaranteed*. If the
  evidence matches one of that suspect's predefined contradictions, the
  directive names the specific contradicted claim and allows the model
  to abandon *that* claim -- still never a full confession. Both the
  culprit's silence under confrontation and the "never confess" rule
  under Ravi's questioning below are deliberate: `verdict.py`'s scoring
  only stays meaningful if the accusation screen can't be shortcut into
  a free confession.

Every interrogation turn is logged as a structured
`characters.InterrogationEvent` (`turn_id`, `speaker`, `event_type` --
`QUESTION`/`CLAIM`/`CONFRONTATION`/`REACTION`, `content`,
`evidence_backed`, `flagged_as_hallucination`) alongside the raw
API-shaped `CharacterState.history` every Ollama call needs verbatim.
The Interview Archive (menu `[5]`) reads straight from this log.

**On hallucination-guarding specifically:** an earlier spec asked for
stripping model responses that invent facts outside a suspect's
knowledge sheet. That's not implemented as literal text editing --
silently mutating an LLM's dialogue tends to produce broken, truncated-
looking sentences, and reliably detecting "an invented physical object"
in free text isn't something deterministic Python can do well. Instead,
`forensics.flag_possible_hallucination()` is a bounded heuristic: it
flags (never edits) a suspect's answer if it shares 2+ distinctive words
with *another* suspect's `secrets`/`lies` that aren't also in the
speaker's own sheet. It will under-flag far more than it over-flags --
paraphrased LLM text rarely echoes source strings closely -- and it's
shown to the player as a labeled `[heuristic note: ...]` hint, never a
verdict.

## The Detective Partner

Ravi (`partner.py`) is the only way a contradiction gets logged as found
-- consistent with "the engine never auto-flags inconsistencies," a
player can always ask their partner about a specific hunch. Two things
happen on every consultation turn, strictly separated:

- **Python decides what's true.** `_match_hypothesis()` checks the
  player's free-text hypothesis against `case.Case.contradictions`,
  requiring both a mention of the suspect's name and a mention of that
  contradiction's evidence file (or one of its `case.json` keywords) --
  reusing the same data the bluff engine uses, for a second purpose.
  `_hint_text()` computes tiered hint content directly from
  `Contradiction` fields.
- **Llama decides how it sounds.** Python's verdict becomes a
  `VALID_CONTRADICTION: True/False` or hint-tier directive sent via
  `extra_context`; Ravi relays it in character.

**Grounding (Stage 4.5):** Ravi used to receive nothing but that per-turn
directive and conversation history -- no case facts at all -- which is
exactly the gap that let an eager model invent things. Now
`partner.build_partner_context(state)` builds a grounding block once per
`consult_partner()` session (public briefing, public suspect roster, the
**full text** of every evidence file the player has already discovered
via `EvidenceRegistry.peek()`, and investigation progress) and prepends
it to `RAVI_PERSONA` as that session's system prompt. `RAVI_PERSONA`
itself now states explicitly that only that context, the evidence, the
roster, and a validated directive are ever factual -- and instructs Ravi
to push back in character on any premise the player states that isn't
backed by them. He still never receives motive, method, the true
timeline, or `deception_info()` -- exactly as blind to the actual
solution as the player, just no longer blind to the case *itself*.
`brief` / `recap` / `catch me up` trigger a short, Python-generated
investigation-progress summary relayed the same way.

**Hint economy** -- three tiers per contradiction (`partner.TIER_PENALTIES`),
using the *highest* tier reached per contradiction, not summed across
tiers reached along the way:

| Tier | Cost | Reveals |
|---|---|---|
| 0 | Free | A generic, non-specific nudge -- same for every contradiction |
| 1 | -5 pts | Names the suspect and the evidence file to compare, not the fact |
| 2 | -15 pts | Full reveal (the contradiction's claim + fact, verbatim) -- auto-logs it as found |

A correct, un-hinted hypothesis costs nothing at all -- hints exist for
when you're stuck, not as the primary way to solve the case. The penalty
is subtracted from the sum of `verdict.py`'s four scoring buckets (see
below), floored at 0.

## The new schema (`case_truth.json`)

Stage 4.5 introduced a nested, "deception-ready" schema for
`case_truth.json`, grouping fields by what they're *for* rather than
leaving them all flat:

```
{
  "actual_truth": {
    "victim": "...", "incident": "...", "culprit": "...",
    "motive": "...", "method": "...", "method_keyword": "...",
    "timeline": [ {"time": "...", "event": "...", "actor": "..."} ]
  },
  "investigation_truth": {
    "contradictions": [ ... ],
    "solution_conditions": {"required_evidence_for_method": [...]},
    "supporting_evidence": [ "..." ]
  },
  "deception": {
    "enabled": false, "planted_target": null,
    "planted_evidence": [], "false_narrative": null
  },
  "character_knowledge": { "<suspect name>": { ... } }
}
```

`character_knowledge` stays a top-level sibling in both schema versions
-- it's per-suspect data, not "what happened" or "how the investigation
proves it," so it didn't fit cleanly into either new group.
`case_validator.normalize_truth()` collapses either the old flat schema
or this nested one into the same internal shape, so `case.py` and every
check in `CaseValidator` are schema-version-agnostic and didn't need to
change. **`deception` is schema-ready, not feature-ready**: nothing in
this codebase currently reads or acts on it -- it's a placeholder for a
future framing/red-herring mechanic, always `enabled: false` for now.

`case.Case`'s public API is completely unaffected by any of this --
`culprit_name()`, `motive()`, `method()`, `true_timeline()`, etc. kept
their exact signatures throughout the migration, plus two new narrow
accessors: `supporting_evidence()` and `method_keyword()` (both used by
the new logical-consistency checks below), and `deception_info()`
(reserved, unused).

## How validation works

`case_validator.CaseValidator` runs automatically inside `case.load()`,
before any `Case` object is built, and again (from disk) at the end of
`case_generator.generate_case()`'s pipeline, and via `--validate-all`. It
aggregates every problem into one `(is_valid, errors)` result rather than
raising on the first issue. Checks include: required top-level values;
every suspect-name reference (suspects, culprit, `character_knowledge`
keys, `contradictions[].suspect`, `true_timeline[].actor`) matching one
of the six fixed-cast names exactly; every evidence filename in the
manifest existing on disk under `evidence/`, and every filename
referenced by a contradiction, `solution_conditions`, or
`supporting_evidence` appearing in that manifest; no duplicate
contradiction ids; knowledge-sheet fields being lists where present; and
a **solvability proxy** (not a general logical solver) -- at least one
contradiction implicates the true culprit, and at least one
required-method-evidence filename is defined.

**Logical-consistency checks (Stage 4.5)** go a step further, catching
cases where the pieces are individually well-formed but don't agree with
each other. All three were deliberately scoped narrower than a literal
reading of "verify chemical names match" or "verify alibi locations
correspond to evidence" -- real free-text entity extraction from
arbitrary generated prose isn't something deterministic Python can do
reliably, so each one uses a genuinely reliable signal instead of faking
NLP with regex that mostly wouldn't fire correctly:

1. **Method/substance consistency** -- rather than parsing prose to find
   "the substance," this uses an explicit, human- or Qwen-authored
   `actual_truth.method_keyword` (e.g. `"thiopental"`) that must appear,
   case-insensitively, in both the method description *and* every file
   listed under `solution_conditions.required_evidence_for_method`.
   Skipped entirely if `method_keyword` isn't set, since it's optional.
2. **Timeline/evidence coherence** -- every `timeline` entry already has
   a structured `time` field, no extraction needed. Checks that **at
   least one** of those times appears as a substring somewhere in the
   combined raw text of `investigation_truth.supporting_evidence` --
   deliberately not *all* of them: a culprit sneaking through a camera
   blind spot realistically leaves no log entry for that exact moment,
   and requiring every step to be evidenced would punish exactly the
   kind of case that has a believable "how they got away with it"
   thread. This only checks the timeline isn't floating completely
   disconnected from the evidence.
3. **Alibi/claim alignment** -- for each contradiction, checks that its
   `claim` text shares at least one distinctive word with that suspect's
   own alibi, public statement, or a listed lie. A coarse sanity net, not
   semantic understanding -- it exists to catch a contradiction whose
   "false claim" is about something the suspect never actually says
   anywhere, which would mean a player could never actually discover it
   through normal conversation.

## How procedural generation works

`case_generator.generate_case()`:

1. Picks the culprit via `random.choice(characters.CAST)` -- **before**
   calling Qwen, and force-writes that name into the generated
   `case_truth.json` regardless of what Qwen actually returns (targeting
   `actual_truth.culprit` if Qwen used the nested schema as asked, or the
   flat top-level `culprit` key as a defensive fallback if it didn't), so
   the model never really gets to pick someone else even if it ignores
   the instruction.
2. Asks Qwen for one JSON object (`case`, `case_truth`,
   `evidence_content`) matching an embedded schema example -- the exact
   nested shape `case.py` and `case_validator.py` expect, including all
   five `character_knowledge` categories, `contradictions`,
   `solution_conditions`, `method_keyword`, and `supporting_evidence`.
3. Parses the response (defensively stripping markdown fences some
   models add despite instructions not to).
4. Writes the case to `cases/CASE_PROCD_<id>/` -- **before** validating,
   since `CaseValidator` checks evidence files against the real
   `evidence/` folder on disk, the same way it checks a hand-written
   case.
5. Validates with the exact same `CaseValidator` a hand-written case
   gets -- schema, references, solvability, *and* the Stage 4.5
   logical-consistency checks. On failure, deletes the partial folder,
   feeds the specific error list back into the next attempt's prompt,
   and retries (up to `MAX_ATTEMPTS`, default 3) -- so a model that gets
   something wrong (including a consistency miss, e.g. a
   `method_keyword` that doesn't actually appear in its own evidence) is
   told exactly what to fix, not just asked to try again blind.

Tested with a mocked Ollama server exercising: a malformed-JSON first
attempt correctly triggering a retry, markdown-fence stripping, the
write-then-validate ordering, and the generated case loading and playing
correctly afterward (including CSV evidence rendering) through the
normal `game.load_case()` path -- no special-casing anywhere else in the
codebase for a generated case versus a hand-written one.


## How scoring works

`verdict.py`'s Detective Score starts at 100 points:

| Component | Points | How it's scaled |
|---|---|---|
| Correct culprit | 50 | all-or-nothing |
| Evidence discovered | 20 | `20 × (examined / total evidence files)` |
| Contradictions uncovered | 20 | `20 × (found / total contradictions)` |
| Efficiency (questions asked) | 10 | full credit at ≤12 questions, 0 at ≥30, linear between |

...minus the **hint penalty** described above. The "primary evidence"
submission at the accusation screen (does it actually link the accused
to the method?) is reported as a correct/incorrect line in the
post-mortem rather than its own point bucket -- the weights above
already sum to 100 without it. `score_verdict()` is a pure function (no
printing) if you want to change either of these or reuse it elsewhere.
