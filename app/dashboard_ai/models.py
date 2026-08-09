from typing import Literal

from pydantic import BaseModel, Field


class TriagePriority(BaseModel):
    run_id: int = Field(gt=0)
    urgency: Literal["high", "medium", "low"]
    title: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=320)


class TriageBrief(BaseModel):
    headline: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)
    priorities: list[TriagePriority] = Field(max_length=3)
    watch_items: list[str] = Field(max_length=3)


class TriageBriefData(TriageBrief):
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
