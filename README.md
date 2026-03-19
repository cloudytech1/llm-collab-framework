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

### Out of scope

- Autonomous operation without human oversight
- Real-time streaming or UI — this is a CLI-driven, file-based system
- Support for more than two debating agents in the core loop (multi-agent role specialization is a future extension, not a current feature)
- Cloud deployment or networked coordination between orchestrator instances
- Any form of automatic decision acceptance

---

## State Machine

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
  | (LLM generates artifacts)
  v
IMPLEMENT
  |
  | (validation_command runs)
  v
TEST
  |               |
  | (exit 0)      | (exit non-zero)
  v               v
VALIDATED       FAILED
  |               |
  v               | (failure context injected)
 IDLE             +-----------> DEBATED
```

### Human commands

| Command             | Effect                                              |
|---------------------|-----------------------------------------------------|
| `ACCEPT`            | Accept current proposal; advance to IMPLEMENT       |
| `REJECT`            | Reject; round resets to DEBATED, iteration 1        |
| `EXTEND +N`         | Grant N additional iterations before DEADLOCK       |
| `OVERRIDE CLAUDE`   | Force-accept CLAUDE's proposal                      |
| `OVERRIDE CODEX`    | Force-accept CODEX's proposal                       |
| `APPROVE_NEXT_ROUND`| Advance from AGREED/VALIDATED to next round (IDLE)  |
| `QUIT`              | Exit the orchestrator                               |

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
├── calibration/
│   └── scoring_examples.md              # Anchor examples injected into scoring prompts
│
└── collaboration/
    ├── index.yaml                        # Lightweight round index for context retrieval
    ├── rounds/
    │   └── r{N}_{title}.md              # Full debate transcript per round
    ├── validations/
    │   └── r{N}_result.md               # VALIDATE phase output (stdout/stderr + exit code)
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
  "validation": {
    "command": "bash validate.sh",
    "timeout_seconds": 120,
    "on_failure": "reopen_round"
  },
  "stuck_state_timeout_hours": 24,
  "git_auto_commit": true,
  "cli": {
    "claude": {
      "command": "claude",
      "flags": ["-p", "--output-format", "text", "--max-turns", "1"],
      "model": null
    },
    "codex": {
      "command": "codex",
      "flags": ["--quiet", "--full-auto"],
      "model": null
    }
  }
}
```

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

### 3. (Optional) Configure validation

If your project produces testable artifacts, set `validation.command` in `config.json` to the shell command that validates them. The command runs from the repo root; exit 0 means VALIDATED, non-zero means FAILED.

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

### 7. Deadlock handling

If `max_iterations` is exceeded, the system enters DEADLOCK. Both LLMs write plea files to `collaboration/pleas/`. The orchestrator also generates a `compromise_template.md` in `collaboration/compromise/` listing the specific contested decisions as structured choices. Fill it out at the `>>>` prompt or edit the file and issue `ACCEPT`.

### 8. Monitor progress

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

## Known Limitations

- **Score reliability.** LLM scoring is subjective and can drift across rounds. Calibration examples reduce variance but do not eliminate it. Cross-round normalization is not implemented.
- **Two-agent only.** The core debate loop assumes exactly two agents. Role specialization (Architect, Implementer, Validator) is architecturally possible as phase-specific agents but is not currently implemented.
- **Codex system prompt injection.** The Codex CLI does not support a dedicated `--system-prompt` flag; system instructions are prepended to the user prompt. This is less reliable than Claude's native system prompt handling.
- **No automatic agreement detection.** The orchestrator does not parse verdicts to detect when both LLMs reach the same conclusion independently. Every round goes to `HUMAN_REVIEW` regardless.
