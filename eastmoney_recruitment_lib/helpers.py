"""通用辅助函数。"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, quote, urlparse, urlunparse

from .config import BINARY_EXTENSIONS, EXTERNAL_RECRUITMENT_HOST_KEYWORDS


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        url = f"https://{url.lstrip('/')}"
        parsed = urlparse(url)
    normalized = parsed._replace(fragment="")
    path = normalized.path or "/"
    return urlunparse(normalized._replace(path=path))


def is_binary_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in BINARY_EXTENSIONS)


def registrable_domain(hostname: str) -> str:
    host = hostname.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if host.endswith((".com.cn", ".net.cn", ".org.cn", ".gov.cn")):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_site(url: str, root_domain: str) -> bool:
    hostname = urlparse(url).hostname
    if not hostname:
        return False
    domain = registrable_domain(hostname)
    return domain == root_domain


def is_external_recruitment_host(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower()
    return any(keyword in hostname for keyword in EXTERNAL_RECRUITMENT_HOST_KEYWORDS)


def infer_market_prefix(stock_code: str) -> str:
    if stock_code.startswith(("600", "601", "603", "605", "688", "689", "900")):
        return "SH"
    if stock_code.startswith(("000", "001", "002", "003", "200", "300", "301")):
        return "SZ"
    if stock_code.startswith(("4", "8", "92")):
        return "BJ"
    raise ValueError(f"无法识别股票代码所属市场: {stock_code}")


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query_string = "&".join(
        f"{quote(key)}={quote(values[0])}" for key, values in sorted(query.items()) if values
    )
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", query_string, ""))


def normalize_field(value: str | None, limit: int = 240) -> str | None:
    if value is None:
        return None
    value = clean_text(value).strip(" |,，;；:：-")
    if not value:
        return None
    return value[:limit]


def contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
