# LLM Collaboration Framework

A structured debate orchestrator that drives two LLM CLIs — **CLAUDE** (Claude Code) and **CODEX** (OpenAI Codex) — to collaboratively design and implement software projects through adversarial peer review. A human operator governs all state transitions and final decisions.

---

## Charter

This framework exists to answer a practical question: can two LLMs, given a shared problem and adversarial incentives, produce better technical decisions than either would alone?

The system enforces structured disagreement as a first-class mechanism. LLMs are not permitted to agree, affirm, or defer — they must propose, score, criticize, and synthesize under explicit anti-sycophancy rules. The human operator is not a tiebreaker of last resort; they are a required governance gate at every round boundary.

### Principles

- **Adversarial by design.** Agreement phrases are forbidden. Every scoring entry must include mandatory weaknesses. Scores are committed before reasoning to prevent post-hoc rationalization.
- **Auditable by default.** Every LLM output, human decision, score, and state transition is committed to git. Nothing is overwritten; everything is appended.
- **Human authority is non-negotiable.** No round advances, no decision is accepted, and no deadlock resolves without explicit human command. The orchestrator cannot self-approve.
- **File-based and dependency-light.** The orchestrator is a single Python file with no external runtime dependencies. State lives in JSON and markdown. The full collaboration history is a git repository.

---

## Scope

### In scope

- Multi-round structured debate between two LLM CLIs on a shared codebase
- Anti-sycophancy enforcement (forbidden phrases, mandatory weaknesses, score-first discipline)
- Weighted scoring rubric with human-configurable dimensions
- Deadlock detection, plea generation, and structured compromise protocol
- Human approval gates at every round boundary
- Git-backed audit trail of all LLM outputs and human decisions
- Dry-run mode for testing without real CLI calls
- Observability via append-only metrics log
- Full SDLC pipeline: CLAUDE authors spec → CODEX builds → CLAUDE reviews → CLAUDE writes tests → automated validation

### Out of scope

- Autonomous operation without human oversight
- Real-time streaming or UI — this is a CLI-driven, file-based system
- Support for more than two debating agents in the core loop (multi-agent role specialization is a future extension, not a current feature)
- Cloud deployment or networked coordination between orchestrator instances
- Any form of automatic decision acceptance

---

## State Machine

### Debate Loop

```
IDLE
  |
  | (round opened, title assigned)
  v
DEBATED <------------------------------------------+
  |                                                 |
  | (both LLMs write proposals)                     |
  v                                                 |
PROPOSED                                            |
  |                                                 |
  | (both LLMs score opposing proposal)             |
  v                                                 |
SCORED                                              |
  |                                                 |
  | (losing LLM synthesizes merged proposal)        |
  v                                                 |
SYNTHESIZED                                         |
  |                    |                            |
  | (max iters ok)     | (max iters exceeded)       |
  v                    v                            |
HUMAN_REVIEW       DEADLOCKED                       |
  |                    |                            |
  |                    | (plea + compromise form)   |
  |                    v                            |
  |               HUMAN_REVIEW                      |
  |                    |                            |
  +--------------------+                            |
  |                                                 |
  | ACCEPT / OVERRIDE                               |
  v                                                 |
AGREED                              REJECT ---------+
  |
  | BEGIN_BUILD (triggers SDLC pipeline)
  v
  (see SDLC Pipeline below)
```

### SDLC Pipeline

Triggered from `AGREED` by the human issuing `BEGIN_BUILD`.

```
AGREED
  |
  | BEGIN_BUILD
  v
SPEC   ← CLAUDE writes implementation spec to collaboration/spec.md
  |
  v
SPEC_READY  ← [human gate: review spec, issue BEGIN_BUILD to proceed]
  |
  v
BUILD  ← CODEX (--full-auto) implements spec, writes files under src/
  |
  v
CODE_REVIEW  ← CLAUDE reviews code against spec
  |                    |
  | APPROVED           | REWORK REQUIRED (up to max_build_iterations)
  v                    v
CODE_REVIEWED       BUILD (next iteration)
  |
  v
QA_WRITE  ← CLAUDE writes pytest test suite to tests/
  |
  v
TEST  ← validation.command runs
  |                    |
  | exit 0             | exit non-zero
  v                    v
VALIDATED           QA_FAILED ← loops back to BUILD (or human review at max)
  |
  | [human gate]
  v
IDLE (next round)
```

### Human commands

| Command             | Effect                                                        |
|---------------------|---------------------------------------------------------------|
| `ACCEPT`            | Accept current proposal; advance to AGREED                    |
| `REJECT`            | Reject; round resets to DEBATED, iteration 1                  |
| `EXTEND +N`         | Grant N additional iterations before DEADLOCK                 |
| `OVERRIDE CLAUDE`   | Force-accept CLAUDE's proposal                                |
| `OVERRIDE CODEX`    | Force-accept CODEX's proposal                                 |
| `BEGIN_BUILD`       | From AGREED or SPEC_READY: start/continue the SDLC pipeline   |
| `IMPLEMENT`         | Legacy: CLAUDE generates implementation artifacts directly     |
| `APPROVE_NEXT_ROUND`| Advance from AGREED/VALIDATED to next round (IDLE)            |
| `QUIT`              | Exit the orchestrator                                         |

---

## File Structure

```
llm-collab-framework/
├── orchestrator.py                      # Single-file orchestrator
├── config.json                          # All tunable parameters
├── seed.md                              # Project goal (immutable after --init)
├── state.json                           # Current FSM state
├── state.lock                           # Write lock (deleted when not held)
├── metrics.jsonl                        # Append-only per-turn instrumentation
│
├── src/                                 # Implementation files (written by CODEX during BUILD)
├── tests/                               # Test suite (written by CLAUDE during QA_WRITE)
│
├── calibration/
│   └── scoring_examples.md              # Anchor examples injected into scoring prompts
│
└── collaboration/
    ├── index.yaml                        # Lightweight round index for context retrieval
    ├── spec.md                           # Implementation spec (written by CLAUDE during SPEC)
    ├── rounds/
    │   └── r{N}_{title}.md              # Full debate transcript per round
    ├── validations/
    │   ├── r{N}_result.md               # TEST phase output (stdout/stderr + exit code)
    │   ├── r{N}_build_{iter}.md         # CODEX build log per iteration
    │   ├── r{N}_code_review.md          # CLAUDE code review output
    │   └── r{N}_qa_written.md           # QA_WRITE phase log
    ├── pleas/
    │   └── r{N}_plea_{LLM}.md           # DEADLOCK plea files
    ├── compromise/
    │   └── r{N}_compromise_template.md  # Structured deadlock resolution form
    └── decisions/
        ├── accepted.md                  # Summaries of all accepted decisions
        └── rejected.md                  # Summaries of all rejected decisions
```

---

## Entry Format

Every LLM output in a round file begins with a YAML header:

```yaml
---
FROM: CLAUDE
ROUND: r02
ITER: 2
STATE: PROPOSED | SCORED | SYNTHESIZED | PLEA
TS: 2026-03-19T04:15:23Z
---
```

### PROPOSED entry structure

```markdown
## Approach
[Technical proposal — data structures, interfaces, algorithms]

## Complexity
[Time/space analysis]

## Trade-offs
[What is sacrificed; under what conditions this breaks down]

## Failure Modes
[Concrete failure scenarios with conditions]
```

### SCORED entry structure

```markdown
## Scores

| Dimension       | Weight | Score | Weighted |
|-----------------|--------|-------|---------|
| goal_alignment  | 0.25   | 4     | 1.00    |
| simplicity      | 0.20   | 3     | 0.60    |
| testability     | 0.20   | 4     | 0.80    |
| robustness      | 0.20   | 3     | 0.60    |
| extensibility   | 0.15   | 2     | 0.30    |
| **TOTAL**       |        |       | **3.30**|

## Weaknesses
- [Specific criticism 1 — must be technical, not stylistic]
- [Specific criticism 2]

## Verdict
[CLAUDE | CODEX | TIE] — [one-sentence rationale]
```

### SYNTHESIZED entry structure

```markdown
## Synthesis

### Adopted from winner
- [Specific element and why]

### Adopted from this proposal (addressing weaknesses)
- [Specific weakness that was addressed and how]

### Merged design
[Full merged proposal — not a summary of both, a single coherent design]
```

---

## Configuration Reference

`config.json` controls all tunable behavior:

```json
{
  "max_iterations_default": 3,
  "token_budget_per_round_file": 6000,
  "token_budget_per_entry": 800,
  "score_proximity_warning_delta": 0.3,
  "scoring_weights": {
    "goal_alignment": 0.25,
    "simplicity": 0.20,
    "testability": 0.20,
    "robustness": 0.20,
    "extensibility": 0.15
  },
  "forbidden_phrases": [
    "I agree", "great point", "you're right", "exactly",
    "well said", "good idea", "nice approach", "that makes sense"
  ],
  "build_agent": "CODEX",
  "review_agent": "CLAUDE",
  "human_gates": ["SPEC_READY", "VALIDATED"],
  "max_build_iterations": 3,
  "validation": {
    "lint_command": "ruff check src/ && mypy src/ --ignore-missing-imports",
    "command": "pytest tests/ -v",
    "timeout_seconds": 120,
    "on_failure": "reopen_round"
  },
  "stuck_state_timeout_hours": 24,
  "git_auto_commit": true,
  "cli": {
    "claude": {
      "command": "claude",
      "flags": ["-p", "--output-format", "text", "--max-turns", "1", "--dangerously-skip-permissions"],
      "model": null
    },
    "codex": {
      "command": "codex",
      "flags": ["--quiet", "--full-auto", "--dangerously-bypass-approvals-and-sandbox"],
      "model": null
    }
  }
}
```

**Key config fields:**

| Field | Default | Description |
|---|---|---|
| `human_gates` | `["SPEC_READY", "VALIDATED"]` | States that pause for human review in the SDLC pipeline |
| `max_build_iterations` | `3` | Max CODEX build attempts before escalating to human |
| `build_agent` | `"CODEX"` | LLM that runs the BUILD phase |
| `review_agent` | `"CLAUDE"` | LLM that runs CODE_REVIEW and QA_WRITE |
| `validation.lint_command` | `"ruff check src/ && mypy src/ --ignore-missing-imports"` | Runs after every BUILD; failure blocks CODE_REVIEW approval |
| `validation.command` | `"pytest tests/ -v"` | Runs in TEST phase; failure → QA_FAILED rebuild loop |

---

## Prerequisites

- Python 3.10+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — `claude`
- [OpenAI Codex CLI](https://github.com/openai/codex) — `codex`
- git (configured with user.name and user.email)

---

## Setup and Usage

### 1. Define your project goal

Edit `seed.md`. Replace all placeholder text. Be specific — vague seeds produce vague proposals.

```bash
vim seed.md
```

`seed.md` is immutable after initialization. If you need to change scope, start a new repo.

### 2. (Optional) Add scoring calibration examples

Create `calibration/scoring_examples.md` with 2–3 example SCORED entries annotated with why they received the scores they did. These are injected into every scoring prompt as anchors.

### 3. Configure lint and test commands

Edit `config.json → validation` to match your project's language and toolchain:

```json
"validation": {
  "lint_command": "ruff check src/ && mypy src/ --ignore-missing-imports",
  "command": "pytest tests/ -v",
  "timeout_seconds": 120
}
```

- `lint_command` runs **automatically after every CODEX build**. Failures are injected into the code review context and block approval — CODEX must fix them before the round can advance.
- `command` runs in the **TEST phase** after CLAUDE writes the test suite. Failure triggers a rebuild loop (`QA_FAILED → BUILD`).

Both commands run from the repo root. Adjust for your language:

| Language | lint_command | command |
|---|---|---|
| Python | `ruff check src/ && mypy src/ --ignore-missing-imports` | `pytest tests/ -v` |
| Node/TS | `eslint src/ && tsc --noEmit` | `npm test` |
| Go | `golangci-lint run ./...` | `go test ./...` |

> **These are not optional.** Leaving them unset means lint and tests do not run. Code will not be verified.

### 4. Initialize

```bash
python orchestrator.py --init
```

This runs Round 1 (Problem Decomposition): CLAUDE proposes a breakdown of your seed into collaboration rounds, CODEX critiques it, and you approve the round plan.

**Dry run** (no real CLI calls, stub output):

```bash
python orchestrator.py --init --dry-run
```

### 5. Review Round 1

The orchestrator pauses at `HUMAN_REVIEW`. Open `collaboration/rounds/r01_problem_decomposition.md` and read both entries. Then issue a command at the `>>>` prompt:

- `ACCEPT` — proceed with the proposed round plan
- `REJECT` — restart R01 with fresh proposals
- `EXTEND +2` — grant 2 more iterations before escalating to you

### 6. Run subsequent rounds

```bash
python orchestrator.py --resume
```

The orchestrator will prompt for a round title if starting a new round from IDLE, then run the full debate loop (PROPOSE → SCORE → SYNTHESIZE → HUMAN_REVIEW) automatically, pausing at each `HUMAN_REVIEW` gate.

To pre-specify the round title (useful for scripting):

```bash
python orchestrator.py --resume --round-title "api_design"
```

### 7. Start the build pipeline

Once a round is `AGREED`, issue `BEGIN_BUILD` at the `>>>` prompt to enter the SDLC pipeline:

1. **SPEC** — CLAUDE writes a detailed implementation spec to `collaboration/spec.md`
2. **SPEC_READY** — Human gate (default). Review the spec, then issue `BEGIN_BUILD` to approve it and start CODEX
3. **BUILD** — CODEX (`--full-auto`) reads the spec and writes implementation files to `src/`. Lint runs automatically (`validation.lint_command`). Lint failures are injected into the code review context and **block approval**.
4. **CODE_REVIEW** — CLAUDE reviews the code against the spec. If lint failed OR acceptance criteria aren't met: REWORK REQUIRED → back to BUILD
5. **QA_WRITE** — CLAUDE writes a pytest test suite to `tests/`
6. **TEST** — `validation.command` runs; exit 0 → VALIDATED, non-zero → QA_FAILED (rebuild loop)
7. **VALIDATED** — Human gate (default). Issue `APPROVE_NEXT_ROUND` to advance to IDLE

To skip human gates, remove the state from `human_gates` in config.json.

### 8. Deadlock handling

If `max_iterations` is exceeded, the system enters DEADLOCK. Both LLMs write plea files to `collaboration/pleas/`. The orchestrator also generates a `compromise_template.md` in `collaboration/compromise/` listing the specific contested decisions as structured choices. Fill it out at the `>>>` prompt or edit the file and issue `ACCEPT`.

### 9. Monitor progress

All state is in `state.json`. Full history is in git log. Per-turn metrics (duration, scores, token counts where available) are appended to `metrics.jsonl`.

```bash
# Current state
cat state.json

# Full audit trail
git log --oneline

# Score trends (requires jq)
jq '{round, llm, score_given}' metrics.jsonl
```

---

## Anti-Sycophancy Rules

These are enforced in the system prompt and checked by the orchestrator:

1. **Score first, justify second.** Numeric scores appear before any reasoning. This prevents post-hoc score rationalization.
2. **Mandatory weaknesses.** Every SCORED entry must include `## Weaknesses` with at least two specific technical criticisms. The orchestrator warns if this section is missing.
3. **Forbidden phrases.** The phrases in `config.json → forbidden_phrases` are checked against every LLM output. Violations are logged as warnings.
4. **No prose preamble.** Entries lead with technical substance. Social padding and affirmations are out of format.
5. **Extreme score justification.** A score of 1 or 5 on any dimension must be accompanied by a concrete example or code block.

---

## Observability

| Artifact | Contents |
|---|---|
| `metrics.jsonl` | Per-turn: timestamp, round, LLM, duration, scores, token count |
| `collaboration/index.yaml` | Per-round: outcome, iteration count, tags, summary |
| `collaboration/decisions/accepted.md` | Accepted decision summaries with rationale |
| `collaboration/decisions/rejected.md` | Rejected decision summaries |
| `git log` | Full audit trail of every state transition |

---

## LLM Roles Summary

| Phase | Role | Agent | Output |
|---|---|---|---|
| PROPOSE | Technical proposer | CLAUDE, CODEX | Round file entry |
| SCORE | Peer reviewer | CLAUDE, CODEX | Scored round file entry |
| SYNTHESIZE | Merger | Lower-scoring LLM | Merged design entry |
| SPEC | Spec writer | CLAUDE | `collaboration/spec.md` |
| BUILD | Developer | CODEX | `src/` implementation files |
| CODE_REVIEW | Code reviewer | CLAUDE | `collaboration/validations/r{N}_code_review.md` |
| QA_WRITE | QA engineer | CLAUDE | `tests/` pytest suite |
| TEST | Validator | Shell | `collaboration/validations/r{N}_result.md` |

---

## Known Limitations

- **Score reliability.** LLM scoring is subjective and can drift across rounds. Calibration examples reduce variance but do not eliminate it. Cross-round normalization is not implemented.
- **Codex system prompt injection.** The Codex CLI does not support a dedicated `--system-prompt` flag; system instructions are prepended to the user prompt. This is less reliable than Claude's native system prompt handling.
- **No automatic agreement detection.** The orchestrator does not parse verdicts to detect when both LLMs reach the same conclusion independently. Every round goes to `HUMAN_REVIEW` regardless.
- **Build coverage.** Codex `--full-auto` writes files directly to disk during its internal loop. The orchestrator captures a summary but does not enumerate or validate individual files written.
