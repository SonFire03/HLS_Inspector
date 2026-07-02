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
        history = get_history(app.config["DATABASE_PATH"], limit=100)
        return render_template("index.html", history=history)

    @app.post("/api/analyze")
    def api_analyze():
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()

        if not is_valid_http_url(url):
            scanned_at = now_iso()
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": "URL invalide",
                    "page_url": url,
                    "m3u8_url": None,
                    "status": "invalid_url",
                    "error_message": "L'URL doit commencer par http:// ou https://.",
                    "scanned_at": scanned_at,
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
                }
            )

        try:
            result = analyze_page(url)
        except AnalysisError as exc:
            scanned_at = now_iso()
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": exc.page_title or "Sans titre",
                    "page_url": exc.page_url or url,
                    "m3u8_url": None,
                    "status": exc.status,
                    "error_message": str(exc),
                    "scanned_at": scanned_at,
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
            }
        )

    @app.get("/api/history")
    def api_history():
        return jsonify({"items": get_history(app.config["DATABASE_PATH"], limit=100)})

    @app.delete("/api/history/<int:entry_id>")
    def api_delete_history_entry(entry_id: int):
        deleted = delete_history_entry(app.config["DATABASE_PATH"], entry_id)
        return jsonify({"success": deleted, "deleted": deleted})

    @app.delete("/api/history")
    def api_clear_history():
        cleared = clear_history(app.config["DATABASE_PATH"])
        return jsonify({"success": True, "cleared": cleared})

    @app.get("/export/json")
    def export_json():
        rows = get_history(app.config["DATABASE_PATH"], limit=None)
        payload = json.dumps(rows, ensure_ascii=False, indent=2)
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history.json"},
        )

    @app.get("/export/csv")
    def export_csv():
        rows = get_history(app.config["DATABASE_PATH"], limit=None)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "page_title", "page_url", "m3u8_url", "status", "error_message", "scanned_at"])
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
                ]
            )
        return Response(
            buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history.csv"},
        )

    return app


class AnalysisError(Exception):
    def __init__(self, message: str, status: str = "error", page_url: str | None = None, page_title: str | None = None):
        super().__init__(message)
        self.status = status
        self.page_url = page_url
        self.page_title = page_title


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
                scanned_at TEXT NOT NULL
            )
            """
        )
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
            INSERT INTO scans (page_title, page_url, m3u8_url, status, error_message, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                scan.get("page_title"),
                scan.get("page_url"),
                scan.get("m3u8_url"),
                scan.get("status"),
                scan.get("error_message"),
                scan.get("scanned_at"),
            ),
        )
        conn.commit()


def get_history(db_path: str, limit: int | None = 100) -> list[dict]:
    query = "SELECT id, page_title, page_url, m3u8_url, status, error_message, scanned_at FROM scans ORDER BY scanned_at DESC, id DESC"
    params: tuple = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def delete_history_entry(db_path: str, entry_id: int) -> bool:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM scans WHERE id = ?", (entry_id,))
        conn.commit()
        return cursor.rowcount > 0


def clear_history(db_path: str) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM scans")
        conn.commit()
        return cursor.rowcount


def is_valid_http_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def analyze_page(page_url: str) -> dict:
    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}

    try:
        response = session.get(page_url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
        response.raise_for_status()
        html_bytes = read_limited_body(response)
    except requests.exceptions.TooManyRedirects as exc:
        raise AnalysisError("Trop de redirections.", page_url=page_url) from exc
    except requests.exceptions.RequestException as exc:
        raise AnalysisError(f"Erreur réseau: {exc}", page_url=page_url) from exc

    html = decode_html(response, html_bytes)
    title = extract_title(html)
    streams = extract_m3u8_urls(html, response.url)
    if not streams:
        streams = crawl_related_sources(session, html, response.url, headers)
    return {"title": title, "page_url": response.url, "streams": streams}


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
) -> list[str]:
    discovered: list[str] = []
    visited: set[str] = set()
    queue: list[tuple[str, str, int]] = []

    for iframe_url in collect_embedded_urls(html, base_url):
        queue.append((iframe_url, "iframe", 0))

    for resource_url in collect_resource_urls(html, base_url):
        queue.append((resource_url, "resource", 0))

    while queue and len(visited) < 12 and not discovered:
        target_url, kind, depth = queue.pop(0)
        if target_url in visited or depth > 2:
            continue
        visited.add(target_url)

        if kind == "resource" and not is_text_resource_url(target_url):
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
            continue
        except AnalysisError:
            continue

        discovered.extend(find_m3u8_candidates(resource_text, response.url))
        if discovered:
            break

        for iframe_url in collect_embedded_urls(resource_text, response.url):
            if iframe_url not in visited:
                queue.append((iframe_url, "iframe", depth + 1))

        for resource_url in collect_resource_urls(resource_text, response.url):
            if resource_url not in visited:
                queue.append((resource_url, "resource", depth + 1))

    return dedupe_urls(discovered)


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


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=False)
