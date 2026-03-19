#!/usr/bin/env python3
"""
LLM Collaboration Framework — Shell Orchestrator
Drives a structured debate between two LLMs (CLAUDE + CODEX) on a shared git repo.
"""

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.resolve()
STATE_FILE = REPO_ROOT / "state.json"
CONFIG_FILE = REPO_ROOT / "config.json"
SEED_FILE = REPO_ROOT / "seed.md"
LOCK_FILE = REPO_ROOT / "state.lock"
ROUNDS_DIR = REPO_ROOT / "collaboration" / "rounds"
DECISIONS_DIR = REPO_ROOT / "collaboration" / "decisions"
PLEAS_DIR = REPO_ROOT / "collaboration" / "pleas"
CALIBRATION_DIR = REPO_ROOT / "calibration"
VALIDATIONS_DIR = REPO_ROOT / "collaboration" / "validations"
COMPROMISE_DIR = REPO_ROOT / "collaboration" / "compromise"
INDEX_FILE = REPO_ROOT / "collaboration" / "index.yaml"
METRICS_FILE = REPO_ROOT / "metrics.jsonl"

VALID_STATES = [
    "IDLE",
    "PROPOSED",
    "DEBATED",
    "SCORED",
    "SYNTHESIZED",
    "AGREED",
    "DEADLOCKED",
    "HUMAN_REVIEW",
    "IMPLEMENT",
    "TEST",
    "VALIDATED",
    "FAILED",
    "COMPLETE",
]
VALID_TURNS = ["CLAUDE", "CODEX", "HUMAN"]
LLM_IDS = ["CLAUDE", "CODEX"]

# ──────────────────────────────────────────────────────────────────────
# Directory bootstrap
# ──────────────────────────────────────────────────────────────────────


def ensure_dirs():
    """Create all required directories on first run."""
    for d in [
        ROUNDS_DIR,
        DECISIONS_DIR,
        PLEAS_DIR,
        CALIBRATION_DIR,
        VALIDATIONS_DIR,
        COMPROMISE_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────
# Config & State helpers
# ──────────────────────────────────────────────────────────────────────


def load_json(path: Path) -> dict:
    with open(path) as f:
        result: dict = json.load(f)
        return result


def save_json(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_config() -> dict:
    return load_json(CONFIG_FILE)


def load_state() -> dict:
    return load_json(STATE_FILE)


def save_state(state: dict):
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_FILE, state)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ──────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────


def append_metric(record: dict):
    """Append a single JSON record to metrics.jsonl."""
    record["ts"] = now_iso()
    with open(METRICS_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


# ──────────────────────────────────────────────────────────────────────
# Write Lock Protocol
# ──────────────────────────────────────────────────────────────────────


class WriteLock:
    """Context manager for the state.lock file."""

    def __init__(self, owner: str, timeout: int = 30, poll: float = 0.5):
        self.owner = owner
        self.timeout = timeout
        self.poll = poll

    def _is_locked(self) -> bool:
        if not LOCK_FILE.exists():
            return False
        try:
            content = LOCK_FILE.read_text().strip()
            return len(content) > 0
        except OSError:
            return False

    def __enter__(self):
        deadline = time.time() + self.timeout
        while self._is_locked():
            if time.time() > deadline:
                existing = LOCK_FILE.read_text().strip()
                raise TimeoutError(
                    f"Lock held by '{existing}' for > {self.timeout}s. "
                    f"If stale, delete {LOCK_FILE} manually."
                )
            time.sleep(self.poll)
        LOCK_FILE.write_text(f"{self.owner}:{now_iso()}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if LOCK_FILE.exists():
                LOCK_FILE.unlink()
        except OSError:
            try:
                LOCK_FILE.write_text("")
            except OSError:
                pass
        return False


# ──────────────────────────────────────────────────────────────────────
# Git helpers
# ──────────────────────────────────────────────────────────────────────


def git_commit(message: str):
    config = load_config()
    if not config.get("git_auto_commit", True):
        return
    try:
        subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        print(f"  [git] committed: {message}")
    except subprocess.CalledProcessError:
        print("  [git] commit skipped (no changes or git not initialized)")


def git_init_if_needed():
    git_dir = REPO_ROOT / ".git"
    if not git_dir.exists():
        try:
            subprocess.run(["git", "init"], cwd=REPO_ROOT, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "orchestrator@llm-collab"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "LLM Collab Orchestrator"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "initial scaffold"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
            )
            print("[git] initialized repository")
        except subprocess.CalledProcessError as e:
            print(f"[git] init warning: {e}")


# ──────────────────────────────────────────────────────────────────────
# Round Index
# ──────────────────────────────────────────────────────────────────────


def update_index(state: dict, outcome: str, summary: str):
    """Append a round entry to collaboration/index.yaml."""
    entry_lines = [
        f"- id: {state['round']}",
        f'  title: "{state.get("round_title", state["round"])}"',
        f"  outcome: {outcome}",
        f"  iteration_count: {state['iteration']}",
        f'  file: "{state.get("round_file", "")}"',
        f'  summary: "{summary.replace(chr(34), chr(39))}"',
        f"  ts: {now_iso()}",
    ]
    with open(INDEX_FILE, "a") as f:
        f.write("\n".join(entry_lines) + "\n\n")


# ──────────────────────────────────────────────────────────────────────
# System Prompt Generator
# ──────────────────────────────────────────────────────────────────────


def build_system_prompt(llm_id: str, state: dict, config: dict) -> str:
    """Build the system prompt injected into every LLM invocation."""

    seed_text = SEED_FILE.read_text() if SEED_FILE.exists() else "(no seed)"
    forbidden = ", ".join(f'"{p}"' for p in config.get("forbidden_phrases", []))
    weights = config.get("scoring_weights", {})
    weight_lines = "\n".join(f"  - {k}: {v}" for k, v in weights.items())

    round_context = ""
    rf = state.get("round_file")
    if rf:
        rf_path = REPO_ROOT / rf
        if rf_path.exists():
            round_context = rf_path.read_text()

    accepted_path = DECISIONS_DIR / "accepted.md"
    accepted_context = ""
    if accepted_path.exists():
        accepted_context = accepted_path.read_text()

    calibration_text = ""
    calibration_path = CALIBRATION_DIR / "scoring_examples.md"
    if calibration_path.exists():
        calibration_text = calibration_path.read_text()

    failure_context = ""
    last_failure = state.get("last_validation_failure")
    if last_failure:
        failure_context = f"\n== LAST VALIDATION FAILURE ==\n{last_failure}\n"

    return f"""You are {llm_id} in a structured LLM collaboration framework.
You are debating technical decisions with your counterpart to produce the best possible outcome.

== ROLE ==
Your ID: {llm_id}
Opponent: {"CODEX" if llm_id == "CLAUDE" else "CLAUDE"}
Current round: {state["round"]}
Current iteration: {state["iteration"]}
Current state: {state["state"]}
Max iterations this round: {state["max_iterations"]}

== PROJECT SEED (IMMUTABLE) ==
{seed_text}

== ACCEPTED DECISIONS (prior rounds) ==
{accepted_context if accepted_context else "(none yet)"}

== CURRENT ROUND FILE ==
{round_context if round_context else "(empty — you are writing the first entry)"}
{failure_context}
== SCORING RUBRIC ==
Score the opposing proposal ONLY. Commit numeric scores BEFORE writing reasoning.
Dimensions and weights:
{weight_lines}
Score each dimension 1–5. Final score = weighted average.
A score of 1 or 5 on any dimension MUST be accompanied by a code block or concrete example.

== CALIBRATION EXAMPLES ==
{calibration_text if calibration_text else "(none — use rubric weights as sole anchor)"}

== ANTI-SYCOPHANCY RULES (MANDATORY) ==
1. Score first, justify second — write numeric scores before reasoning.
2. Mandatory weaknesses — every SCORED entry MUST include ## Weaknesses with ≥2 specific criticisms.
3. Forbidden phrases — NEVER use: {forbidden}
4. No prose preamble. No affirmations. No social padding.
5. Lead with technical substance. Use bullets, code blocks, structured lists.
6. Stay within {config.get("token_budget_per_entry", 800)} token budget per entry.

== ENTRY FORMAT ==
Start every entry with this exact YAML header:
---
FROM: {llm_id}
ROUND: {state["round"]}
ITER: {state["iteration"]}
STATE: PROPOSED | SCORED | SYNTHESIZED | PLEA
TS: [current ISO timestamp]
---

Then write your technical content below the header.

== COMMUNICATION STANDARDS ==
- No prose preamble — lead with technical substance
- Precise technical vocabulary: data structures, complexity, interfaces, failure modes
- Bullets and code blocks preferred over paragraphs
- If a concept needs >3 sentences → use a code block or structured list
"""


# ──────────────────────────────────────────────────────────────────────
# CLI Invocation Wrappers
# ──────────────────────────────────────────────────────────────────────


def invoke_claude(prompt: str, system_prompt: str, dry_run: bool = False) -> str:
    """Invoke Claude Code CLI."""
    config = load_config()
    cli_cfg = config.get("cli", {}).get("claude", {})
    cmd = cli_cfg.get("command", "claude")
    base_flags = cli_cfg.get("flags", ["-p", "--output-format", "text", "--max-turns", "1"])

    full_cmd = [cmd] + base_flags.copy()

    if "-p" in full_cmd:
        idx = full_cmd.index("-p")
        full_cmd.insert(idx + 1, prompt)
    else:
        full_cmd.extend(["-p", prompt])

    full_cmd.extend(["--system-prompt", system_prompt])

    if cli_cfg.get("model"):
        full_cmd.extend(["--model", cli_cfg["model"]])

    if dry_run:
        print(f"\n  [DRY RUN] Would invoke CLAUDE ({len(prompt)} char prompt)")
        return _dry_run_stub("CLAUDE")

    print("  [CLAUDE] invoking claude CLI...")
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=300, cwd=REPO_ROOT
        )
        if result.returncode != 0:
            print(f"  [CLAUDE] stderr: {result.stderr[:500]}")
            raise RuntimeError(f"Claude CLI failed: {result.stderr[:200]}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError("CLAUDE CLI timed out after 300s")
    except FileNotFoundError:
        raise RuntimeError(
            "'claude' CLI not found. Install Claude Code: https://docs.anthropic.com/en/docs/claude-code"
        )


def invoke_codex(prompt: str, system_prompt: str, dry_run: bool = False) -> str:
    """Invoke OpenAI Codex CLI."""
    config = load_config()
    cli_cfg = config.get("cli", {}).get("codex", {})
    cmd = cli_cfg.get("command", "codex")
    base_flags = cli_cfg.get("flags", ["--quiet", "--full-auto"])

    combined_prompt = f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\n---\n\nTASK:\n{prompt}"
    full_cmd = [cmd] + base_flags + [combined_prompt]

    if cli_cfg.get("model"):
        full_cmd.extend(["--model", cli_cfg["model"]])

    if dry_run:
        print(f"\n  [DRY RUN] Would invoke CODEX ({len(combined_prompt)} char prompt)")
        return _dry_run_stub("CODEX")

    print("  [CODEX] invoking codex CLI...")
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=300, cwd=REPO_ROOT
        )
        if result.returncode != 0:
            print(f"  [CODEX] stderr: {result.stderr[:500]}")
            raise RuntimeError(f"Codex CLI failed: {result.stderr[:200]}")
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError("CODEX CLI timed out after 300s")
    except FileNotFoundError:
        raise RuntimeError("'codex' CLI not found. Install: npm install -g @openai/codex")


def _dry_run_stub(llm_id: str) -> str:
    """Return a realistic stub entry for dry-run mode."""
    return f"""---
FROM: {llm_id}
ROUND: r01
ITER: 1
STATE: PROPOSED
TS: {now_iso()}
---

## Proposal (DRY RUN STUB)

This is a placeholder entry generated in dry-run mode.
Replace with actual LLM output by running without --dry-run.

- Point 1: [technical detail]
- Point 2: [technical detail]
- Point 3: [technical detail]
"""


def invoke_llm(llm_id: str, prompt: str, system_prompt: str, dry_run: bool = False) -> str:
    if llm_id == "CLAUDE":
        return invoke_claude(prompt, system_prompt, dry_run)
    elif llm_id == "CODEX":
        return invoke_codex(prompt, system_prompt, dry_run)
    else:
        raise ValueError(f"Unknown LLM ID: {llm_id}")


# ──────────────────────────────────────────────────────────────────────
# Forbidden Phrase Checker
# ──────────────────────────────────────────────────────────────────────


def check_forbidden_phrases(text: str, config: dict) -> list[str]:
    """Return list of forbidden phrases found in text."""
    violations = []
    for phrase in config.get("forbidden_phrases", []):
        if phrase.lower() in text.lower():
            violations.append(phrase)
    return violations


# ──────────────────────────────────────────────────────────────────────
# Score Parser & Proximity Check
# ──────────────────────────────────────────────────────────────────────


def parse_scores_from_entry(text: str) -> Optional[float]:
    """Extract the TOTAL weighted score from a SCORED entry."""
    # Bold format: **TOTAL** ... **X.XX** (any number of pipe-separated columns)
    match = re.search(r"\*\*TOTAL\*\*[^\n]*\*\*(\d+\.?\d*)\*\*", text)
    if match:
        return float(match.group(1))
    # Plain format fallback: TOTAL | X.XX
    match = re.search(r"TOTAL\s*\|\s*(\d+\.?\d*)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def check_score_proximity(score_a: Optional[float], score_b: Optional[float], config: dict) -> bool:
    """Return True if scores are suspiciously close (proximity warning)."""
    if score_a is None or score_b is None:
        return False
    delta = float(config.get("score_proximity_warning_delta", 0.3))
    return abs(score_a - score_b) < delta


# ──────────────────────────────────────────────────────────────────────
# Round File Management
# ──────────────────────────────────────────────────────────────────────


def create_round_file(round_id: str, title: str) -> Path:
    """Create a new round file with header."""
    filename = f"{round_id}_{title.lower().replace(' ', '_')}.md"
    filepath = ROUNDS_DIR / filename
    header = f"""# Round {round_id} — {title}
BUDGET: {load_config().get("token_budget_per_round_file", 6000)} tokens | OPENED: {datetime.now(timezone.utc).strftime("%Y-%m-%d")} | STATUS: ACTIVE

---

"""
    filepath.write_text(header)
    return filepath


def append_to_round_file(filepath: Path, entry: str):
    """Append an entry to the round file."""
    current = filepath.read_text()
    filepath.write_text(current + "\n" + entry + "\n")


def close_round_file(filepath: Path):
    """Mark a round file as CLOSED."""
    current = filepath.read_text()
    updated = current.replace("STATUS: ACTIVE", "STATUS: CLOSED")
    filepath.write_text(updated)


def append_decision_summary(accepted: bool, round_id: str, summary: str):
    """Append a decision summary to accepted.md or rejected.md."""
    target = DECISIONS_DIR / ("accepted.md" if accepted else "rejected.md")
    current = target.read_text() if target.exists() else ""
    entry = f"\n## {round_id} — {now_iso()}\n\n{summary}\n\n---\n"
    target.write_text(current + entry)


# ──────────────────────────────────────────────────────────────────────
# Human Interface
# ──────────────────────────────────────────────────────────────────────


def notify_human(message: str):
    """Notify the human operator."""
    print(f"\n{'=' * 60}")
    print("  HUMAN REVIEW REQUIRED")
    print(f"{'=' * 60}")
    print(f"  {message}")
    print(f"{'=' * 60}\n")
    print("\a", end="")


def prompt_human_review(state: dict) -> str:
    """Block on human input at a HUMAN_REVIEW gate. Returns the command string."""
    notify_human(
        f"Round: {state['round']} | Iteration: {state['iteration']} | State: {state['state']}\n"
        f"  Round file: {state.get('round_file', 'N/A')}"
    )

    print("Commands:")
    print("  ACCEPT                — Accept current proposal")
    print("  REJECT                — Reject; round re-opens with fresh proposals")
    print("  EXTEND +N             — Grant N more iterations before deadlock")
    print("  OVERRIDE CLAUDE|CODEX — Force-accept one LLM's proposal")
    if state["state"] == "AGREED":
        print("  IMPLEMENT             — Generate implementation artifacts for this round")
    print("  APPROVE_NEXT_ROUND    — Advance to next round")
    print("  QUIT                  — Exit orchestrator")
    print()

    while True:
        try:
            cmd = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            sys.exit(0)

        if not cmd:
            continue

        upper = cmd.upper()
        if upper in ("ACCEPT", "REJECT", "QUIT", "APPROVE_NEXT_ROUND", "IMPLEMENT"):
            return upper
        if upper.startswith("EXTEND"):
            parts = upper.split()
            if len(parts) == 2 and parts[1].startswith("+"):
                try:
                    n = int(parts[1][1:])
                    if n > 0:
                        return f"EXTEND +{n}"
                except ValueError:
                    pass
            print("  Usage: EXTEND +N  (e.g., EXTEND +3)")
            continue
        if upper.startswith("OVERRIDE"):
            parts = upper.split()
            if len(parts) == 2 and parts[1] in LLM_IDS:
                return f"OVERRIDE {parts[1]}"
            print("  Usage: OVERRIDE CLAUDE | OVERRIDE CODEX")
            continue
        print(f"  Unknown command: {cmd}")


# ──────────────────────────────────────────────────────────────────────
# R01 Seeding — LLM decomposes seed.md into rounds
# ──────────────────────────────────────────────────────────────────────


def seed_r01(dry_run: bool = False):
    """
    Have CLAUDE propose a problem decomposition from seed.md,
    then CODEX critique it. Human approves the final round plan.
    """
    state = load_state()
    config = load_config()

    rf = create_round_file("r01", "problem_decomposition")
    state["round"] = "r01"
    state["round_title"] = "problem_decomposition"
    state["iteration"] = 1
    state["state"] = "PROPOSED"
    state["turn"] = "CLAUDE"
    state["round_file"] = str(rf.relative_to(REPO_ROOT))
    save_state(state)

    sys_prompt = build_system_prompt("CLAUDE", state, config)
    proposal_prompt = (
        "Read the project seed above. Decompose it into discrete sub-problems, "
        "each of which becomes one collaboration round.\n\n"
        "Output a numbered list of rounds (r02, r03, ...) with:\n"
        "- Round ID\n"
        "- Title (short, snake_case-friendly)\n"
        "- 1-2 sentence description of the decision to be made\n"
        "- Key constraints or dependencies on prior rounds\n\n"
        "Format as a PROPOSED entry with the standard YAML header."
    )

    with WriteLock("CLAUDE"):
        t0 = time.time()
        claude_output = invoke_llm("CLAUDE", proposal_prompt, sys_prompt, dry_run)
        append_metric(
            {
                "round": "r01",
                "iter": 1,
                "llm": "CLAUDE",
                "action": "PROPOSED",
                "duration_s": round(time.time() - t0, 2),
                "output_len": len(claude_output),
            }
        )
        append_to_round_file(rf, claude_output)
        state["turn"] = "CODEX"
        state["state"] = "DEBATED"
        save_state(state)

    git_commit("r01_iter1_CLAUDE")

    state = load_state()
    sys_prompt = build_system_prompt("CODEX", state, config)
    critique_prompt = (
        "Review CLAUDE's problem decomposition above. "
        "Critique the round breakdown:\n"
        "- Are any rounds too broad or too narrow?\n"
        "- Are dependencies between rounds correctly identified?\n"
        "- Would you merge, split, or reorder any rounds?\n\n"
        "Provide your own revised decomposition if needed.\n"
        "Format as a SCORED entry with the standard YAML header."
    )

    with WriteLock("CODEX"):
        t0 = time.time()
        codex_output = invoke_llm("CODEX", critique_prompt, sys_prompt, dry_run)
        append_metric(
            {
                "round": "r01",
                "iter": 1,
                "llm": "CODEX",
                "action": "SCORED",
                "duration_s": round(time.time() - t0, 2),
                "output_len": len(codex_output),
            }
        )
        append_to_round_file(rf, codex_output)
        state["turn"] = "HUMAN"
        state["state"] = "HUMAN_REVIEW"
        save_state(state)

    git_commit("r01_iter1_CODEX")

    state = load_state()
    cmd = prompt_human_review(state)
    handle_human_command(cmd, state, config)


# ──────────────────────────────────────────────────────────────────────
# State Machine Core
# ──────────────────────────────────────────────────────────────────────


def other_llm(llm_id: str) -> str:
    return "CODEX" if llm_id == "CLAUDE" else "CLAUDE"


def run_proposal_turn(state: dict, config: dict, dry_run: bool = False):
    """Execute a PROPOSED turn: the current LLM writes a proposal."""
    llm_id = state["turn"]
    sys_prompt = build_system_prompt(llm_id, state, config)

    prompt = (
        "You are writing a PROPOSED entry for this round. "
        "Provide your technical proposal for the problem described in the round file header. "
        "Include: approach, data structures, interfaces, complexity analysis, "
        "trade-offs, and failure modes.\n"
        "Format as a PROPOSED entry with the standard YAML header."
    )

    rf = REPO_ROOT / state["round_file"]
    with WriteLock(llm_id):
        t0 = time.time()
        output = invoke_llm(llm_id, prompt, sys_prompt, dry_run)
        duration = round(time.time() - t0, 2)

        violations = check_forbidden_phrases(output, config)
        if violations:
            print(f"  [WARN] {llm_id} used forbidden phrases: {violations}")

        append_to_round_file(rf, output)
        append_metric(
            {
                "round": state["round"],
                "iter": state["iteration"],
                "llm": llm_id,
                "action": "PROPOSED",
                "duration_s": duration,
                "output_len": len(output),
            }
        )
        state["turn"] = other_llm(llm_id)
        state["state"] = "PROPOSED"
        save_state(state)

    git_commit(f"{state['round']}_iter{state['iteration']}_{llm_id}_proposed")


def run_scoring_turn(state: dict, config: dict, dry_run: bool = False) -> str:
    """Execute a SCORED turn: the current LLM scores the opposing proposal."""
    llm_id = state["turn"]
    sys_prompt = build_system_prompt(llm_id, state, config)

    prompt = (
        "You are writing a SCORED entry. Score the opposing LLM's most recent proposal.\n"
        "IMPORTANT: Commit your numeric scores FIRST, then write reasoning.\n"
        "You MUST include a ## Weaknesses section with at least 2 specific criticisms.\n"
        "A score of 1 or 5 on any dimension requires a code block or concrete example.\n"
        "End with a ## Verdict: state which proposal is better or TIE.\n"
        "Format as a SCORED entry with the standard YAML header and the scoring table."
    )

    rf = REPO_ROOT / state["round_file"]
    with WriteLock(llm_id):
        t0 = time.time()
        output = invoke_llm(llm_id, prompt, sys_prompt, dry_run)
        duration = round(time.time() - t0, 2)

        violations = check_forbidden_phrases(output, config)
        if violations:
            print(f"  [WARN] {llm_id} used forbidden phrases: {violations}")

        if "## Weaknesses" not in output and "## weaknesses" not in output.lower():
            print(f"  [WARN] {llm_id} SCORED entry missing ## Weaknesses section!")

        score = parse_scores_from_entry(output)
        append_to_round_file(rf, output)
        append_metric(
            {
                "round": state["round"],
                "iter": state["iteration"],
                "llm": llm_id,
                "action": "SCORED",
                "score_given": score,
                "duration_s": duration,
                "output_len": len(output),
            }
        )
        state["state"] = "SCORED"
        save_state(state)

    git_commit(f"{state['round']}_iter{state['iteration']}_{llm_id}_scored")
    return output


def run_synthesis_turn(state: dict, config: dict, dry_run: bool, loser_llm: str):
    """The lower-scoring LLM synthesizes a merged proposal."""
    winner_llm = other_llm(loser_llm)
    sys_prompt = build_system_prompt(loser_llm, state, config)

    prompt = (
        f"Your proposal was scored lower than {winner_llm}'s in this iteration.\n"
        "You are writing a SYNTHESIZED entry — a merged design that:\n"
        f"1. Explicitly adopts the strongest elements of {winner_llm}'s proposal (cite them)\n"
        "2. Addresses the specific weaknesses identified in your own proposal\n"
        "3. Produces a single coherent merged design — not a summary of both\n\n"
        "Structure:\n"
        f"## Adopted from {winner_llm}\n"
        "- [element and why]\n\n"
        "## Addressed in my proposal\n"
        "- [weakness and how it's fixed]\n\n"
        "## Merged Design\n"
        "[Full merged proposal]\n\n"
        "Format as a SYNTHESIZED entry with the standard YAML header."
    )

    rf = REPO_ROOT / state["round_file"]
    with WriteLock(loser_llm):
        t0 = time.time()
        output = invoke_llm(loser_llm, prompt, sys_prompt, dry_run)
        duration = round(time.time() - t0, 2)

        violations = check_forbidden_phrases(output, config)
        if violations:
            print(f"  [WARN] {loser_llm} used forbidden phrases in synthesis: {violations}")

        append_to_round_file(rf, output)
        append_metric(
            {
                "round": state["round"],
                "iter": state["iteration"],
                "llm": loser_llm,
                "action": "SYNTHESIZED",
                "duration_s": duration,
                "output_len": len(output),
            }
        )
        state["state"] = "SYNTHESIZED"
        save_state(state)

    git_commit(f"{state['round']}_iter{state['iteration']}_{loser_llm}_synthesized")


def generate_compromise_template(state: dict, config: dict, dry_run: bool = False):
    """Generate a structured compromise form on DEADLOCK."""
    COMPROMISE_DIR.mkdir(parents=True, exist_ok=True)
    template_path = COMPROMISE_DIR / f"{state['round']}_compromise_template.md"

    sys_prompt = build_system_prompt("CLAUDE", state, config)
    prompt = (
        "The round has DEADLOCKED. Generate a structured compromise template for the human operator.\n"
        "Review the round file and identify the 3-5 most contested specific technical decisions.\n\n"
        "For each contested decision, output:\n"
        "### Decision N: [one-sentence description]\n"
        "- [ ] CLAUDE's position: [position]\n"
        "- [ ] CODEX's position: [position]\n"
        "- [ ] Custom: _______________\n\n"
        "Be specific — each decision must be a concrete technical choice, not a vague theme.\n"
        "The human will check boxes or fill in custom text to direct the compromise."
    )

    t0 = time.time()
    output = invoke_llm("CLAUDE", prompt, sys_prompt, dry_run)
    append_metric(
        {
            "round": state["round"],
            "iter": state["iteration"],
            "llm": "CLAUDE",
            "action": "COMPROMISE_TEMPLATE",
            "duration_s": round(time.time() - t0, 2),
            "output_len": len(output),
        }
    )

    full_template = (
        f"# Deadlock Compromise Template — Round {state['round']}\n"
        f"Generated: {now_iso()}\n\n"
        f"Read the plea files in collaboration/pleas/ then fill in this form.\n"
        f"Issue ACCEPT at the >>> prompt when done.\n\n---\n\n{output}\n"
    )
    template_path.write_text(full_template)
    git_commit(f"{state['round']}_compromise_template")
    print(f"  [DEADLOCK] Compromise template: {template_path.relative_to(REPO_ROOT)}")


def run_debate_iteration(state: dict, config: dict, dry_run: bool = False):
    """
    Run one full debate iteration:
    1. Both LLMs propose
    2. Both LLMs score each other
    3. Loser synthesizes a merged proposal
    4. → HUMAN_REVIEW (or DEADLOCKED if max iterations exceeded)
    """
    first = state["turn"]
    second = other_llm(first)

    print(f"\n--- Iteration {state['iteration']} ---")

    print(f"  [{first}] proposing...")
    run_proposal_turn(state, config, dry_run)

    state = load_state()
    print(f"  [{second}] proposing...")
    run_proposal_turn(state, config, dry_run)

    # Both score
    state = load_state()
    state["turn"] = first
    save_state(state)
    print(f"  [{first}] scoring {second}'s proposal...")
    score_output_a = run_scoring_turn(state, config, dry_run)  # first scores second's proposal

    state = load_state()
    state["turn"] = second
    save_state(state)
    print(f"  [{second}] scoring {first}'s proposal...")
    score_output_b = run_scoring_turn(state, config, dry_run)  # second scores first's proposal

    # score_a = score given to second's proposal; score_b = score given to first's proposal
    score_a = parse_scores_from_entry(score_output_a)
    score_b = parse_scores_from_entry(score_output_b)
    print(f"  Scores — {second}: {score_a}, {first}: {score_b}")

    state = load_state()
    if check_score_proximity(score_a, score_b, config):
        state["score_proximity_warning"] = True
        save_state(state)
        print("  [WARN] Score proximity warning — scores within delta")

    # Determine loser: lower score = loser (synthesizer)
    # score_a is for second, score_b is for first
    if score_a is not None and score_b is not None:
        loser = second if score_a < score_b else first
    else:
        loser = second  # fallback when scores unparseable

    print(f"  [{loser}] synthesizing merged proposal...")
    run_synthesis_turn(state, config, dry_run, loser)

    state = load_state()
    state["iteration"] += 1

    if state["iteration"] > state["max_iterations"]:
        print("  Max iterations reached — DEADLOCKED")
        state["state"] = "DEADLOCKED"
        state["turn"] = "HUMAN"
        save_state(state)
        run_plea_protocol(state, config, dry_run)
    else:
        state["state"] = "HUMAN_REVIEW"
        state["turn"] = "HUMAN"
        save_state(state)


def run_plea_protocol(state: dict, config: dict, dry_run: bool = False):
    """Generate plea files and compromise template on DEADLOCK."""
    print("\n  [DEADLOCK] Generating plea files...")

    for llm_id in LLM_IDS:
        sys_prompt = build_system_prompt(llm_id, state, config)
        prompt = (
            "The round has DEADLOCKED. Write a plea file.\n"
            "Format:\n"
            "# Plea — {ID} — Round {round}\n\n"
            "## Core Argument\n[2-3 sentences defending your proposal]\n\n"
            "## Specific Failure Mode in Opposing Proposal\n"
            "[Concrete technical failure scenario with conditions]\n\n"
            "## Concession\n"
            "[What you would modify if the human directs a compromise]\n"
        ).format(ID=llm_id, round=state["round"])

        plea_path = PLEAS_DIR / f"{state['round']}_plea_{llm_id}.md"
        with WriteLock(llm_id):
            t0 = time.time()
            output = invoke_llm(llm_id, prompt, sys_prompt, dry_run)
            append_metric(
                {
                    "round": state["round"],
                    "iter": state["iteration"],
                    "llm": llm_id,
                    "action": "PLEA",
                    "duration_s": round(time.time() - t0, 2),
                    "output_len": len(output),
                }
            )
            plea_path.write_text(output)

        git_commit(f"{state['round']}_plea_{llm_id}")

    generate_compromise_template(state, config, dry_run)

    state["turn"] = "HUMAN"
    state["state"] = "HUMAN_REVIEW"
    save_state(state)


# ──────────────────────────────────────────────────────────────────────
# Implementation & Validation
# ──────────────────────────────────────────────────────────────────────


def run_implement_turn(state: dict, config: dict, dry_run: bool = False):
    """CLAUDE generates implementation artifacts based on accepted decisions."""
    print("\n  [IMPLEMENT] CLAUDE generating artifacts...")
    sys_prompt = build_system_prompt("CLAUDE", state, config)

    prompt = (
        "Based on the accepted decisions documented above, generate the implementation artifacts.\n"
        "Write all code, configs, and files needed to satisfy the accepted design.\n"
        "Place source files under src/.\n"
        "After writing files, output a ## Manifest section listing every file created and its purpose."
    )

    t0 = time.time()
    output = invoke_llm("CLAUDE", prompt, sys_prompt, dry_run)
    duration = round(time.time() - t0, 2)

    impl_log = VALIDATIONS_DIR / f"{state['round']}_implement.md"
    impl_log.write_text(f"# Implementation Log — {state['round']}\nTS: {now_iso()}\n\n{output}\n")
    append_metric(
        {
            "round": state["round"],
            "iter": state["iteration"],
            "llm": "CLAUDE",
            "action": "IMPLEMENT",
            "duration_s": duration,
            "output_len": len(output),
        }
    )

    state["state"] = "TEST"
    state["turn"] = "HUMAN"
    save_state(state)
    git_commit(f"{state['round']}_IMPLEMENT")
    print("  [IMPLEMENT] Done. Running validation...")


def run_test_phase(state: dict, config: dict, dry_run: bool = False):
    """Run the configured validation command and transition to VALIDATED or FAILED."""
    validation = config.get("validation", {})
    cmd = validation.get("command")

    if not cmd:
        print("  [TEST] No validation.command in config — skipping to VALIDATED")
        state["state"] = "VALIDATED"
        save_state(state)
        return

    timeout = validation.get("timeout_seconds", 120)
    print(f"\n  [TEST] Running: {cmd}")

    result_path = VALIDATIONS_DIR / f"{state['round']}_result.md"

    if dry_run:
        exit_code = 0
        result_text = "DRY RUN — validation skipped"
    else:
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT
            )
            exit_code = result.returncode
            result_text = f"STDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        except subprocess.TimeoutExpired:
            exit_code = 1
            result_text = f"TIMEOUT after {timeout}s"

    status = "VALIDATED" if exit_code == 0 else "FAILED"
    result_path.write_text(
        f"# Validation Result — {state['round']}\n"
        f"TS: {now_iso()}\nExit code: {exit_code}\nStatus: {status}\n\n"
        f"```\n{result_text}\n```\n"
    )

    print(f"  [TEST] Exit code: {exit_code} → {status}")
    append_metric(
        {
            "round": state["round"],
            "iter": state["iteration"],
            "action": "TEST",
            "exit_code": exit_code,
            "status": status,
        }
    )

    state["state"] = status
    if status == "FAILED":
        state["last_validation_failure"] = result_text[:500]
    save_state(state)
    git_commit(f"{state['round']}_{status}")


# ──────────────────────────────────────────────────────────────────────
# Human Command Handlers
# ──────────────────────────────────────────────────────────────────────


def handle_human_command(cmd: str, state: dict, config: dict):
    """Process a human operator command and update state."""

    if cmd == "ACCEPT":
        print("  [HUMAN] Accepted.")
        state["state"] = "AGREED"
        state["turn"] = "HUMAN"
        save_state(state)

        rf = REPO_ROOT / state["round_file"]
        close_round_file(rf)

        summary = (
            f"Accepted at iteration {state['iteration']}. "
            f"See {state['round_file']} for full discussion."
        )
        append_decision_summary(True, state["round"], summary)
        update_index(state, "AGREED", summary)
        git_commit(f"{state['round']}_HUMAN_APPROVED")

    elif cmd == "REJECT":
        print("  [HUMAN] Rejected. Round re-opens with fresh proposals.")
        append_decision_summary(
            False, state["round"], f"Rejected at iteration {state['iteration']}. Round re-opened."
        )
        state["state"] = "DEBATED"
        state["iteration"] = 1
        state["turn"] = "CLAUDE"
        save_state(state)
        git_commit(f"{state['round']}_HUMAN_REJECTED")

    elif cmd.startswith("EXTEND"):
        n = int(cmd.split("+")[1])
        print(f"  [HUMAN] Extended by +{n} iterations.")
        state["max_iterations"] += n
        state["state"] = "DEBATED"
        state["turn"] = "CLAUDE"
        save_state(state)
        git_commit(f"{state['round']}_HUMAN_EXTENDED_{n}")

    elif cmd.startswith("OVERRIDE"):
        winner = cmd.split()[1]
        print(f"  [HUMAN] Overriding in favor of {winner}.")
        state["state"] = "AGREED"
        state["turn"] = "HUMAN"
        save_state(state)

        rf = REPO_ROOT / state["round_file"]
        close_round_file(rf)

        summary = (
            f"OVERRIDE: Human forced acceptance of {winner}'s proposal. "
            f"See {state['round_file']} for full discussion."
        )
        append_decision_summary(True, state["round"], summary)
        update_index(state, "AGREED_OVERRIDE", summary)
        git_commit(f"{state['round']}_HUMAN_OVERRIDE_{winner}")

    elif cmd == "IMPLEMENT":
        print("  [HUMAN] Triggering implementation phase.")
        state["state"] = "IMPLEMENT"
        state["turn"] = "CLAUDE"
        save_state(state)
        git_commit(f"{state['round']}_IMPLEMENT_TRIGGERED")

    elif cmd == "APPROVE_NEXT_ROUND":
        print("  [HUMAN] Approved advancement to next round.")
        state["state"] = "IDLE"
        save_state(state)
        git_commit(f"{state['round']}_HUMAN_APPROVE_NEXT")

    elif cmd == "QUIT":
        print("  Exiting orchestrator.")
        sys.exit(0)


# ──────────────────────────────────────────────────────────────────────
# Stuck-State Detection
# ──────────────────────────────────────────────────────────────────────


def check_stuck_state(state: dict, config: dict):
    """Warn if the orchestrator has been in a non-human state too long."""
    if state["state"] in ("HUMAN_REVIEW", "AGREED", "COMPLETE", "IDLE"):
        return
    last_updated = state.get("last_updated")
    if not last_updated:
        return
    timeout_hours = config.get("stuck_state_timeout_hours", 24)
    try:
        last_dt = datetime.fromisoformat(last_updated)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        if elapsed > timeout_hours:
            print(
                f"\n  [WARN] Stuck state: '{state['state']}' unchanged for {elapsed:.1f}h "
                f"(threshold: {timeout_hours}h). Consider restarting or inspecting state.json."
            )
    except (ValueError, TypeError):
        pass


# ──────────────────────────────────────────────────────────────────────
# Main Loop
# ──────────────────────────────────────────────────────────────────────


def next_round_id(current: str) -> str:
    """Increment round ID: r01 -> r02, etc."""
    num = int(current[1:]) + 1
    return f"r{num:02d}"


def main():
    import argparse

    parser = argparse.ArgumentParser(description="LLM Collaboration Framework Orchestrator")
    parser.add_argument(
        "--dry-run", action="store_true", help="Run with stub LLM outputs (no actual CLI calls)"
    )
    parser.add_argument(
        "--init", action="store_true", help="Initialize git repo and run R01 seeding"
    )
    parser.add_argument("--resume", action="store_true", help="Resume from current state.json")
    parser.add_argument(
        "--round-title",
        type=str,
        default=None,
        help="Title for a new round (used with --resume when starting a new round)",
    )
    args = parser.parse_args()

    ensure_dirs()
    config = load_config()

    if args.init:
        git_init_if_needed()
        seed = SEED_FILE.read_text()
        if "[HUMAN:" in seed:
            print("ERROR: seed.md still has placeholder text.")
            print("Edit seed.md with your project goal before running --init.")
            sys.exit(1)

        print("Starting R01: Problem Decomposition")
        print("CLAUDE will propose a round breakdown, CODEX will critique it.\n")
        seed_r01(dry_run=args.dry_run)
        return

    # Resume or continue main loop
    state = load_state()
    print(
        f"State: round={state['round']}, iter={state['iteration']}, "
        f"state={state['state']}, turn={state['turn']}"
    )

    while True:
        state = load_state()
        config = load_config()
        check_stuck_state(state, config)

        if state["state"] == "COMPLETE":
            print("\nProject COMPLETE. All rounds finished.")
            break

        elif state["state"] == "IDLE":
            rid = next_round_id(state["round"])
            title = args.round_title or input(f"Enter title for round {rid}: ").strip()
            args.round_title = None  # only use once
            if not title:
                print("Round title required.")
                continue

            rf = create_round_file(rid, title)
            state["round"] = rid
            state["round_title"] = title
            state["iteration"] = 1
            state["state"] = "DEBATED"
            state["turn"] = "CLAUDE"
            state["max_iterations"] = config.get("max_iterations_default", 3)
            state["round_file"] = str(rf.relative_to(REPO_ROOT))
            state["score_proximity_warning"] = False
            state.pop("last_validation_failure", None)
            save_state(state)
            git_commit(f"{rid}_opened")
            print(f"\nStarting round {rid}: {title}")

        elif state["state"] in ("DEBATED", "PROPOSED"):
            run_debate_iteration(state, config, dry_run=args.dry_run)

        elif state["state"] == "SCORED":
            # Resuming after an interrupted iteration — go to HUMAN_REVIEW
            state["turn"] = "HUMAN"
            state["state"] = "HUMAN_REVIEW"
            save_state(state)

        elif state["state"] in ("SYNTHESIZED", "HUMAN_REVIEW", "DEADLOCKED"):
            cmd = prompt_human_review(state)
            handle_human_command(cmd, state, config)

        elif state["state"] == "AGREED":
            print(f"\nRound {state['round']} AGREED.")
            print(f"  Read: {state.get('round_file')}")
            cmd = prompt_human_review(state)
            handle_human_command(cmd, state, config)

        elif state["state"] == "IMPLEMENT":
            run_implement_turn(state, config, dry_run=args.dry_run)

        elif state["state"] == "TEST":
            run_test_phase(state, config, dry_run=args.dry_run)

        elif state["state"] == "VALIDATED":
            print(f"\nRound {state['round']} VALIDATED.")
            cmd = prompt_human_review(state)
            handle_human_command(cmd, state, config)

        elif state["state"] == "FAILED":
            print(f"\nRound {state['round']} FAILED validation.")
            print("  Failure context injected into next debate iteration.")
            state["state"] = "DEBATED"
            state["iteration"] = 1
            state["turn"] = "CLAUDE"
            save_state(state)
            git_commit(f"{state['round']}_FAILED_REOPENED")

        else:
            print(f"Unknown state: {state['state']}")
            sys.exit(1)


if __name__ == "__main__":
    main()
