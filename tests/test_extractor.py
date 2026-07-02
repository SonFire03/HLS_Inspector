import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import (
    crawl_related_sources,
    dedupe_urls,
    ensure_database,
    extract_m3u8_urls,
    extract_mp4_urls,
    get_analysis_group,
    get_history_view,
    infer_source_type_from_steps,
    is_valid_http_url,
    save_scan,
)


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


def test_extract_mp4_from_src():
    html = '<video src="https://example.com/media/trailer.mp4"></video>'
    assert extract_mp4_urls(html, "https://example.com/page") == ["https://example.com/media/trailer.mp4"]


def test_extract_mp4_from_href():
    html = '<a href="https://cdn.example.com/path/clip.mp4?token=abc">video</a>'
    assert extract_mp4_urls(html, "https://example.com/page") == [
        "https://cdn.example.com/path/clip.mp4?token=abc"
    ]


def test_extract_mp4_from_inline_js():
    html = '<script>const videoUrl = "/assets/videos/movie.mp4";</script>'
    assert extract_mp4_urls(html, "https://example.com/watch/123") == [
        "https://example.com/assets/videos/movie.mp4"
    ]


def test_mp4_relative_url_conversion():
    html = '<source data-url="../video/preview.mp4">'
    assert extract_mp4_urls(html, "https://example.com/videos/episode/index.html") == [
        "https://example.com/videos/video/preview.mp4"
    ]


def test_mp4_deduplication():
    urls = [
        "https://example.com/a.mp4",
        "https://example.com/a.mp4",
        "https://example.com/b.mp4",
    ]
    assert dedupe_urls(urls) == ["https://example.com/a.mp4", "https://example.com/b.mp4"]


def test_no_mp4_found():
    html = "<html><body>No video here</body></html>"
    assert extract_mp4_urls(html, "https://example.com") == []


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
    streams, videos, trace = crawl_related_sources(session, html, "https://example.com/page", {"User-Agent": "test"})
    assert streams == ["https://cdn.example.com/live/master.m3u8"]
    assert videos == []
    assert any(step["stage"] == "resource_fetched" for step in trace)


def test_follow_resource_to_find_mp4():
    html = '<iframe src="https://player.example.com/embed/456"></iframe>'
    session = FakeSession(
        {
            "https://player.example.com/embed/456": '<script>const src="/media/trailer.mp4";</script>'
        }
    )
    streams, videos, trace = crawl_related_sources(session, html, "https://example.com/page", {"User-Agent": "test"})
    assert streams == []
    assert videos == ["https://player.example.com/media/trailer.mp4"]
    assert any(step["stage"] == "resource_fetched" for step in trace)


def test_infer_source_type_from_trace():
    assert infer_source_type_from_steps([{"stage": "direct_m3u8"}]) == "direct"
    assert infer_source_type_from_steps([{"stage": "follow", "kind": "iframe"}]) == "iframe"
    assert infer_source_type_from_steps([{"stage": "follow", "kind": "resource"}]) == "resource"


def test_grouped_history_pagination():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "history.db")
        ensure_database(db_path)
        save_scan(
            db_path,
            {
                "page_title": "Page A",
                "page_url": "https://example.com/a",
                "m3u8_url": "https://cdn.example.com/a1.m3u8",
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T10:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_m3u8"}]),
                "source_type": "direct",
            },
        )
        save_scan(
            db_path,
            {
                "page_title": "Page A",
                "page_url": "https://example.com/a",
                "m3u8_url": "https://cdn.example.com/a2.m3u8",
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T10:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_m3u8"}]),
                "source_type": "direct",
            },
        )
        save_scan(
            db_path,
            {
                "page_title": "Page B",
                "page_url": "https://example.com/b",
                "m3u8_url": None,
                "status": "no_stream_found",
                "error_message": None,
                "scanned_at": "2026-07-02T11:00:00+02:00",
                "source_trace": json.dumps([{"stage": "no_stream_found"}]),
                "source_type": "unknown",
            },
        )

        history_view = get_history_view(db_path, page=1, per_page=1, grouped=True)
        assert history_view["pagination"]["total_items"] == 2
        assert history_view["pagination"]["total_pages"] == 2
        assert len(history_view["items"]) == 1
        assert history_view["items"][0]["stream_count"] in {0, 2}
        assert "video_count" in history_view["items"][0]


def test_analysis_group_detail():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "detail.db")
        ensure_database(db_path)
        save_scan(
            db_path,
            {
                "page_title": "Detail Page",
                "page_url": "https://example.com/detail",
                "m3u8_url": "https://cdn.example.com/detail.m3u8",
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T12:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_m3u8"}]),
                "source_type": "direct",
            },
        )
        item = get_analysis_group(db_path, 1)
        assert item is not None
        assert item["page_title"] == "Detail Page"
        assert item["trace_steps"][0]["stage"] == "direct_m3u8"


def test_grouped_history_includes_videos():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "videos.db")
        ensure_database(db_path)
        save_scan(
            db_path,
            {
                "page_title": "Video Page",
                "page_url": "https://example.com/video",
                "m3u8_url": None,
                "mp4_url": "https://cdn.example.com/video.mp4",
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T13:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_mp4"}]),
                "source_type": "direct",
            },
        )
        history_view = get_history_view(db_path, page=1, per_page=10, grouped=True)
        item = history_view["items"][0]
        assert item["videos"] == ["https://cdn.example.com/video.mp4"]
        assert item["video_count"] == 1


def test_history_media_filter():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "filter.db")
        ensure_database(db_path)
        save_scan(
            db_path,
            {
                "page_title": "Streams Only",
                "page_url": "https://example.com/streams",
                "m3u8_url": "https://cdn.example.com/only.m3u8",
                "mp4_url": None,
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T14:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_m3u8"}]),
                "source_type": "direct",
            },
        )
        save_scan(
            db_path,
            {
                "page_title": "Videos Only",
                "page_url": "https://example.com/videos",
                "m3u8_url": None,
                "mp4_url": "https://cdn.example.com/only.mp4",
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T15:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_mp4"}]),
                "source_type": "direct",
            },
        )
        save_scan(
            db_path,
            {
                "page_title": "Both",
                "page_url": "https://example.com/both",
                "m3u8_url": "https://cdn.example.com/both.m3u8",
                "mp4_url": "https://cdn.example.com/both.mp4",
                "status": "success",
                "error_message": None,
                "scanned_at": "2026-07-02T16:00:00+02:00",
                "source_trace": json.dumps([{"stage": "direct_m3u8"}, {"stage": "direct_mp4"}]),
                "source_type": "direct",
            },
        )

        streams_only = get_history_view(db_path, page=1, per_page=10, grouped=True, media="streams")
        videos_only = get_history_view(db_path, page=1, per_page=10, grouped=True, media="videos")
        both = get_history_view(db_path, page=1, per_page=10, grouped=True, media="both")

        assert [item["page_title"] for item in streams_only["items"]] == ["Streams Only"]
        assert [item["page_title"] for item in videos_only["items"]] == ["Videos Only"]
        assert [item["page_title"] for item in both["items"]] == ["Both"]
