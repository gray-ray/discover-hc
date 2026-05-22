"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import CompanyRecruitmentResult
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="上市公司招聘洞察")
    parser.add_argument("industry", help="行业名，例如：白酒、面板、小金属")
    parser.add_argument("--company-limit", type=int, default=20, help="最多处理多少家公司，默认 20")
    parser.add_argument("--page-limit", type=int, default=15, help="每个官网最多抓取多少个页面，默认 15")
    parser.add_argument("--result-limit", type=int, default=5, help="每家公司最多输出多少条招聘命中，默认 5")
    parser.add_argument("--timeout", type=float, default=120.0, help="单次请求超时秒数，默认 120")
    parser.add_argument("--show-ssl-warning", action="store_true", help="显示 SSL 回退警告")
    parser.add_argument("--quiet", action="store_true", help="关闭运行过程中的进度提示")
    parser.add_argument("--json-out", type=Path, default=Path("output.json"), help="将完整结果写入 JSON 文件，默认 output.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_runtime(show_ssl_warning=args.show_ssl_warning, quiet=args.quiet)

    progress(f"开始处理行业: {args.industry}")
    progress("正在分页获取行业列表...")
    boards = fetch_industry_boards()
    progress(f"行业列表加载完成，共 {len(boards)} 个行业")

    board = select_industry_board(args.industry, boards)
    progress(f"已匹配行业: {board.name} ({board.code})")

    progress("正在分页获取行业成分股...")
    companies = fetch_board_companies(board.code, board.name, company_limit=args.company_limit)
    progress(f"已获取 {len(companies)} 家公司，开始逐家查询官网和社会招聘信息")

    results = []
    total = len(companies)
    for index, company in enumerate(companies, start=1):
        prefix = f"[{index}/{total}] "
        progress(f"{prefix}正在查询公司资料: {company.stock_code} {company.stock_name}")
        try:
            profile = enrich_company_profile(company)
            if profile.website:
                progress(f"{prefix}已找到官网: {profile.website}")
            else:
                progress(f"{prefix}未找到官网，跳过官网招聘抓取")

            result = collect_company_recruitment(
                profile,
                page_limit=args.page_limit,
                result_limit=args.result_limit,
                timeout=args.timeout,
                progress_prefix=prefix,
            )
        except Exception as exc:
            progress(f"{prefix}公司处理失败，已跳过: {exc}")
            result = CompanyRecruitmentResult(
                stock_code=company.stock_code,
                stock_name=company.stock_name,
                company_name=company.company_name,
                industry=company.industry,
                website=None,
                error=str(exc),
            )

        if result.recruitment_info:
            progress(f"{prefix}处理完成，保留 {len(result.recruitment_info)} 条社会招聘信息")
        else:
            progress(f"{prefix}处理完成，结果为空: {result.error or '未命中社会招聘信息'}")
        results.append(result)

    print(f"匹配行业: {board.name} ({board.code})")
    print_summary(results)

    progress("正在写入 JSON 结果...")
    args.json_out.write_text(
        json.dumps([serialize_result(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    progress("JSON 写入完成")
    print("\n处理完成")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
