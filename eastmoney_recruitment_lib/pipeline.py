"""核心抓取流程。"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import quote, urljoin, urlparse, urlunparse

from .config import (
    DATE_PATTERNS,
    DESCRIPTION_PATTERNS,
    FOLLOW_HINT_KEYWORDS,
    LOCATION_PATTERNS,
    POSITION_PATTERNS,
    SOCIAL_RECRUITMENT_NEGATIVE_KEYWORDS,
    SOCIAL_RECRUITMENT_POSITIVE_KEYWORDS,
    STRONG_RECRUITMENT_KEYWORDS,
)
from .helpers import (
    canonicalize_url,
    clean_text,
    contains_any_keyword,
    infer_market_prefix,
    is_binary_url,
    is_external_recruitment_host,
    normalize_field,
    normalize_url,
    registrable_domain,
    same_site,
)
from .html_parser import LinkAndTextParser
from .http_client import fetch_json, fetch_text
from .models import CompanyProfile, CompanyRecruitmentResult, IndustryBoard, RecruitmentInfo
from .runtime import progress


def fetch_clist_rows(
    *,
    fs: str,
    fields: str,
    limit: int | None = None,
    page_size: int = 100,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    page = 1

    while True:
        current_page_size = page_size
        if limit is not None:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            current_page_size = min(page_size, remaining)

        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            f"pn={page}&pz={current_page_size}&po=1&np=1&fltt=2&invt=2&fid=f3&fs={quote(fs)}"
            f"&fields={fields}"
        )
        payload = fetch_json(url)
        data = payload.get("data", {}) or {}
        diff = data.get("diff") or []
        if not diff:
            break

        rows.extend(diff)

        total = int(data.get("total") or 0)
        if limit is not None and len(rows) >= limit:
            break
        if total and len(rows) >= total:
            break
        if len(diff) < current_page_size:
            break
        page += 1

    return rows[:limit] if limit is not None else rows


def fetch_industry_boards() -> list[IndustryBoard]:
    rows = fetch_clist_rows(fs="m:90+t:2", fields="f12,f14")
    return [IndustryBoard(code=row["f12"], name=row["f14"]) for row in rows if row.get("f12") and row.get("f14")]


def select_industry_board(industry_name: str, boards: Iterable[IndustryBoard]) -> IndustryBoard:
    query = industry_name.strip().lower()
    candidates: list[tuple[int, int, IndustryBoard]] = []
    for idx, board in enumerate(boards):
        name = board.name.lower()
        if name == query:
            score = 0
        elif query in name:
            score = 1
        else:
            continue
        candidates.append((score, idx, board))

    if not candidates:
        raise ValueError(f"未找到行业 `{industry_name}`。可先确认行业名是否与东财行业一致。")

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def fetch_board_companies(board_code: str, industry_name: str, company_limit: int | None) -> list[CompanyProfile]:
    rows = fetch_clist_rows(
        fs=f"b:{board_code}+f:!50",
        fields="f12,f14",
        limit=company_limit,
    )
    profiles: list[CompanyProfile] = []
    for row in rows:
        stock_code = row.get("f12")
        stock_name = row.get("f14")
        if not stock_code or not stock_name:
            continue
        eastmoney_code = f"{infer_market_prefix(stock_code)}{stock_code}"
        profiles.append(
            CompanyProfile(
                stock_code=stock_code,
                stock_name=stock_name,
                industry=industry_name,
                company_name=stock_name,
                website=None,
                eastmoney_code=eastmoney_code,
            )
        )
    return profiles


def enrich_company_profile(profile: CompanyProfile) -> CompanyProfile:
    url = (
        "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?"
        f"code={profile.eastmoney_code}"
    )
    payload = fetch_json(url)
    rows = payload.get("jbzl", [])
    if not rows:
        return profile
    row = rows[0]
    company_name = row.get("ORG_NAME") or profile.company_name
    website = (row.get("ORG_WEB") or "").strip() or None
    if website:
        website = normalize_url(website)
    return CompanyProfile(
        stock_code=profile.stock_code,
        stock_name=profile.stock_name,
        industry=profile.industry,
        company_name=company_name,
        website=website,
        eastmoney_code=profile.eastmoney_code,
    )


def extract_page(url: str, timeout: float) -> tuple[str, str, list[tuple[str, str]]]:
    html, final_url = fetch_text(url, timeout=timeout)
    parser = LinkAndTextParser()
    parser.feed(html)
    title = clean_text(parser.title) or clean_text(urlparse(final_url).path.split("/")[-1])
    text = clean_text(" ".join(parser.text_parts))
    return title, text, parser.links


def score_text_for_recruitment(text: str) -> int:
    lowered = text.lower()
    score = 0
    for keyword in STRONG_RECRUITMENT_KEYWORDS:
        if keyword.lower() in lowered:
            score += 1
    return score


def score_social_recruitment_signal(text: str) -> int:
    lowered = text.lower()
    score = 0
    for keyword in SOCIAL_RECRUITMENT_POSITIVE_KEYWORDS:
        if keyword.lower() in lowered:
            score += 2
    for keyword in STRONG_RECRUITMENT_KEYWORDS:
        if keyword.lower() in lowered:
            score += 1
    for keyword in SOCIAL_RECRUITMENT_NEGATIVE_KEYWORDS:
        if keyword.lower() in lowered:
            score -= 3
    return score


def score_link(url: str, text: str) -> int:
    haystack = f"{url} {text}".lower()
    score = 0
    for keyword in FOLLOW_HINT_KEYWORDS:
        if keyword.lower() in haystack:
            score += 3 if keyword in STRONG_RECRUITMENT_KEYWORDS else 1
    return score


def extract_date(text: str) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def build_snippet(text: str) -> str:
    snippets: list[str] = []
    lowered = text.lower()
    for keyword in STRONG_RECRUITMENT_KEYWORDS:
        needle = keyword.lower()
        idx = lowered.find(needle)
        if idx < 0:
            continue
        start = max(0, idx - 45)
        end = min(len(text), idx + len(keyword) + 85)
        snippet = clean_text(text[start:end]).strip(" -|")
        if snippet and snippet not in snippets:
            snippets.append(snippet)
        if len(snippets) >= 2:
            break
    if snippets:
        return " ... ".join(snippets)
    return clean_text(text[:180])


def extract_by_patterns(text: str, patterns: tuple[re.Pattern[str], ...], limit: int = 240) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return normalize_field(match.group(1), limit=limit)
    return None


def infer_position(title: str, text: str) -> str | None:
    position = extract_by_patterns(f"{title} {text[:400]}", POSITION_PATTERNS, limit=80)
    if position:
        return position

    cleaned_title = clean_text(title)
    if any(keyword.lower() in cleaned_title.lower() for keyword in STRONG_RECRUITMENT_KEYWORDS):
        parts = re.split(r"[-_|｜/]", cleaned_title)
        for part in parts:
            part = normalize_field(part, limit=80)
            if part and not any(keyword.lower() in part.lower() for keyword in STRONG_RECRUITMENT_KEYWORDS):
                return part
        return cleaned_title[:80]
    return None


def infer_work_location(text: str) -> str | None:
    return extract_by_patterns(text, LOCATION_PATTERNS, limit=120)


def infer_job_description(text: str) -> str | None:
    description = extract_by_patterns(text, DESCRIPTION_PATTERNS, limit=260)
    if description:
        return description
    snippet = build_snippet(text)
    return normalize_field(snippet, limit=260)


def build_recruitment_info(title: str, url: str, text: str) -> RecruitmentInfo:
    return RecruitmentInfo(
        publish_date=extract_date(f"{title} {text[:500]}"),
        position=infer_position(title, text),
        job_description=infer_job_description(text),
        work_location=infer_work_location(text),
        source_title=normalize_field(title, limit=120),
        source_url=url,
    )


def is_social_recruitment_page(title: str, url: str, text: str, info: RecruitmentInfo) -> bool:
    haystack = " ".join(
        part
        for part in (
            title,
            url,
            text[:4000],
            info.position or "",
            info.job_description or "",
            info.work_location or "",
        )
        if part
    )
    positive_signal = score_social_recruitment_signal(haystack)
    if positive_signal < 2:
        return False

    if contains_any_keyword(haystack, SOCIAL_RECRUITMENT_NEGATIVE_KEYWORDS):
        has_strong_positive = contains_any_keyword(haystack, SOCIAL_RECRUITMENT_POSITIVE_KEYWORDS)
        has_job_fields = bool(info.position or info.work_location)
        if not (has_strong_positive and has_job_fields):
            return False

    if info.position:
        bad_position_keywords = (
            "新闻",
            "公告",
            "人才",
            "大会",
            "活动",
            "宣讲",
            "校招",
            "校园",
        )
        if contains_any_keyword(info.position, bad_position_keywords):
            return False

    if not info.position and not contains_any_keyword(haystack, SOCIAL_RECRUITMENT_POSITIVE_KEYWORDS):
        return False

    return True


def serialize_result(result: CompanyRecruitmentResult) -> dict[str, object]:
    return {
        "company": {
            "stock_code": result.stock_code,
            "stock_name": result.stock_name,
            "company_name": result.company_name,
            "industry": result.industry,
            "website": result.website,
        },
        "recruitment_info": [
            {
                "publish_date": info.publish_date,
                "position": info.position,
                "job_description": info.job_description,
                "work_location": info.work_location,
                "source_title": info.source_title,
                "source_url": info.source_url,
            }
            for info in result.recruitment_info
        ],
        "error": result.error,
    }


def candidate_links(
    page_url: str,
    links: list[tuple[str, str]],
    root_domain: str,
    limit: int,
) -> list[tuple[int, str]]:
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for href, text in links:
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            continue
        if is_binary_url(absolute):
            continue

        link_score = score_link(absolute, text)
        same_domain = same_site(absolute, root_domain)
        external_recruitment = is_external_recruitment_host(absolute)
        if not same_domain and not external_recruitment and link_score < 3:
            continue

        canonical = canonicalize_url(absolute)
        if canonical in seen:
            continue
        seen.add(canonical)
        score = score_link(canonical, text)
        if external_recruitment:
            score += 6
        ranked.append((score, canonical))

    ranked.sort(key=lambda item: (-item[0], len(item[1])))
    if not ranked:
        return []

    selected = [item for item in ranked if item[0] > 0]
    if len(selected) < limit:
        remainder = [item for item in ranked if item not in selected]
        selected.extend(remainder[: limit - len(selected)])
    return selected[:limit]


def crawl_recruitment_pages(
    website: str,
    page_limit: int,
    result_limit: int,
    timeout: float,
) -> list[RecruitmentInfo]:
    root_domain = registrable_domain(urlparse(website).hostname or "")
    queue: list[tuple[int, int, str]] = [(10, 0, canonicalize_url(website))]
    seen: set[str] = set()
    hits: list[RecruitmentInfo] = []
    hit_keys: set[tuple[str | None, str | None]] = set()

    while queue and len(seen) < page_limit:
        queue.sort(key=lambda item: (-item[0], item[1], len(item[2])))
        score, depth, current_url = queue.pop(0)
        if current_url in seen:
            continue
        seen.add(current_url)

        try:
            title, text, links = extract_page(current_url, timeout=timeout)
        except Exception:
            continue

        page_text = f"{title} {current_url} {text}"
        relevance = score_text_for_recruitment(page_text)
        if relevance > 0:
            info = build_recruitment_info(title or current_url, current_url, text)
            key = (info.position, info.source_url)
            if (
                (info.position or info.job_description or info.work_location)
                and is_social_recruitment_page(title, current_url, text, info)
                and key not in hit_keys
            ):
                hits.append(info)
                hit_keys.add(key)
                if len(hits) >= result_limit:
                    break

        if depth >= 2:
            continue

        for next_score, next_url in candidate_links(current_url, links, root_domain, limit=12):
            if next_url in seen:
                continue
            queue.append((next_score, depth + 1, next_url))

    hits.sort(key=lambda item: (item.publish_date is None, item.publish_date or "", item.position or "", item.source_title or ""))
    return hits[:result_limit]


def collect_company_recruitment(
    profile: CompanyProfile,
    page_limit: int,
    result_limit: int,
    timeout: float,
    progress_prefix: str = "",
) -> CompanyRecruitmentResult:
    if not profile.website:
        return CompanyRecruitmentResult(
            stock_code=profile.stock_code,
            stock_name=profile.stock_name,
            company_name=profile.company_name,
            industry=profile.industry,
            website=None,
            error="公司资料未提供官网地址",
        )

    attempts = [profile.website]
    parsed = urlparse(profile.website)
    if parsed.scheme == "https":
        attempts.append(urlunparse(("http", parsed.netloc, parsed.path, "", parsed.query, "")))

    last_error: str | None = None
    for attempt_index, website in enumerate(attempts, start=1):
        progress(f"{progress_prefix}正在抓取社会招聘页面: {website} (尝试 {attempt_index}/{len(attempts)})")
        try:
            hits = crawl_recruitment_pages(website, page_limit=page_limit, result_limit=result_limit, timeout=timeout)
            if hits:
                progress(f"{progress_prefix}抓取完成，命中 {len(hits)} 条社会招聘信息")
            else:
                progress(f"{progress_prefix}官网可访问，但当前未命中明确的社会招聘信息")
            return CompanyRecruitmentResult(
                stock_code=profile.stock_code,
                stock_name=profile.stock_name,
                company_name=profile.company_name,
                industry=profile.industry,
                website=website,
                recruitment_info=hits,
                error=None if hits else "官网可访问，但未在限定页面内发现明确招聘信息",
            )
        except Exception as exc:
            last_error = str(exc)
            progress(f"{progress_prefix}官网访问失败: {exc}")

    return CompanyRecruitmentResult(
        stock_code=profile.stock_code,
        stock_name=profile.stock_name,
        company_name=profile.company_name,
        industry=profile.industry,
        website=profile.website,
        error=last_error or "官网访问失败",
    )


def print_summary(results: list[CompanyRecruitmentResult]) -> None:
    for result in results:
        print("=" * 80)
        print(f"公司: {result.company_name} ({result.stock_code} {result.stock_name})")
        print(f"行业: {result.industry}")
        print(f"官网: {result.website or '未找到'}")
        if result.error and not result.recruitment_info:
            print(f"状态: {result.error}")
            continue
        print(f"招聘相关信息: {len(result.recruitment_info)}")
        for idx, info in enumerate(result.recruitment_info, start=1):
            print(f"{idx}. 发布日期: {info.publish_date or ''}")
            print(f"   岗位: {info.position or ''}")
            print(f"   岗位描述: {info.job_description or ''}")
            print(f"   工作地址: {info.work_location or ''}")
            print(f"   来源标题: {info.source_title or ''}")
            print(f"   来源链接: {info.source_url or ''}")
