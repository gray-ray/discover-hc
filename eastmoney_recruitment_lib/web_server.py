"""本地 HTML 控制台服务。"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .service import RecruitmentJobService


class RecruitmentHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:  # type: ignore[override]
        exc_type, exc, _ = sys.exc_info()
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class RecruitmentConsoleHandler(BaseHTTPRequestHandler):
    service: RecruitmentJobService
    web_root: Path

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._redirect("/discover.html")
            return
        if self._serve_static(parsed.path):
            return
        if parsed.path == "/api/industries":
            self._respond_json({"industries": self.service.list_industries()})
            return
        if parsed.path == "/api/events":
            self._respond_sse()
            return
        if parsed.path == "/api/jobs":
            self._respond_json({"jobs": self.service.list_jobs()})
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/")[-1]
            job = self.service.get_job(job_id)
            if job is None:
                self._respond_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._respond_json(job)
            return
        if parsed.path == "/api/artifacts":
            query = parse_qs(parsed.query)
            kind = query.get("kind", ["discover"])[0]
            self._respond_json({"artifacts": self.service.list_artifacts(kind)})
            return
        if parsed.path == "/api/artifact":
            query = parse_qs(parsed.query)
            path_text = query.get("path", [""])[0]
            if not path_text:
                self._respond_json({"error": "missing path"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._respond_json(self.service.get_artifact_payload(path_text))
            return
        if parsed.path == "/api/artifact/export.xlsx":
            query = parse_qs(parsed.query)
            path_text = query.get("path", [""])[0]
            if not path_text:
                self._respond_json({"error": "missing path"}, status=HTTPStatus.BAD_REQUEST)
                return
            payload, filename = self.service.export_artifact_excel(path_text)
            self._respond_bytes(
                payload,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                download_name=filename,
            )
            return
        self._respond_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/jobs/start":
            payload = self._read_json_body()
            if payload is None:
                self._respond_json({"error": "invalid json"}, status=HTTPStatus.BAD_REQUEST)
                return
            job = self.service.start_job(payload)
            self._respond_json(job, status=HTTPStatus.CREATED)
            return
        if parsed.path == "/api/jobs/clear":
            result = self.service.clear_job_records()
            self._respond_json(result)
            return
        if parsed.path == "/api/system/shutdown":
            payload = self._read_json_body()
            if payload is None or payload.get("confirm") is not True:
                self._respond_json({"error": "shutdown confirmation required"}, status=HTTPStatus.BAD_REQUEST)
                return
            self._respond_json({"status": "shutting-down"})
            threading.Thread(target=self._shutdown_server, daemon=True).start()
            return
        if parsed.path == "/api/artifacts/delete":
            payload = self._read_json_body()
            if payload is None:
                self._respond_json({"error": "invalid json"}, status=HTTPStatus.BAD_REQUEST)
                return
            paths = payload.get("paths")
            if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
                self._respond_json({"error": "paths must be a string list"}, status=HTTPStatus.BAD_REQUEST)
                return
            result = self.service.delete_artifacts(paths)
            self._respond_json(result)
            return
        self._respond_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _read_json_body(self) -> dict | None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.end_headers()

    def _respond_html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _respond_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_bytes(
        self,
        payload: bytes,
        *,
        content_type: str,
        download_name: str | None = None,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        if download_name:
            encoded = quote(download_name)
            self.send_header("Content-Disposition", f"attachment; filename=\"artifact.xlsx\"; filename*=UTF-8''{encoded}")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static(self, path_text: str) -> bool:
        safe_path = path_text.lstrip("/") or "discover.html"
        candidate = (self.web_root / safe_path).resolve()
        web_root = self.web_root.resolve()
        if web_root not in candidate.parents and candidate != web_root:
            return False
        if not candidate.is_file():
            return False
        content_type, _ = guess_type(candidate.name)
        payload = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    def _respond_sse(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        version = -1
        while True:
            version = self.service.wait_for_updates(version, timeout=15.0)
            payload = self.service.get_dashboard_state()
            data = json.dumps(payload, ensure_ascii=False)
            message = f"event: snapshot\ndata: {data}\n\n".encode("utf-8")
            try:
                self.wfile.write(message)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                break
            except Exception:
                with suppress(Exception):
                    self.wfile.flush()
                break

    def _shutdown_server(self) -> None:
        with suppress(Exception):
            self.server.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="上市公司招聘洞察")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path.cwd()
    service = RecruitmentJobService(base_dir)
    web_root = base_dir / "web"

    handler = RecruitmentConsoleHandler
    handler.service = service
    handler.web_root = web_root

    server = RecruitmentHTTPServer((args.host, args.port), handler)
    print(f"控制台已启动: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
