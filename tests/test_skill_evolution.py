"""Stage 6: evidence-gated Skill lifecycle and explicit routing."""

from __future__ import annotations

import pytest

from miniclaude.skills.base import BaseSkill, SkillContext, SkillMeta, SkillResult
from miniclaude.skills.catalog import SkillCatalog
from miniclaude.skills.evolution import EvaluationOutcome, SkillVersionStore
from miniclaude.skills.router import SkillRouter


class DemoSkill(BaseSkill):
    @property
    def meta(self) -> SkillMeta:
        return SkillMeta(name="demo", description="demo workflow", tags=["demo"])

    async def execute(self, context: SkillContext) -> SkillResult:
        return SkillResult(skill_name=self.name, success=True, instructions="follow demo evidence")


def test_candidate_promotion_rejection_and_rollback(tmp_path) -> None:
    store = SkillVersionStore(tmp_path / "skills.db")
    previous = store.ensure_active("demo", "v1")
    candidate = store.register_candidate("demo", "v2")
    for tokens in (90, 100, 110):
        store.record("demo", candidate, EvaluationOutcome(True, tokens, 2, 10.0))

    decision = store.evaluate_promotion(
        "demo", candidate, baseline_success_rate=0.9, baseline_average_tokens=100
    )
    assert decision.approved
    store.promote("demo", candidate, decision)
    assert store.active_version("demo") == candidate
    assert store.evidence_score("demo") == pytest.approx(0.4)
    store.rollback("demo", previous)
    assert store.active_version("demo") == previous
    store.deprecate("demo", previous)
    assert store.active_version("demo") is None

    unsafe = store.register_candidate("demo", "unsafe")
    for _ in range(3):
        store.record("demo", unsafe, EvaluationOutcome(True, 50, 1, 5.0, True))
    rejected = store.evaluate_promotion(
        "demo", unsafe, baseline_success_rate=0.9, baseline_average_tokens=100
    )
    assert not rejected.approved
    assert "Safety" in rejected.reason
    store.close()


@pytest.mark.asyncio
async def test_explicit_skill_selection() -> None:
    catalog = SkillCatalog()
    skill = DemoSkill()
    catalog.register(skill)

    selected = await SkillRouter(catalog).route("please use $demo now")

    assert selected == [skill]
