"""任务服务和结果持久化。"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import CompanyProfile, CompanyRecruitmentResult
from .pipeline import (
    collect_company_recruitment,
    enrich_company_profile,
    fetch_board_companies,
    fetch_industry_boards,
    print_summary,
    select_industry_board,
    serialize_result,
)
from .runtime import configure_runtime, progress
from .xlsx_export import build_xlsx


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.strip().lower())
    compact = "-".join(part for part in cleaned.split("-") if part)
    return compact or "job"


def default_discover_filename(industry_name: str) -> str:
    return f"{slugify(industry_name)}-discover.json"


def serialize_profile(profile: CompanyProfile) -> dict[str, Any]:
    return {
        "stock_code": profile.stock_code,
        "stock_name": profile.stock_name,
        "company_name": profile.company_name,
        "industry": profile.industry,
        "website": profile.website,
        "eastmoney_code": profile.eastmoney_code,
    }


def deserialize_profile(payload: dict[str, Any]) -> CompanyProfile:
    return CompanyProfile(
        stock_code=payload["stock_code"],
        stock_name=payload["stock_name"],
        company_name=payload.get("company_name") or payload["stock_name"],
        industry=payload["industry"],
        website=payload.get("website"),
        eastmoney_code=payload.get("eastmoney_code") or "",
    )


@dataclass
class JobConfig:
    mode: str
    industry: str = ""
    company_limit: int | None = 20
    page_limit: int = 15
    result_limit: int = 5
    timeout: float = 120.0
    source_path: str = ""
    output_path: str = ""
    show_ssl_warning: bool = False


@dataclass
class JobRecord:
    job_id: str
    config: JobConfig
    status: str = "queued"
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    artifact_path: str = ""
    source_path: str = ""
    error: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "config": self.config.__dict__,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "artifact_path": self.artifact_path,
            "source_path": self.source_path,
            "error": self.error,
            "summary": self.summary,
            "logs": self.logs[-200:],
        }


class RecruitmentJobService:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.data_dir = base_dir / "console_data"
        self.jobs_dir = self.data_dir / "jobs"
        self.discover_dir = self.data_dir / "discover"
        self.crawl_dir = self.data_dir / "crawl"
        for directory in (self.jobs_dir, self.discover_dir, self.crawl_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._version = 0
        self._jobs: dict[str, JobRecord] = {}

    def list_industries(self) -> list[str]:
        cache_file = self.base_dir / "eastmoney_industries.json"
        if cache_file.exists():
            try:
                payload = json.loads(cache_file.read_text(encoding="utf-8"))
                return list(payload.get("industries") or [])
            except Exception:
                pass
        return [board.name for board in fetch_industry_boards()]

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda item: item.created_at, reverse=True)
        return [job.to_dict() for job in jobs]

    def get_dashboard_state(self) -> dict[str, Any]:
        return {
            "version": self.current_version(),
            "jobs": self.list_jobs(),
            "artifacts": {
                "discover": self.list_artifacts("discover"),
                "crawl": self.list_artifacts("crawl"),
            },
        }

    def current_version(self) -> int:
        with self._lock:
            return self._version

    def wait_for_updates(self, since_version: int, timeout: float = 15.0) -> int:
        with self._condition:
            if self._version > since_version:
                return self._version
            self._condition.wait(timeout=timeout)
            return self._version

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return None
        payload = job.to_dict()
        if job.artifact_path:
            payload["artifact_preview"] = self._read_json_safe(Path(job.artifact_path))
        return payload

    def get_artifact_payload(self, path_text: str) -> dict[str, Any]:
        payload, _ = self._load_json_artifact(path_text)
        return payload

    def export_artifact_excel(self, path_text: str) -> tuple[bytes, str]:
        payload, path = self._load_json_artifact(path_text)
        headers, rows, sheet_name = self._build_export_table(payload)
        return build_xlsx(sheet_name, headers, rows), f"{path.stem}.xlsx"

    def delete_artifacts(self, paths: list[str]) -> dict[str, Any]:
        deleted: list[str] = []
        skipped: list[str] = []
        for path_text in paths:
            try:
                path = self._resolve_artifact_delete_path(path_text)
            except Exception:
                skipped.append(path_text)
                continue
            if not path.exists():
                skipped.append(str(path))
                continue
            path.unlink()
            deleted.append(str(path))
        if deleted:
            self._publish_update()
        return {"deleted": deleted, "skipped": skipped}

    def clear_job_records(self) -> dict[str, Any]:
        deleted: list[str] = []
        kept_running: list[str] = []

        with self._lock:
            removable_ids = [
                job_id for job_id, job in self._jobs.items()
                if job.status != "running"
            ]
            kept_running = [
                job_id for job_id, job in self._jobs.items()
                if job.status == "running"
            ]

            for job_id in removable_ids:
                job_file = self.jobs_dir / f"{job_id}.json"
                if job_file.exists():
                    job_file.unlink()
                deleted.append(job_id)
                self._jobs.pop(job_id, None)

        if deleted:
            self._publish_update()
        return {
            "cleared_job_ids": deleted,
            "kept_running_job_ids": kept_running,
        }

    def list_artifacts(self, kind: str) -> list[dict[str, Any]]:
        directory = self.discover_dir if kind == "discover" else self.crawl_dir
        artifacts: list[dict[str, Any]] = []
        seen_industries: set[tuple[str, str]] = set()
        for path in sorted(directory.glob("*.json"), reverse=True):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if kind == "discover":
                industry = payload.get("industry", {}) or {}
                dedupe_key = (
                    str(industry.get("code") or ""),
                    str(industry.get("name") or ""),
                )
                if dedupe_key in seen_industries:
                    continue
                seen_industries.add(dedupe_key)
            artifacts.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "kind": payload.get("kind", kind),
                    "created_at": payload.get("created_at", ""),
                    "industry": payload.get("industry", {}),
                    "company_count": payload.get("company_count"),
                    "completed_count": payload.get("completed_count"),
                    "source_path": payload.get("source_path", ""),
                }
            )
        return artifacts

    def start_job(self, config_payload: dict[str, Any]) -> dict[str, Any]:
        config = JobConfig(
            mode=str(config_payload.get("mode", "discover")).strip(),
            industry=str(config_payload.get("industry", "")).strip(),
            company_limit=self._to_optional_int(config_payload.get("company_limit"), default=20),
            page_limit=int(config_payload.get("page_limit", 15)),
            result_limit=int(config_payload.get("result_limit", 5)),
            timeout=float(config_payload.get("timeout", 120.0)),
            source_path=str(config_payload.get("source_path", "")).strip(),
            output_path=str(config_payload.get("output_path", "")).strip(),
            show_ssl_warning=bool(config_payload.get("show_ssl_warning", False)),
        )
        job_id = uuid.uuid4().hex[:12]
        job = JobRecord(job_id=job_id, config=config)
        with self._lock:
            self._jobs[job_id] = job
        self._persist_job(job)
        thread = threading.Thread(target=self._run_job, args=(job_id,), daemon=True)
        thread.start()
        return job.to_dict()

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = now_iso()
            self._persist_job(job)

        configure_runtime(
            show_ssl_warning=job.config.show_ssl_warning,
            quiet=True,
            progress_callback=lambda message: self._append_log(job_id, message),
        )
        try:
            if job.config.mode == "discover":
                self._run_discover(job_id)
            elif job.config.mode == "crawl":
                self._run_crawl(job_id)
            elif job.config.mode == "resume":
                self._run_resume(job_id)
            elif job.config.mode == "refresh-failed":
                self._run_refresh_failed(job_id)
            else:
                raise ValueError(f"不支持的模式: {job.config.mode}")
            with self._lock:
                job = self._jobs[job_id]
                job.status = "completed"
                job.finished_at = now_iso()
                self._persist_job(job)
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = "failed"
                job.error = str(exc)
                job.finished_at = now_iso()
                self._persist_job(job)
            self._append_log(job_id, f"任务失败: {exc}")

    def _run_discover(self, job_id: str) -> None:
        job = self._jobs[job_id]
        config = job.config
        progress(f"开始公司名单整理，行业: {config.industry}")
        boards = fetch_industry_boards()
        board = select_industry_board(config.industry, boards)
        progress(f"已匹配行业: {board.name} ({board.code})")
        companies = fetch_board_companies(board.code, board.name, company_limit=config.company_limit)
        progress(f"获取到 {len(companies)} 家公司，开始补全官网信息")

        enriched: list[CompanyProfile] = []
        total = len(companies)
        for index, company in enumerate(companies, start=1):
            prefix = f"[{index}/{total}] "
            progress(f"{prefix}正在补全公司信息: {company.stock_code} {company.stock_name}")
            try:
                profile = enrich_company_profile(company)
            except Exception as exc:
                progress(f"{prefix}补全失败，保留基础信息: {exc}")
                profile = company
            enriched.append(profile)
            if profile.website:
                progress(f"{prefix}官网: {profile.website}")

        artifact_path = self._resolve_output_path(
            kind="discover",
            preferred=config.output_path,
            default_name=default_discover_filename(board.name),
        )
        payload = {
            "kind": "discover",
            "created_at": now_iso(),
            "industry": {"name": board.name, "code": board.code},
            "company_count": len(enriched),
            "companies": [serialize_profile(profile) for profile in enriched],
        }
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._publish_update()

        with self._lock:
            job = self._jobs[job_id]
            job.artifact_path = str(artifact_path)
            job.summary = {
                "industry": board.name,
                "company_count": len(enriched),
            }
            self._persist_job(job)
        progress("公司名单整理完成")

    def _run_crawl(self, job_id: str) -> None:
        job = self._jobs[job_id]
        config = job.config
        companies, source_path, source_meta = self._load_or_discover_source_companies(config)
        artifact_path = self._resolve_output_path(
            kind="crawl",
            preferred=config.output_path,
            default_name=f"{slugify(source_meta['industry_name'])}-crawl-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json",
        )
        with self._lock:
            job.source_path = source_path
            job.artifact_path = str(artifact_path)
            self._persist_job(job)

        progress(f"开始招聘扫描，待处理公司数: {len(companies)}")
        results = self._crawl_profiles(job_id, companies, artifact_path, mode="crawl", source_path=source_path, industry_meta=source_meta)
        with self._lock:
            job = self._jobs[job_id]
            job.summary = {
                "industry": source_meta["industry_name"],
                "company_count": len(companies),
                "completed_count": len(results),
                "hit_count": sum(1 for item in results if item.recruitment_info),
            }
            self._persist_job(job)

    def _run_resume(self, job_id: str) -> None:
        job = self._jobs[job_id]
        crawl_payload, crawl_path = self._load_crawl_artifact(job.config.source_path)
        source_path = str(crawl_payload.get("source_path") or "")
        if not source_path:
            raise ValueError("继续执行需要结果中包含来源信息")
        all_companies, _, source_meta = self._load_discover_artifact(source_path)
        finished_codes = {
            str(item.get("company", {}).get("stock_code", ""))
            for item in crawl_payload.get("results", [])
            if item.get("company", {}).get("stock_code")
        }
        pending = [profile for profile in all_companies if profile.stock_code not in finished_codes]
        progress(f"继续执行已就绪，待处理公司数: {len(pending)}")
        with self._lock:
            job.source_path = str(crawl_path)
            job.artifact_path = str(crawl_path)
            self._persist_job(job)
        existing_results = list(crawl_payload.get("results", []))
        self._crawl_profiles(
            job_id,
            pending,
            crawl_path,
            mode="resume",
            source_path=source_path,
            industry_meta=source_meta,
            existing_results=existing_results,
        )

    def _run_refresh_failed(self, job_id: str) -> None:
        job = self._jobs[job_id]
        crawl_payload, crawl_path = self._load_crawl_artifact(job.config.source_path)
        source_path = str(crawl_payload.get("source_path") or "")
        if not source_path:
            raise ValueError("重试失败需要结果中包含来源信息")
        all_companies, _, source_meta = self._load_discover_artifact(source_path)
        failed_codes = {
            str(item.get("company", {}).get("stock_code", ""))
            for item in crawl_payload.get("results", [])
            if item.get("error")
        }
        pending = [profile for profile in all_companies if profile.stock_code in failed_codes]
        progress(f"失败重试已就绪，待重试公司数: {len(pending)}")
        with self._lock:
            job.source_path = str(crawl_path)
            job.artifact_path = str(crawl_path)
            self._persist_job(job)

        retained = [item for item in crawl_payload.get("results", []) if str(item.get("company", {}).get("stock_code", "")) not in failed_codes]
        self._crawl_profiles(
            job_id,
            pending,
            crawl_path,
            mode="refresh-failed",
            source_path=source_path,
            industry_meta=source_meta,
            existing_results=retained,
        )

    def _crawl_profiles(
        self,
        job_id: str,
        companies: list[CompanyProfile],
        artifact_path: Path,
        *,
        mode: str,
        source_path: str,
        industry_meta: dict[str, Any],
        existing_results: list[dict[str, Any]] | None = None,
    ) -> list[CompanyRecruitmentResult]:
        results_payload = list(existing_results or [])
        results_by_code = {
            str(item.get("company", {}).get("stock_code", "")): item
            for item in results_payload
            if item.get("company", {}).get("stock_code")
        }

        total = len(companies)
        for index, company in enumerate(companies, start=1):
            prefix = f"[{index}/{total}] "
            progress(f"{prefix}正在查询公司资料: {company.stock_code} {company.stock_name}")
            try:
                profile = company if company.website else enrich_company_profile(company)
                if profile.website:
                    progress(f"{prefix}已找到官网: {profile.website}")
                else:
                    progress(f"{prefix}未找到官网，跳过官网招聘抓取")
                result = collect_company_recruitment(
                    profile,
                    page_limit=self._jobs[job_id].config.page_limit,
                    result_limit=self._jobs[job_id].config.result_limit,
                    timeout=self._jobs[job_id].config.timeout,
                    progress_prefix=prefix,
                )
            except Exception as exc:
                progress(f"{prefix}公司处理失败，已跳过: {exc}")
                result = CompanyRecruitmentResult(
                    stock_code=company.stock_code,
                    stock_name=company.stock_name,
                    company_name=company.company_name,
                    industry=company.industry,
                    website=company.website,
                    error=str(exc),
                )

            results_by_code[result.stock_code] = serialize_result(result)
            if result.recruitment_info:
                progress(f"{prefix}处理完成，保留 {len(result.recruitment_info)} 条社会招聘信息")
            else:
                progress(f"{prefix}处理完成，结果为空: {result.error or '未命中社会招聘信息'}")
            self._write_crawl_artifact(
                artifact_path=artifact_path,
                mode=mode,
                source_path=source_path,
                industry_meta=industry_meta,
                results=list(results_by_code.values()),
            )

        return [self._deserialize_result(item) for item in results_by_code.values()]

    def _write_crawl_artifact(
        self,
        *,
        artifact_path: Path,
        mode: str,
        source_path: str,
        industry_meta: dict[str, Any],
        results: list[dict[str, Any]],
    ) -> None:
        payload = {
            "kind": "crawl",
            "mode": mode,
            "created_at": now_iso(),
            "source_path": source_path,
            "industry": {
                "name": industry_meta["industry_name"],
                "code": industry_meta["industry_code"],
            },
            "company_count": len(results),
            "completed_count": len(results),
            "results": results,
        }
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._publish_update()

    def _load_or_discover_source_companies(self, config: JobConfig) -> tuple[list[CompanyProfile], str, dict[str, Any]]:
        if config.source_path:
            return self._load_discover_artifact(config.source_path)
        if not config.industry:
            raise ValueError("新开一轮需要行业名称")
        progress("未提供公司名单，先自动整理公司名单")
        boards = fetch_industry_boards()
        board = select_industry_board(config.industry, boards)
        companies = fetch_board_companies(board.code, board.name, company_limit=config.company_limit)
        enriched: list[CompanyProfile] = []
        total = len(companies)
        for index, company in enumerate(companies, start=1):
            prefix = f"[公司名单 {index}/{total}] "
            progress(f"{prefix}正在补全公司信息: {company.stock_code} {company.stock_name}")
            try:
                profile = enrich_company_profile(company)
            except Exception as exc:
                progress(f"{prefix}补全失败，保留基础信息: {exc}")
                profile = company
            enriched.append(profile)

        artifact_path = self._resolve_output_path(
            kind="discover",
            preferred="",
            default_name=default_discover_filename(board.name),
        )
        payload = {
            "kind": "discover",
            "created_at": now_iso(),
            "industry": {"name": board.name, "code": board.code},
            "company_count": len(enriched),
            "companies": [serialize_profile(profile) for profile in enriched],
        }
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return enriched, str(artifact_path), {"industry_name": board.name, "industry_code": board.code}

    def _load_discover_artifact(self, source_path: str) -> tuple[list[CompanyProfile], str, dict[str, Any]]:
        payload, path = self._load_json_artifact(source_path)
        companies = [deserialize_profile(item) for item in payload.get("companies", [])]
        industry = payload.get("industry", {}) or {}
        meta = {
            "industry_name": str(industry.get("name") or companies[0].industry if companies else ""),
            "industry_code": str(industry.get("code") or ""),
        }
        return companies, str(path), meta

    def _load_crawl_artifact(self, source_path: str) -> tuple[dict[str, Any], Path]:
        payload, path = self._load_json_artifact(source_path)
        if payload.get("kind") != "crawl":
            raise ValueError("当前选择的不是招聘扫描结果")
        return payload, path

    def _load_json_artifact(self, source_path: str) -> tuple[dict[str, Any], Path]:
        if not source_path:
            raise ValueError("缺少 source_path")
        path = Path(source_path)
        if not path.is_absolute():
            path = self.base_dir / path
        if not path.exists():
            raise ValueError(f"文件不存在: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload, path

    def _build_export_table(self, payload: dict[str, Any]) -> tuple[list[str], list[list[str]], str]:
        kind = str(payload.get("kind") or "")
        industry_name = str((payload.get("industry", {}) or {}).get("name") or "")

        if kind == "discover":
            headers = ["行业", "股票代码", "股票简称", "公司名称", "官网", "状态"]
            rows: list[list[str]] = []
            for company in payload.get("companies", []) or []:
                rows.append(
                    [
                        industry_name or str(company.get("industry") or ""),
                        str(company.get("stock_code") or ""),
                        str(company.get("stock_name") or ""),
                        str(company.get("company_name") or company.get("stock_name") or ""),
                        str(company.get("website") or ""),
                        "待抓招聘",
                    ]
                )
            return headers, rows, "公司筛选"

        headers = ["行业", "股票代码", "股票简称", "公司名称", "官网", "状态", "发布日期", "岗位", "工作地点", "岗位描述", "来源标题", "来源链接"]
        rows = []
        for item in payload.get("results", []) or []:
            company = item.get("company", {}) or {}
            recruitment_list = item.get("recruitment_info", []) or []
            status = str(item.get("error") or (f"命中 {len(recruitment_list)} 条" if recruitment_list else "未命中"))
            base = [
                industry_name or str(company.get("industry") or ""),
                str(company.get("stock_code") or ""),
                str(company.get("stock_name") or ""),
                str(company.get("company_name") or company.get("stock_name") or ""),
                str(company.get("website") or ""),
                status,
            ]
            if recruitment_list:
                for info in recruitment_list:
                    rows.append(
                        [
                            *base,
                            str(info.get("publish_date") or ""),
                            str(info.get("position") or ""),
                            str(info.get("work_location") or ""),
                            str(info.get("job_description") or ""),
                            str(info.get("source_title") or ""),
                            str(info.get("source_url") or ""),
                        ]
                    )
            else:
                rows.append([*base, "", "", "", "", "", ""])
        return headers, rows, "招聘获取"

    def _resolve_artifact_delete_path(self, path_text: str) -> Path:
        path = Path(path_text)
        if not path.is_absolute():
            path = self.base_dir / path
        resolved = path.resolve()
        allowed_dirs = (self.discover_dir.resolve(), self.crawl_dir.resolve())
        if not any(parent == resolved.parent for parent in allowed_dirs):
            raise ValueError(f"不允许删除该路径: {resolved}")
        if resolved.suffix.lower() != ".json":
            raise ValueError(f"只允许删除 json 文件: {resolved}")
        return resolved

    def _resolve_output_path(self, *, kind: str, preferred: str, default_name: str) -> Path:
        if preferred:
            path = Path(preferred)
            if not path.is_absolute():
                path = self.base_dir / path
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        directory = self.discover_dir if kind == "discover" else self.crawl_dir
        return directory / default_name

    def _append_log(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(f"{now_iso()} {message}")
            if len(job.logs) > 1000:
                job.logs = job.logs[-1000:]
            self._persist_job(job)

    def _persist_job(self, job: JobRecord) -> None:
        job_file = self.jobs_dir / f"{job.job_id}.json"
        job_file.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._publish_update()

    def _publish_update(self) -> None:
        with self._condition:
            self._version += 1
            self._condition.notify_all()

    def _read_json_safe(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _deserialize_result(self, payload: dict[str, Any]) -> CompanyRecruitmentResult:
        company = payload.get("company", {})
        return CompanyRecruitmentResult(
            stock_code=str(company.get("stock_code", "")),
            stock_name=str(company.get("stock_name", "")),
            company_name=str(company.get("company_name", "")),
            industry=str(company.get("industry", "")),
            website=company.get("website"),
            error=payload.get("error"),
        )

    def _to_optional_int(self, value: Any, default: int | None = None) -> int | None:
        if value in (None, "", 0, "0"):
            return default
        return int(value)
