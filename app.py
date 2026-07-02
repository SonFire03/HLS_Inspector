from __future__ import annotations

from db import (
    clear_history,
    delete_analysis_group,
    ensure_database,
    fetch_history_rows,
    get_analysis_group,
    get_history_view,
    group_history_rows,
    infer_source_type_from_steps,
    save_scan,
)
from extractor import (
    AnalysisError,
    analyze_page,
    crawl_related_sources,
    dedupe_urls,
    extract_m3u8_urls,
    infer_source_type_from_steps as infer_source_type_from_steps_extractor,
    is_valid_http_url,
)
from routes import create_app


app = create_app()


__all__ = [
    "app",
    "create_app",
    "AnalysisError",
    "analyze_page",
    "crawl_related_sources",
    "dedupe_urls",
    "ensure_database",
    "extract_m3u8_urls",
    "fetch_history_rows",
    "get_analysis_group",
    "get_history_view",
    "group_history_rows",
    "infer_source_type_from_steps",
    "infer_source_type_from_steps_extractor",
    "is_valid_http_url",
    "save_scan",
    "clear_history",
    "delete_analysis_group",
]


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
