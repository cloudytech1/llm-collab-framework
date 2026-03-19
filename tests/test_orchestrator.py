"""
Tests for orchestrator.py — covers pure functions and state machine helpers.
LLM invocation, git ops, and subprocess calls are not tested here (integration concerns).
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Patch config/state file paths before importing orchestrator
_tmpdir = tempfile.mkdtemp()
_tmp = Path(_tmpdir)

# Minimal valid config and state written before import
(_tmp / "config.json").write_text(json.dumps({
    "max_iterations_default": 3,
    "token_budget_per_round_file": 6000,
    "token_budget_per_entry": 800,
    "score_proximity_warning_delta": 0.3,
    "scoring_weights": {
        "goal_alignment": 0.25,
        "simplicity": 0.20,
        "testability": 0.20,
        "robustness": 0.20,
        "extensibility": 0.15,
    },
    "forbidden_phrases": ["I agree", "great point", "you're right"],
    "git_auto_commit": False,
    "validation": {"command": None, "timeout_seconds": 120},
    "stuck_state_timeout_hours": 24,
    "cli": {
        "claude": {"command": "claude", "flags": ["-p"], "model": None},
        "codex": {"command": "codex", "flags": ["--quiet"], "model": None},
    },
}))

(_tmp / "state.json").write_text(json.dumps({
    "project": "",
    "round": "r01",
    "round_title": "test_round",
    "iteration": 1,
    "state": "DEBATED",
    "turn": "CLAUDE",
    "max_iterations": 3,
    "token_budget_per_entry": 800,
    "round_file": None,
    "last_updated": None,
    "score_proximity_warning": False,
    "history": [],
}))

(_tmp / "seed.md").write_text("# Test Seed\n## Objective\nTest project.\n")
(_tmp / "state.lock").write_text("")

# Redirect module-level paths before import
import orchestrator as _orch_pre_patch  # noqa: E402
_orch_pre_patch.REPO_ROOT = _tmp
_orch_pre_patch.STATE_FILE = _tmp / "state.json"
_orch_pre_patch.CONFIG_FILE = _tmp / "config.json"
_orch_pre_patch.SEED_FILE = _tmp / "seed.md"
_orch_pre_patch.LOCK_FILE = _tmp / "state.lock"
_orch_pre_patch.ROUNDS_DIR = _tmp / "collaboration" / "rounds"
_orch_pre_patch.DECISIONS_DIR = _tmp / "collaboration" / "decisions"
_orch_pre_patch.PLEAS_DIR = _tmp / "collaboration" / "pleas"
_orch_pre_patch.CALIBRATION_DIR = _tmp / "calibration"
_orch_pre_patch.VALIDATIONS_DIR = _tmp / "collaboration" / "validations"
_orch_pre_patch.COMPROMISE_DIR = _tmp / "collaboration" / "compromise"
_orch_pre_patch.INDEX_FILE = _tmp / "collaboration" / "index.yaml"
_orch_pre_patch.METRICS_FILE = _tmp / "metrics.jsonl"

import orchestrator as orch  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# Pure function tests
# ──────────────────────────────────────────────────────────────────────

class TestNextRoundId:
    def test_increments_r01_to_r02(self):
        assert orch.next_round_id("r01") == "r02"

    def test_increments_r09_to_r10(self):
        assert orch.next_round_id("r09") == "r10"

    def test_zero_padded(self):
        assert orch.next_round_id("r05") == "r06"

    def test_double_digits(self):
        assert orch.next_round_id("r10") == "r11"


class TestOtherLlm:
    def test_claude_returns_codex(self):
        assert orch.other_llm("CLAUDE") == "CODEX"

    def test_codex_returns_claude(self):
        assert orch.other_llm("CODEX") == "CLAUDE"


class TestNowIso:
    def test_format(self):
        result = orch.now_iso()
        assert result.endswith("Z")
        assert "T" in result
        assert len(result) == 20  # YYYY-MM-DDTHH:MM:SSZ


class TestCheckForbiddenPhrases:
    def setup_method(self):
        self.config = {"forbidden_phrases": ["I agree", "great point", "you're right"]}

    def test_detects_forbidden_phrase(self):
        violations = orch.check_forbidden_phrases("I agree with this approach.", self.config)
        assert "I agree" in violations

    def test_case_insensitive(self):
        violations = orch.check_forbidden_phrases("GREAT POINT about the design.", self.config)
        assert "great point" in violations

    def test_no_violations(self):
        violations = orch.check_forbidden_phrases("This approach has poor testability.", self.config)
        assert violations == []

    def test_multiple_violations(self):
        text = "I agree and that's a great point."
        violations = orch.check_forbidden_phrases(text, self.config)
        assert len(violations) == 2

    def test_empty_text(self):
        assert orch.check_forbidden_phrases("", self.config) == []

    def test_empty_config(self):
        assert orch.check_forbidden_phrases("I agree", {}) == []


class TestParseScoresFromEntry:
    def test_parses_bold_total_multi_column(self):
        # Real scoring table: | **TOTAL** | | | **3.75**|
        text = "| **TOTAL**       |        |       | **3.75**|"
        assert orch.parse_scores_from_entry(text) == 3.75

    def test_parses_plain_total(self):
        text = "TOTAL | 4.2"
        assert orch.parse_scores_from_entry(text) == 4.2

    def test_returns_none_when_missing(self):
        assert orch.parse_scores_from_entry("No scores here.") is None

    def test_integer_score(self):
        text = "| **TOTAL**       |        |       | **4**|"
        assert orch.parse_scores_from_entry(text) == 4.0

    def test_bold_takes_precedence_over_plain(self):
        # Bold regex checked first; plain fallback not used when bold matches
        text = "TOTAL | 2.0\n**TOTAL** | **3.5**"
        assert orch.parse_scores_from_entry(text) == 3.5


class TestCheckScoreProximity:
    def setup_method(self):
        self.config = {"score_proximity_warning_delta": 0.3}

    def test_close_scores_trigger_warning(self):
        assert orch.check_score_proximity(3.5, 3.6, self.config) is True

    def test_distant_scores_no_warning(self):
        assert orch.check_score_proximity(2.0, 4.0, self.config) is False

    def test_above_delta_no_warning(self):
        # 0.4 > 0.3 threshold — not close
        assert orch.check_score_proximity(3.0, 3.4, self.config) is False

    def test_none_score_a(self):
        assert orch.check_score_proximity(None, 3.5, self.config) is False

    def test_none_score_b(self):
        assert orch.check_score_proximity(3.5, None, self.config) is False

    def test_both_none(self):
        assert orch.check_score_proximity(None, None, self.config) is False


# ──────────────────────────────────────────────────────────────────────
# File management tests
# ──────────────────────────────────────────────────────────────────────

class TestRoundFileManagement:
    def setup_method(self):
        orch.ensure_dirs()

    def test_create_round_file_creates_file(self):
        path = orch.create_round_file("r99", "test_title")
        assert path.exists()
        content = path.read_text()
        assert "r99" in content
        assert "test_title" in content
        assert "STATUS: ACTIVE" in content

    def test_append_to_round_file(self):
        path = orch.create_round_file("r98", "append_test")
        orch.append_to_round_file(path, "## New Entry\nContent here.")
        assert "## New Entry" in path.read_text()

    def test_close_round_file(self):
        path = orch.create_round_file("r97", "close_test")
        orch.close_round_file(path)
        assert "STATUS: CLOSED" in path.read_text()
        assert "STATUS: ACTIVE" not in path.read_text()

    def test_append_decision_summary_accepted(self):
        accepted = _tmp / "collaboration" / "decisions" / "accepted.md"
        accepted.write_text("# Accepted\n")
        orch.append_decision_summary(True, "r99", "Test decision summary.")
        content = accepted.read_text()
        assert "r99" in content
        assert "Test decision summary." in content

    def test_append_decision_summary_rejected(self):
        rejected = _tmp / "collaboration" / "decisions" / "rejected.md"
        rejected.write_text("# Rejected\n")
        orch.append_decision_summary(False, "r99", "Rejected reason.")
        assert "Rejected reason." in rejected.read_text()


# ──────────────────────────────────────────────────────────────────────
# Metrics
# ──────────────────────────────────────────────────────────────────────

class TestAppendMetric:
    def setup_method(self):
        if orch.METRICS_FILE.exists():
            orch.METRICS_FILE.unlink()

    def test_writes_jsonl_line(self):
        orch.append_metric({"round": "r01", "llm": "CLAUDE", "action": "PROPOSED"})
        lines = orch.METRICS_FILE.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["round"] == "r01"
        assert record["llm"] == "CLAUDE"
        assert "ts" in record

    def test_appends_multiple_lines(self):
        orch.append_metric({"action": "PROPOSED"})
        orch.append_metric({"action": "SCORED"})
        lines = orch.METRICS_FILE.read_text().strip().split("\n")
        assert len(lines) == 2


# ──────────────────────────────────────────────────────────────────────
# Index
# ──────────────────────────────────────────────────────────────────────

class TestUpdateIndex:
    def setup_method(self):
        orch.ensure_dirs()
        if orch.INDEX_FILE.exists():
            orch.INDEX_FILE.unlink()

    def test_creates_index_entry(self):
        state = {
            "round": "r02", "round_title": "architecture",
            "iteration": 2, "round_file": "collaboration/rounds/r02_architecture.md",
        }
        orch.update_index(state, "AGREED", "Chose relational schema.")
        content = orch.INDEX_FILE.read_text()
        assert "r02" in content
        assert "AGREED" in content
        assert "architecture" in content

    def test_appends_multiple_entries(self):
        for i in range(3):
            state = {"round": f"r0{i+2}", "round_title": f"round_{i}",
                     "iteration": 1, "round_file": ""}
            orch.update_index(state, "AGREED", f"Summary {i}.")
        content = orch.INDEX_FILE.read_text()
        assert content.count("- id:") == 3


# ──────────────────────────────────────────────────────────────────────
# State & config loading
# ──────────────────────────────────────────────────────────────────────

class TestLoadSave:
    def test_load_config_returns_dict(self):
        config = orch.load_config()
        assert isinstance(config, dict)
        assert "scoring_weights" in config

    def test_load_state_returns_dict(self):
        state = orch.load_state()
        assert isinstance(state, dict)
        assert "state" in state

    def test_save_state_updates_last_updated(self):
        state = orch.load_state()
        state["state"] = "IDLE"
        orch.save_state(state)
        reloaded = orch.load_state()
        assert reloaded["last_updated"] is not None
        assert reloaded["state"] == "IDLE"


# ──────────────────────────────────────────────────────────────────────
# Dry-run stub
# ──────────────────────────────────────────────────────────────────────

class TestDryRunStub:
    def test_contains_llm_id(self):
        stub = orch._dry_run_stub("CLAUDE")
        assert "FROM: CLAUDE" in stub

    def test_contains_yaml_header(self):
        stub = orch._dry_run_stub("CODEX")
        assert "---" in stub
        assert "FROM: CODEX" in stub

    def test_is_string(self):
        assert isinstance(orch._dry_run_stub("CLAUDE"), str)
