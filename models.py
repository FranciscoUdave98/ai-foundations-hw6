"""Structured data types for the Campus Customs vendor desk agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExtractedFacts(BaseModel):
    """The facts pulled from one vendor document. With the design defense on, this is all the model returns."""

    vendor: str | None = Field(description="Vendor company name exactly as the document states it. null if not stated.")
    sku: str | None = Field(description="Campus Customs SKU, e.g. CC-HD-006. null if the document gives none.")
    unit_price: float | None = Field(description="Quoted price per unit in US dollars. null if not stated.")
    lead_time_days: int | None = Field(description="Lead time in days as stated by the vendor. null if not stated.")
    min_order: int | None = Field(description="Minimum order in units. null if not stated.")


class VendorDecision(ExtractedFacts):
    """The final answer for one vendor document (handbook section 13)."""

    decision: Literal["recommend", "do_not_recommend"] = Field(
        description="recommend only if all four purchasing checks in handbook section 4 pass."
    )
    reason: str = Field(description="One sentence that names the purchasing check that decided it.")
