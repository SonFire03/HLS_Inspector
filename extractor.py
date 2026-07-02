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
ASSET_RULES: dict[str, str] = {
    "m3u8": "stream",
    "mp4": "video",
    "pdf": "document",
    "doc": "document",
    "docx": "document",
    "xls": "document",
    "xlsx": "document",
    "csv": "document",
    "txt": "document",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "svg": "image",
}
ASSET_EXTENSIONS = tuple(sorted(ASSET_RULES))
ASSET_REGEX = re.compile(
    rf"""(?P<url>
        (?:
            https?://[^\s"'<>]+?\.(?:{'|'.join(map(re.escape, ASSET_EXTENSIONS))})[^\s"'<>]*
            |
            //[^\s"'<>]+?\.(?:{'|'.join(map(re.escape, ASSET_EXTENSIONS))})[^\s"'<>]*
            |
            /[^\s"'<>]+?\.(?:{'|'.join(map(re.escape, ASSET_EXTENSIONS))})[^\s"'<>]*
            |
            [^\s"'<>]+?\.(?:{'|'.join(map(re.escape, ASSET_EXTENSIONS))})[^\s"'<>]*
        )
    )""",
    re.IGNORECASE | re.VERBOSE,
)
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
    assets = extract_assets(html, response.url)
    direct_assets = [asset for asset in assets if asset["source"] == "direct"]
    streams = [asset["url"] for asset in assets if asset["kind"] == "m3u8"]
    videos = [asset["url"] for asset in assets if asset["kind"] == "mp4"]
    if streams:
        trace.append({"stage": "direct_m3u8", "count": len(streams), "url": response.url})
    if videos:
        trace.append({"stage": "direct_mp4", "count": len(videos), "url": response.url})
    if any(asset["kind"] not in {"m3u8", "mp4"} for asset in direct_assets):
        trace.append({"stage": "direct_asset", "count": len(direct_assets), "url": response.url})
    related_assets, extra_trace = crawl_related_sources(session, html, response.url, headers)
    if related_assets:
        assets = dedupe_assets(assets + related_assets)
        streams = [asset["url"] for asset in assets if asset["kind"] == "m3u8"]
        videos = [asset["url"] for asset in assets if asset["kind"] == "mp4"]
    trace.extend(extra_trace)
    return {
        "title": title,
        "page_url": response.url,
        "streams": streams,
        "videos": videos,
        "assets": assets,
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
    return [asset["url"] for asset in extract_assets(html, base_url) if asset["kind"] == "m3u8"]


def extract_mp4_urls(html: str, base_url: str) -> list[str]:
    return [asset["url"] for asset in extract_assets(html, base_url) if asset["kind"] == "mp4"]


def crawl_related_sources(
    session: requests.Session, html: str, base_url: str, headers: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    discovered_assets: list[dict] = []
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

    while queue and len(visited) < 12:
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

        resource_assets = extract_assets(resource_text, response.url)
        trace.append(
            {
                "stage": "resource_fetched",
                "kind": kind,
                "url": response.url,
                "bytes": len(resource_body),
                "assets": len(resource_assets),
            }
        )
        if resource_assets:
            discovered_assets.extend(
                [dict(asset, source=kind) for asset in resource_assets]
            )

        for iframe_url in collect_embedded_urls(resource_text, response.url):
            if iframe_url not in visited:
                queue.append((iframe_url, "iframe", depth + 1))

        for resource_url in collect_resource_urls(resource_text, response.url):
            if resource_url not in visited and not is_media_asset_url(resource_url):
                queue.append((resource_url, "resource", depth + 1))

    if not discovered_assets:
        trace.append({"stage": "no_stream_found", "url": base_url})
    else:
        counts = count_assets(discovered_assets)
        trace.append({"stage": "stream_found", "count": len(discovered_assets), **counts, "url": base_url})
    return dedupe_assets(discovered_assets), trim_trace(trace)


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
    return lowered.endswith((".txt", ".js", ".json", ".html", ".htm", ".xml", ".m3u8", ".csv", ".md"))


def is_media_asset_url(url: str) -> bool:
    return detect_asset_kind(url) is not None


def extract_assets(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    discovered: list[dict] = []

    for tag in soup.find_all(True):
        for attr in sorted(set(M3U8_ATTRS) | set(RESOURCE_ATTRS)):
            value = tag.get(attr)
            if isinstance(value, str):
                discovered.extend(find_asset_candidates(value, base_url))

    discovered.extend(find_asset_candidates(html, base_url))
    for script in soup.find_all("script"):
        script_text = script.get_text("\n", strip=False)
        if script_text:
            discovered.extend(find_asset_candidates(script_text, base_url))
    return dedupe_assets(discovered)


def find_asset_candidates(text: str, base_url: str) -> list[dict]:
    text = normalize_search_text(text)
    candidates: list[dict] = []
    for match in ASSET_REGEX.finditer(text):
        candidate = match.group("url").strip().strip('\"\'<>(),;')
        normalized = normalize_candidate_url(candidate, base_url)
        if not normalized:
            continue
        kind = detect_asset_kind(normalized)
        if not kind:
            continue
        candidates.append({"url": normalized, "kind": kind, "category": asset_category_for_kind(kind), "source": "direct"})
    return dedupe_assets(candidates)


def detect_asset_kind(url: str) -> str | None:
    lowered = url.lower().split("?", 1)[0].split("#", 1)[0]
    for ext in sorted(ASSET_EXTENSIONS, key=len, reverse=True):
        if lowered.endswith(f".{ext}"):
            return ext
    return None


def asset_category_for_kind(kind: str) -> str:
    return ASSET_RULES.get(kind, "other")


def dedupe_assets(assets: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    ordered: list[dict] = []
    for asset in assets:
        url = asset.get("url")
        if url and url not in seen:
            seen.add(url)
            ordered.append(asset)
    return ordered


def count_assets(assets: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for asset in assets:
        category = asset.get("category") or "other"
        counts[category] = counts.get(category, 0) + 1
    return counts


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
        if step.get("stage") == "direct_asset":
            return "direct"
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
