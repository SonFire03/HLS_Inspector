from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = "HLSInspector/1.0 (+local-authorized-analysis)"
HTML_MAX_BYTES = 5 * 1024 * 1024
REQUEST_TIMEOUT = 10
MAX_REDIRECTS = 5
TRACE_MAX_ITEMS = 40

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
MP4_REGEX = re.compile(
    r"""(?P<url>
        (?:
            https?://[^\s"'<>]+?\.mp4[^\s"'<>]*
            |
            //[^\s"'<>]+?\.mp4[^\s"'<>]*
            |
            /[^\s"'<>]+?\.mp4[^\s"'<>]*
            |
            [^\s"'<>]+?\.mp4[^\s"'<>]*
        )
    )""",
    re.IGNORECASE | re.VERBOSE,
)


class AnalysisError(Exception):
    def __init__(self, message: str, status: str = "error", page_url: str | None = None, page_title: str | None = None):
        super().__init__(message)
        self.status = status
        self.page_url = page_url
        self.page_title = page_title
        self.trace: list[dict] = []


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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
    mp4s = extract_mp4_urls(html, response.url)
    if streams:
        trace.append({"stage": "direct_m3u8", "count": len(streams), "url": response.url})
    if mp4s:
        trace.append({"stage": "direct_mp4", "count": len(mp4s), "url": response.url})
    if not streams or not mp4s:
        related_streams, related_mp4s, extra_trace = crawl_related_sources(session, html, response.url, headers)
        if related_streams:
            streams = dedupe_urls(streams + related_streams)
        if related_mp4s:
            mp4s = dedupe_urls(mp4s + related_mp4s)
        trace.extend(extra_trace)
    return {
        "title": title,
        "page_url": response.url,
        "streams": streams,
        "mp4s": mp4s,
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


def extract_mp4_urls(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[str] = []

    for tag in soup.find_all(True):
        for attr in M3U8_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str):
                discovered.extend(find_mp4_candidates(value, base_url))

    discovered.extend(find_mp4_candidates(html, base_url))
    for script in soup.find_all("script"):
        script_text = script.get_text("\n", strip=False)
        if script_text:
            discovered.extend(find_mp4_candidates(script_text, base_url))
    return dedupe_urls(discovered)


def crawl_related_sources(
    session: requests.Session, html: str, base_url: str, headers: dict[str, str]
) -> tuple[list[str], list[str], list[dict]]:
    discovered_m3u8: list[str] = []
    discovered_mp4: list[str] = []
    visited: set[str] = set()
    trace: list[dict] = []
    queue: list[tuple[str, str, int]] = []

    for iframe_url in collect_embedded_urls(html, base_url):
        queue.append((iframe_url, "iframe", 0))
    if queue:
        trace.append({"stage": "embedded_urls", "count": len(queue), "url": base_url})

    for resource_url in collect_resource_urls(html, base_url):
        if not is_media_asset_url(resource_url):
            queue.append((resource_url, "resource", 0))
    if queue:
        trace.append({"stage": "related_urls", "count": len(queue), "url": base_url})

    while queue and len(visited) < 12 and (not discovered_m3u8 or not discovered_mp4):
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
        resource_mp4s = find_mp4_candidates(resource_text, response.url)
        trace.append(
            {
                "stage": "resource_fetched",
                "kind": kind,
                "url": response.url,
                "bytes": len(resource_body),
                "streams": len(resource_streams),
                "videos": len(resource_mp4s),
            }
        )
        discovered_m3u8.extend(resource_streams)
        discovered_mp4.extend(resource_mp4s)
        if discovered_m3u8 and discovered_mp4:
            break

        for iframe_url in collect_embedded_urls(resource_text, response.url):
            if iframe_url not in visited:
                queue.append((iframe_url, "iframe", depth + 1))

        for resource_url in collect_resource_urls(resource_text, response.url):
            if resource_url not in visited and not is_media_asset_url(resource_url):
                queue.append((resource_url, "resource", depth + 1))

    if not discovered_m3u8 and not discovered_mp4:
        trace.append({"stage": "no_stream_found", "url": base_url})
    else:
        trace.append(
            {
                "stage": "stream_found",
                "count": len(discovered_m3u8) + len(discovered_mp4),
                "streams": len(discovered_m3u8),
                "videos": len(discovered_mp4),
                "url": base_url,
            }
        )
    return dedupe_urls(discovered_m3u8), dedupe_urls(discovered_mp4), trim_trace(trace)


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
    return lowered.endswith((".txt", ".js", ".json", ".html", ".htm", ".xml", ".m3u8", ".mp4"))


def is_media_asset_url(url: str) -> bool:
    lowered = url.lower()
    return ".m3u8" in lowered or ".mp4" in lowered


def find_m3u8_candidates(text: str, base_url: str) -> list[str]:
    text = normalize_search_text(text)
    candidates: list[str] = []
    for match in M3U8_REGEX.finditer(text):
        candidate = match.group("url").strip().strip('\"\'<>(),;')
        normalized = normalize_candidate_url(candidate, base_url)
        if normalized and ".m3u8" in normalized.lower():
            candidates.append(normalized)
    return dedupe_urls(candidates)


def find_mp4_candidates(text: str, base_url: str) -> list[str]:
    text = normalize_search_text(text)
    candidates: list[str] = []
    for match in MP4_REGEX.finditer(text):
        candidate = match.group("url").strip().strip('\"\'<>(),;')
        normalized = normalize_candidate_url(candidate, base_url)
        if normalized and ".mp4" in normalized.lower():
            candidates.append(normalized)
    return dedupe_urls(candidates)


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


def infer_source_type_from_steps(trace: list[dict]) -> str:
    if not trace:
        return "unknown"

    for step in trace:
        if step.get("stage") == "direct_mp4":
            return "direct"
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
