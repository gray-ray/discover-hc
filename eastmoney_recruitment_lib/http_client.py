"""HTTP 请求相关逻辑。"""

from __future__ import annotations

from http.client import HTTPException, IncompleteRead, RemoteDisconnected
import json
import re
import socket
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from .config import USER_AGENT
from .models import FetchError
from . import runtime


TRANSIENT_HTTP_ERRORS = (
    ConnectionError,
    ConnectionResetError,
    EOFError,
    HTTPException,
    IncompleteRead,
    RemoteDisconnected,
    socket.timeout,
    TimeoutError,
)
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.6
EASTMONEY_QUOTE_UT = "fa5fd1943c7b386f172d6893dbfba10b"
EASTMONEY_WEBGUEST_WBP2U = "|0|0|0|web"
EASTMONEY_JSONP_CALLBACK = "jQuery1124"
_LOGGED_FALLBACK_KEYS: set[tuple[str, str]] = set()


def _is_ssl_verification_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if isinstance(exc, URLError):
        return isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError)
    return False


def _is_transient_network_error(exc: BaseException) -> bool:
    if isinstance(exc, TRANSIENT_HTTP_ERRORS):
        return True
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TRANSIENT_HTTP_ERRORS):
            return True
        if isinstance(reason, str):
            lowered = reason.lower()
            return "timed out" in lowered or "connection reset" in lowered
    return False


def _open_url(req: Request, *, timeout: float, context: ssl.SSLContext | None = None) -> tuple[bytes, object, str]:
    with urlopen(req, timeout=timeout, context=context) as response:
        return response.read(), response.headers, response.geturl()


def _open_with_retries(req: Request, *, timeout: float, context: ssl.SSLContext | None = None) -> tuple[bytes, object, str]:
    last_exc: BaseException | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return _open_url(req, timeout=timeout, context=context)
        except HTTPError:
            raise
        except URLError as exc:
            if _is_ssl_verification_error(exc):
                raise
            if not _is_transient_network_error(exc) or attempt == MAX_RETRIES:
                raise
            last_exc = exc
        except TRANSIENT_HTTP_ERRORS as exc:
            if attempt == MAX_RETRIES:
                raise
            last_exc = exc

        time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    assert last_exc is not None
    raise last_exc


def http_request(url: str, timeout: float = 120.0) -> tuple[bytes, object, str]:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Connection": "close",
        },
    )
    try:
        return _open_with_retries(req, timeout=timeout)
    except URLError as exc:
        if _is_ssl_verification_error(exc):
            insecure_context = ssl._create_unverified_context()
            try:
                result = _open_with_retries(req, timeout=timeout, context=insecure_context)
                if runtime.should_show_ssl_warning():
                    print(
                        f"警告: {url} 的 SSL 证书校验失败，已自动回退为不校验证书模式。",
                        file=sys.stderr,
                    )
                return result
            except (HTTPError, URLError, *TRANSIENT_HTTP_ERRORS) as retry_exc:
                raise FetchError(f"请求失败: {url} ({retry_exc})") from retry_exc
        raise FetchError(f"请求失败: {url} ({exc})") from exc
    except (HTTPError, *TRANSIENT_HTTP_ERRORS) as exc:
        raise FetchError(f"请求失败: {url} ({exc})") from exc


def _build_eastmoney_webguest_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc != "push2.eastmoney.com" or parsed.path != "/api/qt/clist/get":
        return None

    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    params.setdefault("np", "1")
    params.setdefault("fltt", "1")
    params.setdefault("invt", "2")
    params.setdefault("dect", "1")
    params.setdefault("timil", "1")
    params["ut"] = EASTMONEY_QUOTE_UT
    params["cb"] = EASTMONEY_JSONP_CALLBACK
    params.setdefault("wbp2u", EASTMONEY_WEBGUEST_WBP2U)
    return urlunparse(
        parsed._replace(
            path="/webguest/api/qt/clist/get",
            query=urlencode(params),
        )
    )


def _decode_json_body(body: bytes, url: str) -> object:
    text = body.decode("utf-8", "ignore").strip()
    if not text:
        raise FetchError(f"请求失败: {url} (返回内容为空，无法解析 JSON)")

    payload_text = text
    if not text.startswith(("{", "[")):
        match = re.match(r"^[^(]+\((.*)\)\s*;?\s*$", text, re.DOTALL)
        if not match:
            preview = text[:120].replace("\n", " ")
            raise FetchError(f"请求失败: {url} (返回内容不是合法 JSON: {preview})")
        payload_text = match.group(1).strip()

    try:
        return json.loads(payload_text)
    except json.JSONDecodeError as exc:
        preview = payload_text[:120].replace("\n", " ")
        raise FetchError(f"请求失败: {url} (返回内容不是合法 JSON: {preview})") from exc


def _log_fallback_once(primary_url: str, fallback_url: str) -> None:
    primary_path = urlparse(primary_url).path
    fallback_path = urlparse(fallback_url).path
    key = (primary_path, fallback_path)
    if key in _LOGGED_FALLBACK_KEYS:
        return
    _LOGGED_FALLBACK_KEYS.add(key)
    runtime.progress(
        f"主列表接口不可用，已自动切换到 Eastmoney 兼容通道: {fallback_path}"
    )


def detect_charset(body: bytes, headers: object) -> str:
    content_type = getattr(headers, "get", lambda *_: None)("Content-Type", "") or ""
    match = re.search(r"charset=([-\w]+)", content_type, re.IGNORECASE)
    if match:
        return match.group(1).strip("'\"")

    head = body[:2000].decode("ascii", "ignore")
    meta = re.search(r"charset=['\"]?([-\w]+)", head, re.IGNORECASE)
    if meta:
        return meta.group(1)
    return "utf-8"


def fetch_text(url: str, timeout: float = 120.0) -> tuple[str, str]:
    body, headers, final_url = http_request(url, timeout=timeout)
    charset = detect_charset(body, headers)
    return body.decode(charset, "ignore"), final_url


def fetch_json(url: str, timeout: float = 120.0) -> object:
    try:
        body, _, final_url = http_request(url, timeout=timeout)
        return _decode_json_body(body, final_url)
    except FetchError as exc:
        fallback_url = _build_eastmoney_webguest_url(url)
        if not fallback_url:
            raise
        body, _, final_url = http_request(fallback_url, timeout=timeout)
        _log_fallback_once(url, fallback_url)
        return _decode_json_body(body, final_url)
