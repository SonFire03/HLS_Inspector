from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "hls_inspector.db"
USER_AGENT = "HLSInspector/1.0 (+local-authorized-analysis)"
HTML_MAX_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = 10
MAX_REDIRECTS = 5
ALLOWED_SCAN_STATUSES = {"success", "no_stream_found", "error", "invalid_url"}
TRACE_MAX_ITEMS = 40
DEFAULT_HISTORY_PAGE_SIZE = 10
MAX_HISTORY_PAGE_SIZE = 50

M3U8_ATTRS = ("src", "href", "data-src", "data-url", "data-video", "data-hls")
RESOURCE_ATTRS = ("src", "href", "data-src", "data-url", "data-script", "data-ajax")
EMBED_ATTRS = ("src", "data-src", "data-url")
M3U8_REGEX = re.compile(
    r"""(?P<url>
        (?:
            https?://[^\s"'<>]+?\.m3u8[^\s"'<>]*
            |
            //[^\s"'<>]+?\.m3u8[^\s"'<>]*
            |
            /[^\s"'<>]+?\.m3u8[^\s"'<>]*
            |
            [^\s"'<>]+?\.m3u8[^\s"'<>]*
        )
    )""",
    re.IGNORECASE | re.VERBOSE,
)
RESOURCE_URL_REGEX = re.compile(r"""(?P<url>https?://[^\s"'<>]+|/[^\s"'<>]+)""", re.IGNORECASE)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = str(DEFAULT_DB_PATH)
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    if test_config:
        app.config.update(test_config)

    ensure_database(app.config["DATABASE_PATH"])

    @app.get("/")
    def index() -> str:
        history_view = get_history_view(
            app.config["DATABASE_PATH"],
            page=1,
            per_page=DEFAULT_HISTORY_PAGE_SIZE,
            grouped=True,
        )
        return render_template("index.html", history_view=history_view)

    @app.post("/api/analyze")
    def api_analyze():
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()

        if not is_valid_http_url(url):
            scanned_at = now_iso()
            source_trace = [
                {
                    "stage": "validation",
                    "message": "URL rejetée: schéma HTTP/HTTPS requis.",
                    "url": url,
                }
            ]
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": "URL invalide",
                    "page_url": url,
                    "m3u8_url": None,
                    "status": "invalid_url",
                    "error_message": "L'URL doit commencer par http:// ou https://.",
                    "scanned_at": scanned_at,
                    "source_trace": json.dumps(source_trace, ensure_ascii=False),
                    "source_type": "validation",
                },
            )
            return jsonify(
                {
                    "success": False,
                    "title": "URL invalide",
                    "page_url": url,
                    "streams": [],
                    "status": "invalid_url",
                    "error_message": "L'URL doit commencer par http:// ou https://.",
                    "scanned_at": scanned_at,
                    "trace": source_trace,
                    "source_type": "validation",
                }
            )

        try:
            result = analyze_page(url)
        except AnalysisError as exc:
            scanned_at = now_iso()
            source_trace = exc.trace or [
                {
                    "stage": "error",
                    "message": str(exc),
                    "url": exc.page_url or url,
                }
            ]
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": exc.page_title or "Sans titre",
                    "page_url": exc.page_url or url,
                    "m3u8_url": None,
                    "status": exc.status,
                    "error_message": str(exc),
                    "scanned_at": scanned_at,
                    "source_trace": json.dumps(source_trace, ensure_ascii=False),
                    "source_type": infer_source_type_from_steps(source_trace),
                },
            )
            return jsonify(
                {
                    "success": False,
                    "title": exc.page_title or "Sans titre",
                    "page_url": exc.page_url or url,
                    "streams": [],
                    "status": exc.status,
                    "error_message": str(exc),
                    "scanned_at": scanned_at,
                    "trace": source_trace,
                    "source_type": infer_source_type_from_steps(source_trace),
                }
            )

        scanned_at = now_iso()
        if result["streams"]:
            for stream_url in result["streams"]:
                save_scan(
                    app.config["DATABASE_PATH"],
                    {
                        "page_title": result["title"],
                        "page_url": result["page_url"],
                        "m3u8_url": stream_url,
                        "status": "success",
                        "error_message": None,
                        "scanned_at": scanned_at,
                        "source_trace": json.dumps(result["trace"], ensure_ascii=False),
                        "source_type": result["source_type"],
                    },
                )
        else:
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": result["title"],
                    "page_url": result["page_url"],
                    "m3u8_url": None,
                    "status": "no_stream_found",
                    "error_message": None,
                    "scanned_at": scanned_at,
                    "source_trace": json.dumps(result["trace"], ensure_ascii=False),
                    "source_type": result["source_type"],
                },
            )

        return jsonify(
            {
                "success": bool(result["streams"]),
                "title": result["title"],
                "page_url": result["page_url"],
                "streams": result["streams"],
                "status": "success" if result["streams"] else "no_stream_found",
                "scanned_at": scanned_at,
                "trace": result["trace"],
                "source_type": result["source_type"],
            }
        )

    @app.get("/api/history")
    def api_history():
        page = clamp_int(request.args.get("page"), default=1, minimum=1)
        per_page = clamp_int(
            request.args.get("per_page"),
            default=DEFAULT_HISTORY_PAGE_SIZE,
            minimum=1,
            maximum=MAX_HISTORY_PAGE_SIZE,
        )
        status = (request.args.get("status") or "all").strip()
        search = (request.args.get("search") or "").strip()
        grouped = (request.args.get("grouped") or "1").strip() != "0"
        return jsonify(
            get_history_view(
                app.config["DATABASE_PATH"],
                page=page,
                per_page=per_page,
                grouped=grouped,
                status=status,
                search=search,
            )
        )

    @app.delete("/api/history/<int:entry_id>")
    def api_delete_history_entry(entry_id: int):
        deleted = delete_analysis_group(app.config["DATABASE_PATH"], entry_id)
        return jsonify({"success": deleted, "deleted": deleted})

    @app.delete("/api/history")
    def api_clear_history():
        cleared = clear_history(app.config["DATABASE_PATH"])
        return jsonify({"success": True, "cleared": cleared})

    @app.get("/export/json")
    def export_json():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        payload = json.dumps(rows, ensure_ascii=False, indent=2)
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history.json"},
        )

    @app.get("/export/csv")
    def export_csv():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "id",
                "page_title",
                "page_url",
                "m3u8_url",
                "status",
                "error_message",
                "scanned_at",
                "source_trace",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["id"],
                    row["page_title"],
                    row["page_url"],
                    row["m3u8_url"],
                    row["status"],
                    row["error_message"],
                    row["scanned_at"],
                    row["source_trace"],
                ]
            )
        return Response(
            buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history.csv"},
        )

    @app.get("/export/detail/json")
    def export_detail_json():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        history_items = group_history_rows(rows)
        payload = json.dumps(
            {
                "generated_at": now_iso(),
                "items": history_items,
                "pagination": {"total_items": len(history_items)},
                "filters": {"grouped": True},
            },
            ensure_ascii=False,
            indent=2,
        )
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history_detailed.json"},
        )

    @app.get("/export/detail/csv")
    def export_detail_csv():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        history_items = group_history_rows(rows)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "analysis_id",
                "page_title",
                "page_url",
                "status",
                "source_type",
                "streams",
                "stream_count",
                "error_message",
                "scanned_at",
                "source_trace",
            ]
        )
        for item in history_items:
            writer.writerow(
                [
                    item["id"],
                    item["page_title"],
                    item["page_url"],
                    item["status"],
                    item["source_type"],
                    json.dumps(item["streams"], ensure_ascii=False),
                    item["stream_count"],
                    item["error_message"],
                    item["scanned_at"],
                    item["source_trace"],
                ]
            )
        return Response(
            buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history_detailed.csv"},
        )

    return app


class AnalysisError(Exception):
    def __init__(self, message: str, status: str = "error", page_url: str | None = None, page_title: str | None = None):
        super().__init__(message)
        self.status = status
        self.page_url = page_url
        self.page_title = page_title
        self.trace: list[dict] = []


def clamp_int(value: str | None, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def ensure_database(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_title TEXT,
                page_url TEXT NOT NULL,
                m3u8_url TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                source_trace TEXT,
                source_type TEXT,
                scanned_at TEXT NOT NULL
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(scans)")}
        if "source_trace" not in existing_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN source_trace TEXT")
        if "source_type" not in existing_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN source_type TEXT")
        conn.commit()


def get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def save_scan(db_path: str, scan: dict) -> None:
    if scan["status"] not in ALLOWED_SCAN_STATUSES:
        raise ValueError(f"Invalid status: {scan['status']}")
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO scans (page_title, page_url, m3u8_url, status, error_message, source_trace, source_type, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan.get("page_title"),
                scan.get("page_url"),
                scan.get("m3u8_url"),
                scan.get("status"),
                scan.get("error_message"),
                scan.get("source_trace"),
                scan.get("source_type"),
                scan.get("scanned_at"),
            ),
        )
        conn.commit()


def fetch_history_rows(
    db_path: str,
    *,
    limit: int | None = 100,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
) -> list[dict]:
    query = (
        "SELECT id, page_title, page_url, m3u8_url, status, error_message, source_trace, source_type, scanned_at "
        "FROM scans"
    )
    where: list[str] = []
    params: list[object] = []

    if status and status != "all":
        where.append("status = ?")
        params.append(status)

    if search:
        like = f"%{search}%"
        where.append(
            "("
            "page_title LIKE ? OR page_url LIKE ? OR m3u8_url LIKE ? OR status LIKE ? OR error_message LIKE ? OR source_trace LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like])

    if where:
        query += " WHERE " + " AND ".join(where)

    query += " ORDER BY scanned_at DESC, id DESC"

    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_history_view(
    db_path: str,
    *,
    page: int = 1,
    per_page: int = DEFAULT_HISTORY_PAGE_SIZE,
    grouped: bool = True,
    status: str = "all",
    search: str = "",
) -> dict:
    rows = fetch_history_rows(db_path, limit=None, status=status, search=search or None)
    items = group_history_rows(rows) if grouped else rows
    total_items = len(items)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    paged_items = items[start : start + per_page]
    return {
        "items": paged_items,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        },
        "filters": {
            "status": status,
            "search": search,
            "grouped": grouped,
        },
    }


def delete_analysis_group(db_path: str, entry_id: int) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT page_url, scanned_at FROM scans WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return False
        cursor = conn.execute(
            "DELETE FROM scans WHERE page_url = ? AND scanned_at = ?",
            (row["page_url"], row["scanned_at"]),
        )
        conn.commit()
        return cursor.rowcount > 0


def clear_history(db_path: str) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM scans")
        conn.commit()
        return cursor.rowcount


def group_history_rows(rows: list[dict]) -> list[dict]:
    grouped: list[dict] = []
    buckets: dict[tuple[str, str], dict] = {}

    for row in rows:
        key = (row["page_url"], row["scanned_at"])
        group = buckets.get(key)
        if group is None:
            group = {
                "id": row["id"],
                "page_title": row["page_title"],
                "page_url": row["page_url"],
                "status": row["status"],
                "error_message": row["error_message"],
                "scanned_at": row["scanned_at"],
                "source_trace": row.get("source_trace"),
                "source_type": row.get("source_type") or "unknown",
                "streams": [],
                "entries": [],
            }
            buckets[key] = group
            grouped.append(group)

        group["entries"].append(
            {
                "id": row["id"],
                "m3u8_url": row["m3u8_url"],
                "status": row["status"],
                "source_type": row.get("source_type") or infer_source_type(row.get("source_trace")),
            }
        )
        if row["m3u8_url"]:
            group["streams"].append(row["m3u8_url"])
        if not group.get("source_trace") and row.get("source_trace"):
            group["source_trace"] = row["source_trace"]
        if group.get("source_type") in {None, "unknown"}:
            group["source_type"] = row.get("source_type") or infer_source_type(row.get("source_trace"))

    for group in grouped:
        group["streams"] = dedupe_urls(group["streams"])
        group["stream_count"] = len(group["streams"])
        group["source_type"] = group.get("source_type") or infer_source_type(group.get("source_trace"))
        group["source_label"] = source_type_label(group["source_type"])

    return grouped


def parse_trace(raw_trace: str | None) -> list[dict]:
    if not raw_trace:
        return []
    try:
        parsed = json.loads(raw_trace)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def infer_source_type(raw_trace: str | None) -> str:
    trace = parse_trace(raw_trace)
    return infer_source_type_from_steps(trace)


def infer_source_type_from_steps(trace: list[dict]) -> str:
    if not trace:
        return "unknown"

    for step in trace:
        if step.get("stage") == "direct_m3u8":
            return "direct"

    for step in trace:
        kind = step.get("kind")
        if kind in {"iframe", "resource"}:
            return kind

    for step in trace:
        stage = step.get("stage")
        if stage == "validation":
            return "validation"
        if stage == "error":
            return "error"

    return "unknown"


def source_type_label(source_type: str) -> str:
    labels = {
        "direct": "Direct",
        "iframe": "Iframe",
        "resource": "Ressource",
        "validation": "Validation",
        "error": "Erreur",
        "unknown": "Inconnue",
    }
    return labels.get(source_type, "Inconnue")


def is_valid_http_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def analyze_page(page_url: str) -> dict:
    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    trace: list[dict] = [{"stage": "fetch", "url": page_url}]

    try:
        response = session.get(page_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
        response.raise_for_status()
        html_bytes = read_limited_body(response)
    except requests.exceptions.TooManyRedirects as exc:
        error = AnalysisError("Trop de redirections.", page_url=page_url)
        error.trace = trace + [{"stage": "error", "message": "too_many_redirects", "url": page_url}]
        raise error from exc
    except requests.exceptions.RequestException as exc:
        error = AnalysisError(f"Erreur réseau: {exc}", page_url=page_url)
        error.trace = trace + [{"stage": "error", "message": "network_error", "url": page_url}]
        raise error from exc

    html = decode_html(response, html_bytes)
    title = extract_title(html)
    trace.append(
        {
            "stage": "page_fetched",
            "url": response.url,
            "title": title,
            "bytes": len(html_bytes),
        }
    )
    streams = extract_m3u8_urls(html, response.url)
    if streams:
        trace.append({"stage": "direct_m3u8", "count": len(streams), "url": response.url})
    if not streams:
        streams, extra_trace = crawl_related_sources(session, html, response.url, headers)
        trace.extend(extra_trace)
    return {
        "title": title,
        "page_url": response.url,
        "streams": streams,
        "trace": trim_trace(trace),
        "source_type": infer_source_type_from_steps(trace),
    }


def read_limited_body(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > HTML_MAX_BYTES:
        raise AnalysisError("HTML trop volumineux.", page_url=response.url)

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total > HTML_MAX_BYTES:
            raise AnalysisError("HTML trop volumineux.", page_url=response.url)
    return b"".join(chunks)


def decode_html(response: requests.Response, html_bytes: bytes) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return html_bytes.decode(encoding, errors="replace")
    except LookupError:
        return html_bytes.decode("utf-8", errors="replace")


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return og_title["content"].strip() or "Sans titre"

    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)

    return "Sans titre"


def extract_m3u8_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[str] = []

    for tag in soup.find_all(True):
        for attr in M3U8_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str):
                discovered.extend(find_m3u8_candidates(value, base_url))

    discovered.extend(find_m3u8_candidates(html, base_url))
    for script in soup.find_all("script"):
        script_text = script.get_text("\n", strip=False)
        if script_text:
            discovered.extend(find_m3u8_candidates(script_text, base_url))
    return dedupe_urls(discovered)


def crawl_related_sources(
    session: requests.Session, html: str, base_url: str, headers: dict[str, str]
) -> tuple[list[str], list[dict]]:
    discovered: list[str] = []
    visited: set[str] = set()
    trace: list[dict] = []
    queue: list[tuple[str, str, int]] = []

    for iframe_url in collect_embedded_urls(html, base_url):
        queue.append((iframe_url, "iframe", 0))
    if queue:
        trace.append({"stage": "embedded_urls", "count": len(queue), "url": base_url})

    for resource_url in collect_resource_urls(html, base_url):
        queue.append((resource_url, "resource", 0))
    if queue:
        trace.append({"stage": "related_urls", "count": len(queue), "url": base_url})

    while queue and len(visited) < 12 and not discovered:
        target_url, kind, depth = queue.pop(0)
        if target_url in visited or depth > 2:
            continue
        visited.add(target_url)
        trace.append({"stage": "follow", "kind": kind, "url": target_url, "depth": depth})

        if kind == "resource" and not is_text_resource_url(target_url):
            trace.append({"stage": "skip", "kind": kind, "url": target_url, "reason": "non_text_resource"})
            continue

        try:
            response = session.get(
                target_url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
                stream=True,
            )
            response.raise_for_status()
            resource_body = read_limited_body(response)
            resource_text = decode_html(response, resource_body)
        except requests.exceptions.RequestException:
            trace.append({"stage": "fetch_error", "kind": kind, "url": target_url})
            continue
        except AnalysisError:
            trace.append({"stage": "size_error", "kind": kind, "url": target_url})
            continue

        resource_streams = find_m3u8_candidates(resource_text, response.url)
        trace.append(
            {
                "stage": "resource_fetched",
                "kind": kind,
                "url": response.url,
                "bytes": len(resource_body),
                "streams": len(resource_streams),
            }
        )
        discovered.extend(resource_streams)
        if discovered:
            break

        for iframe_url in collect_embedded_urls(resource_text, response.url):
            if iframe_url not in visited:
                queue.append((iframe_url, "iframe", depth + 1))

        for resource_url in collect_resource_urls(resource_text, response.url):
            if resource_url not in visited:
                queue.append((resource_url, "resource", depth + 1))

    if not discovered:
        trace.append({"stage": "no_stream_found", "url": base_url})
    else:
        trace.append({"stage": "stream_found", "count": len(discovered), "url": base_url})
    return dedupe_urls(discovered), trim_trace(trace)


def collect_resource_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[str] = []

    for tag in soup.find_all(True):
        for attr in RESOURCE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str):
                discovered.extend(extract_urls_from_text(value, base_url))

    for script in soup.find_all("script"):
        script_text = script.get_text("\n", strip=False)
        if script_text:
            discovered.extend(extract_urls_from_text(script_text, base_url))

    discovered.extend(extract_urls_from_text(html, base_url))
    return dedupe_urls(discovered)


def collect_embedded_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[str] = []

    for tag in soup.find_all(["iframe", "frame", "embed", "object"]):
        for attr in EMBED_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str):
                normalized = normalize_candidate_url(value.strip(), base_url)
                if normalized:
                    discovered.append(normalized)

    return dedupe_urls(discovered)


def extract_urls_from_text(text: str, base_url: str) -> list[str]:
    text = normalize_search_text(text)
    urls: list[str] = []
    for match in RESOURCE_URL_REGEX.finditer(text):
        candidate = match.group("url").strip().strip('\"\'<>(),;')
        normalized = normalize_candidate_url(candidate, base_url)
        if normalized:
            urls.append(normalized)
    return dedupe_urls(urls)


def is_text_resource_url(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith((".txt", ".js", ".json", ".html", ".htm", ".xml", ".m3u8"))


def find_m3u8_candidates(text: str, base_url: str) -> list[str]:
    text = normalize_search_text(text)
    candidates: list[str] = []
    for match in M3U8_REGEX.finditer(text):
        candidate = match.group("url").strip().strip('\"\'<>(),;')
        normalized = normalize_candidate_url(candidate, base_url)
        if normalized and ".m3u8" in normalized.lower():
            candidates.append(normalized)
    return candidates


def normalize_search_text(text: str) -> str:
    return (
        text.replace("\\/", "/")
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\x2F", "/")
        .replace("\\x2f", "/")
    )


def normalize_candidate_url(candidate: str, base_url: str) -> str | None:
    if candidate.startswith("//"):
        parsed_base = urlparse(base_url)
        candidate = f"{parsed_base.scheme}:{candidate}"
    absolute = urljoin(base_url, candidate)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    return absolute


def dedupe_urls(urls: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def trim_trace(trace: list[dict]) -> list[dict]:
    if len(trace) <= TRACE_MAX_ITEMS:
        return trace
    return trace[: TRACE_MAX_ITEMS - 1] + [{"stage": "trace_truncated", "count": len(trace)}]


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
