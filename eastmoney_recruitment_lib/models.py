"""数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


class FetchError(RuntimeError):
    """Raised when a remote fetch fails."""


@dataclass
class IndustryBoard:
    code: str
    name: str


@dataclass
class CompanyProfile:
    stock_code: str
    stock_name: str
    industry: str
    company_name: str
    website: str | None
    eastmoney_code: str


@dataclass
class RecruitmentInfo:
    publish_date: str | None = None
    position: str | None = None
    job_description: str | None = None
    work_location: str | None = None
    source_title: str | None = None
    source_url: str | None = None


@dataclass
class CompanyRecruitmentResult:
    stock_code: str
    stock_name: str
    company_name: str
    industry: str
    website: str | None
    recruitment_info: list[RecruitmentInfo] = field(default_factory=list)
    error: str | None = None
