"""Structured contracts for the workspace AI briefing."""

from typing import Literal

from pydantic import Field

from app.control_plane.models import ContractModel


class BriefPriority(ContractModel):
    migration_id: str | None = None
    title: str = Field(min_length=1, max_length=140)
    urgency: Literal["critical", "high", "medium", "low"]
    recommended_action: Literal[
        "connect", "scan", "review", "generate", "revise", "publish", "monitor"
    ]
    reason: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(min_length=1, max_length=3)


class PortfolioRisk(ContractModel):
    title: str = Field(min_length=1, max_length=140)
    detail: str = Field(min_length=1, max_length=500)
    affected_migration_ids: list[str] = Field(max_length=10)


class NextAction(ContractModel):
    label: str = Field(min_length=1, max_length=120)
    detail: str = Field(min_length=1, max_length=400)
    migration_id: str | None = None


class WorkspaceBriefData(ContractModel):
    headline: str = Field(min_length=1, max_length=180)
    executive_summary: str = Field(min_length=1, max_length=1200)
    attention_summary: str = Field(min_length=1, max_length=240)
    priorities: list[BriefPriority] = Field(max_length=6)
    portfolio_risks: list[PortfolioRisk] = Field(max_length=4)
    next_actions: list[NextAction] = Field(max_length=5)


class GenerateWorkspaceBriefRequest(ContractModel):
    refresh: bool = False
