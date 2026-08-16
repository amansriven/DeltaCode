"""Contracts for the repository-scoped dashboard assistant."""

from typing import Literal

from pydantic import Field

from app.control_plane.models import ContractModel


class DashboardCitation(ContractModel):
    kind: Literal["repository", "provider", "migration", "pull_request", "dashboard"]
    label: str = Field(min_length=1, max_length=160)
    href: str = Field(min_length=1, max_length=500, pattern=r"^/")


class DashboardChatAnswer(ContractModel):
    scope_status: Literal["answered", "out_of_scope", "insufficient_context"]
    answer: str = Field(min_length=1, max_length=2400)
    citations: list[DashboardCitation] = Field(max_length=6)
    follow_ups: list[str] = Field(max_length=3)


class DashboardChatRequest(ContractModel):
    thread_id: str | None = Field(default=None, min_length=8, max_length=100)
    message: str = Field(min_length=1, max_length=1200)
    repository_full_names: list[str] = Field(min_length=1, max_length=10)
