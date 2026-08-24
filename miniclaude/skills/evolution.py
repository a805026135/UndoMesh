"""Versioned, evidence-gated lifecycle for reusable skills."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SkillStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    success: bool
    tokens: int
    steps: int
    duration_ms: float
    safety_failure: bool = False


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    approved: bool
    reason: str
    success_rate: float
    average_tokens: float


class SkillVersionStore:
    """SQLite-backed Skill versions and evaluation evidence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS skill_versions (
                    skill_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY(skill_name, version)
                );
                CREATE TABLE IF NOT EXISTS skill_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    tokens INTEGER NOT NULL,
                    steps INTEGER NOT NULL,
                    duration_ms REAL NOT NULL,
                    safety_failure INTEGER NOT NULL
                );
                """
            )

    def register_candidate(self, skill_name: str, content: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM skill_versions WHERE skill_name = ?",
            (skill_name,),
        ).fetchone()
        version = int(row["version"])
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._connection:
            self._connection.execute(
                "INSERT INTO skill_versions VALUES (?, ?, ?, ?, ?)",
                (skill_name, version, digest, content, SkillStatus.CANDIDATE.value),
            )
        return version

    def ensure_active(self, skill_name: str, content: str) -> int:
        row = self._connection.execute(
            "SELECT version FROM skill_versions WHERE skill_name = ? AND status = ?",
            (skill_name, SkillStatus.ACTIVE.value),
        ).fetchone()
        if row:
            return int(row["version"])
        version = self.register_candidate(skill_name, content)
        with self._connection:
            self._connection.execute(
                "UPDATE skill_versions SET status = ? WHERE skill_name = ? AND version = ?",
                (SkillStatus.ACTIVE.value, skill_name, version),
            )
        return version

    def record(self, skill_name: str, version: int, outcome: EvaluationOutcome) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO skill_evaluations VALUES (NULL, ?, ?, ?, ?, ?, ?, ?)",
                (
                    skill_name,
                    version,
                    int(outcome.success),
                    outcome.tokens,
                    outcome.steps,
                    outcome.duration_ms,
                    int(outcome.safety_failure),
                ),
            )

    def evaluate_promotion(
        self,
        skill_name: str,
        version: int,
        *,
        baseline_success_rate: float,
        baseline_average_tokens: float,
        min_evaluations: int = 3,
        max_token_regression: float = 0.20,
    ) -> PromotionDecision:
        row = self._connection.execute(
            """
            SELECT COUNT(*) AS count, AVG(success) AS success_rate,
                   AVG(tokens) AS average_tokens, SUM(safety_failure) AS safety_failures
            FROM skill_evaluations WHERE skill_name = ? AND version = ?
            """,
            (skill_name, version),
        ).fetchone()
        count = int(row["count"])
        success_rate = float(row["success_rate"] or 0.0)
        average_tokens = float(row["average_tokens"] or 0.0)
        if count < min_evaluations:
            return PromotionDecision(False, "Insufficient evaluation evidence", success_rate, average_tokens)
        if int(row["safety_failures"] or 0) > 0:
            return PromotionDecision(False, "Safety regression detected", success_rate, average_tokens)
        if success_rate < baseline_success_rate:
            return PromotionDecision(False, "Success rate is below baseline", success_rate, average_tokens)
        token_limit = baseline_average_tokens * (1 + max_token_regression)
        if baseline_average_tokens > 0 and average_tokens > token_limit:
            return PromotionDecision(False, "Token cost regression exceeds threshold", success_rate, average_tokens)
        return PromotionDecision(True, "Candidate passed evidence gates", success_rate, average_tokens)

    def promote(self, skill_name: str, version: int, decision: PromotionDecision) -> None:
        if not decision.approved:
            raise ValueError(f"Cannot promote rejected candidate: {decision.reason}")
        with self._connection:
            self._connection.execute(
                "UPDATE skill_versions SET status = ? WHERE skill_name = ? AND status = ?",
                (SkillStatus.DEPRECATED.value, skill_name, SkillStatus.ACTIVE.value),
            )
            self._connection.execute(
                "UPDATE skill_versions SET status = ? WHERE skill_name = ? AND version = ?",
                (SkillStatus.ACTIVE.value, skill_name, version),
            )

    def rollback(self, skill_name: str, version: int) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE skill_versions SET status = ? WHERE skill_name = ? AND status = ?",
                (SkillStatus.DEPRECATED.value, skill_name, SkillStatus.ACTIVE.value),
            )
            updated = self._connection.execute(
                "UPDATE skill_versions SET status = ? WHERE skill_name = ? AND version = ?",
                (SkillStatus.ACTIVE.value, skill_name, version),
            )
            if updated.rowcount != 1:
                raise KeyError(f"Unknown skill version: {skill_name}@{version}")

    def deprecate(self, skill_name: str, version: int) -> None:
        """Disable a version without silently selecting a replacement."""
        with self._connection:
            updated = self._connection.execute(
                "UPDATE skill_versions SET status = ? WHERE skill_name = ? AND version = ?",
                (SkillStatus.DEPRECATED.value, skill_name, version),
            )
            if updated.rowcount != 1:
                raise KeyError(f"Unknown skill version: {skill_name}@{version}")

    def evidence_score(self, skill_name: str) -> float:
        row = self._connection.execute(
            """
            SELECT AVG(e.success) AS rate, COUNT(*) AS count
            FROM skill_versions v JOIN skill_evaluations e
              ON e.skill_name = v.skill_name AND e.version = v.version
            WHERE v.skill_name = ? AND v.status = ?
            """,
            (skill_name, SkillStatus.ACTIVE.value),
        ).fetchone()
        if not row or not row["count"]:
            return 0.0
        return min(0.4, float(row["rate"] or 0.0) * 0.4)

    def active_version(self, skill_name: str) -> int | None:
        row = self._connection.execute(
            "SELECT version FROM skill_versions WHERE skill_name = ? AND status = ?",
            (skill_name, SkillStatus.ACTIVE.value),
        ).fetchone()
        return int(row["version"]) if row else None

    def close(self) -> None:
        self._connection.close()
