from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import crawl_related_sources, dedupe_urls, extract_m3u8_urls, is_valid_http_url


class FakeResponse:
    def __init__(self, url, text):
        self.url = url
        self._text = text.encode("utf-8")
        self.encoding = "utf-8"
        self.headers = {}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        yield self._text


class FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping

    def get(self, url, headers=None, timeout=None, allow_redirects=True, stream=True):
        return FakeResponse(url, self.mapping[url])


def test_extract_m3u8_from_src():
    html = '<video src="https://example.com/live/index.m3u8"></video>'
    assert extract_m3u8_urls(html, "https://example.com/page") == ["https://example.com/live/index.m3u8"]


def test_extract_m3u8_from_href():
    html = '<a href="https://cdn.example.com/path/playlist.m3u8?token=abc">stream</a>'
    assert extract_m3u8_urls(html, "https://example.com/page") == [
        "https://cdn.example.com/path/playlist.m3u8?token=abc"
    ]


def test_extract_m3u8_from_inline_js():
    html = '<script>const streamUrl = "/assets/hls/master.m3u8";</script>'
    assert extract_m3u8_urls(html, "https://example.com/watch/123") == [
        "https://example.com/assets/hls/master.m3u8"
    ]


def test_extract_m3u8_from_escaped_inline_js():
    html = r'<script>const streamUrl = "https:\/\/cdn.example.com\/live\/master.m3u8";</script>'
    assert extract_m3u8_urls(html, "https://example.com/watch/123") == [
        "https://cdn.example.com/live/master.m3u8"
    ]


def test_relative_url_conversion():
    html = '<source data-hls="../hls/playlist.m3u8">'
    assert extract_m3u8_urls(html, "https://example.com/videos/episode/index.html") == [
        "https://example.com/videos/hls/playlist.m3u8"
    ]


def test_deduplication():
    urls = [
        "https://example.com/a.m3u8",
        "https://example.com/a.m3u8",
        "https://example.com/b.m3u8",
    ]
    assert dedupe_urls(urls) == ["https://example.com/a.m3u8", "https://example.com/b.m3u8"]


def test_no_m3u8_found():
    html = "<html><body>No stream here</body></html>"
    assert extract_m3u8_urls(html, "https://example.com") == []


def test_invalid_url_validation():
    assert is_valid_http_url("ftp://example.com/video") is False
    assert is_valid_http_url("example.com/video") is False
    assert is_valid_http_url("https://example.com/video") is True


def test_follow_iframe_to_find_stream():
    html = '<iframe src="https://player.example.com/embed/123"></iframe>'
    session = FakeSession(
        {
            "https://player.example.com/embed/123": '<script>const src="https://cdn.example.com/live/master.m3u8";</script>'
        }
    )
    assert crawl_related_sources(session, html, "https://example.com/page", {"User-Agent": "test"}) == [
        "https://cdn.example.com/live/master.m3u8"
    ]
