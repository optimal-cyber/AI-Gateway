"""Pydantic return types for the compliance MCP tools."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class PointOfContact(BaseModel):
    type: str = ""
    name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None   # redacted unless caller is admin + include_pii
    phone: Optional[str] = None   # redacted unless caller is admin + include_pii


class NaicsEntry(BaseModel):
    code: str
    description: Optional[str] = None
    primary: bool = False


class SamEntity(BaseModel):
    uei: Optional[str] = None
    cage_code: Optional[str] = None
    legal_business_name: Optional[str] = None
    registration_status: Optional[str] = None
    registration_date: Optional[str] = None
    registration_expiration_date: Optional[str] = None
    business_types: List[str] = Field(default_factory=list)
    naics: List[NaicsEntry] = Field(default_factory=list)
    points_of_contact: List[PointOfContact] = Field(default_factory=list)
    pii_included: bool = False
    source: str = "sam.gov entity-information/v3"


class NistControl(BaseModel):
    id: str
    family: str
    title: str
    text: str
    related: List[str] = Field(default_factory=list)
    cmmc_l2_practices: List[str] = Field(default_factory=list)


class Poam(BaseModel):
    id: int
    control_id: str
    weakness_description: str
    severity: str
    status: str
    scheduled_completion_date: Optional[str] = None
    milestones: list = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PoamSummary(BaseModel):
    total: int
    by_severity: dict
    by_status: dict


class CmmcDomain(BaseModel):
    domain: str
    name: str
    total: int
    implemented: int
    partial: int
    not_implemented: int


class CmmcStatus(BaseModel):
    framework: str
    total_practices: int
    implemented: int
    partial: int
    not_implemented: int
    last_assessed: Optional[str] = None
    score_sprs_estimate: Optional[int] = None
    domains: List[CmmcDomain] = Field(default_factory=list)
    disclaimer: str = ""


class FederalRegisterDocument(BaseModel):
    document_number: Optional[str] = None
    title: Optional[str] = None
    type: Optional[str] = None          # Rule | Proposed Rule | Notice | Presidential Document
    abstract: Optional[str] = None
    publication_date: Optional[str] = None
    agencies: List[str] = Field(default_factory=list)
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    source: str = "federalregister.gov api/v1"
