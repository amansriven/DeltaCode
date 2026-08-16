"""Strict contracts for pull-request intelligence."""

from typing import Literal

from pydantic import Field

from app.control_plane.models import ContractModel


class PullRequestRisk(ContractModel):
    severity: Literal["high", "medium", "low"]
    title: str = Field(min_length=1, max_length=140)
    detail: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(min_length=1, max_length=4)


class PullRequestReviewFocus(ContractModel):
    path: str | None = Field(default=None, max_length=500)
    title: str = Field(min_length=1, max_length=140)
    detail: str = Field(min_length=1, max_length=600)
    reviewer_question: str = Field(min_length=1, max_length=300)


class PullRequestTestAssessment(ContractModel):
    status: Literal["adequate", "gaps", "unknown"]
    summary: str = Field(min_length=1, max_length=600)
    missing_coverage: list[str] = Field(max_length=5)


class PullRequestConfidence(ContractModel):
    score: float = Field(ge=0, le=1)
    basis: str = Field(min_length=1, max_length=400)


class PullRequestOverview(ContractModel):
    verdict: Literal["low_risk", "review_needed", "high_risk", "insufficient_context"]
    headline: str = Field(min_length=1, max_length=180)
    executive_summary: str = Field(min_length=1, max_length=1200)
    change_summary: list[str] = Field(min_length=1, max_length=6)
    risk_signals: list[PullRequestRisk] = Field(max_length=6)
    review_focus: list[PullRequestReviewFocus] = Field(max_length=6)
    test_assessment: PullRequestTestAssessment
    recommended_actions: list[str] = Field(min_length=1, max_length=6)
    confidence: PullRequestConfidence


class GeneratePullRequestOverviewRequest(ContractModel):
    refresh: bool = False
