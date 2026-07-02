from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ALLOWED_SCAN_STATUSES = {"success", "no_stream_found", "error", "invalid_url"}
ASSET_KIND_TO_CATEGORY = {
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
                mp4_url TEXT,
                asset_url TEXT,
                asset_kind TEXT,
                asset_category TEXT,
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
        if "mp4_url" not in existing_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN mp4_url TEXT")
        if "asset_url" not in existing_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN asset_url TEXT")
        if "asset_kind" not in existing_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN asset_kind TEXT")
        if "asset_category" not in existing_columns:
            conn.execute("ALTER TABLE scans ADD COLUMN asset_category TEXT")
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
            INSERT INTO scans (page_title, page_url, m3u8_url, mp4_url, asset_url, asset_kind, asset_category, status, error_message, source_trace, source_type, scanned_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scan.get("page_title"),
                scan.get("page_url"),
                scan.get("m3u8_url"),
                scan.get("mp4_url"),
                scan.get("asset_url"),
                scan.get("asset_kind"),
                scan.get("asset_category"),
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
        "SELECT id, page_title, page_url, m3u8_url, mp4_url, asset_url, asset_kind, asset_category, status, error_message, source_trace, source_type, scanned_at "
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
            "page_title LIKE ? OR page_url LIKE ? OR m3u8_url LIKE ? OR mp4_url LIKE ? OR asset_url LIKE ? OR asset_kind LIKE ? OR asset_category LIKE ? OR status LIKE ? OR error_message LIKE ? OR source_trace LIKE ?"
            ")"
        )
        params.extend([like, like, like, like, like, like, like, like, like, like])

    if where:
        query += " WHERE " + " AND ".join(where)

    query += " ORDER BY scanned_at DESC, id DESC"

    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def parse_trace(raw_trace: str | None) -> list[dict]:
    if not raw_trace:
        return []
    try:
        parsed = json.loads(raw_trace)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def infer_source_type(raw_trace: str | None) -> str:
    from extractor import infer_source_type_from_steps

    return infer_source_type_from_steps(parse_trace(raw_trace))


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


def infer_source_type_from_steps(trace: list[dict]) -> str:
    from extractor import infer_source_type_from_steps as extractor_infer_source_type_from_steps

    return extractor_infer_source_type_from_steps(trace)


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
                "videos": [],
                "documents": [],
                "images": [],
                "other_assets": [],
                "assets": [],
                "entries": [],
            }
            buckets[key] = group
            grouped.append(group)

        asset_url = row.get("asset_url") or row.get("m3u8_url") or row.get("mp4_url")
        asset_kind = row.get("asset_kind")
        asset_category = row.get("asset_category")
        if asset_url and not asset_kind:
            if row.get("m3u8_url"):
                asset_kind = "m3u8"
            elif row.get("mp4_url"):
                asset_kind = "mp4"
        if asset_url and not asset_category:
            asset_category = asset_category_for_kind(asset_kind)

        group["entries"].append(
            {
                "id": row["id"],
                "m3u8_url": row["m3u8_url"],
                "mp4_url": row.get("mp4_url"),
                "asset_url": row.get("asset_url"),
                "asset_kind": row.get("asset_kind"),
                "asset_category": row.get("asset_category"),
                "status": row["status"],
                "source_type": row.get("source_type") or infer_source_type(row.get("source_trace")),
            }
        )
        if row["m3u8_url"]:
            group["streams"].append(row["m3u8_url"])
        if row.get("mp4_url"):
            group["videos"].append(row["mp4_url"])
        if asset_url:
            asset_record = {
                "url": asset_url,
                "kind": asset_kind or "other",
                "category": asset_category or asset_category_for_kind(asset_kind),
            }
            group["assets"].append(asset_record)
            if asset_record["category"] == "document" and asset_record["kind"] not in {"m3u8", "mp4"}:
                group["documents"].append(asset_record["url"])
            elif asset_record["category"] == "image" and asset_record["kind"] not in {"m3u8", "mp4"}:
                group["images"].append(asset_record["url"])
            elif asset_record["kind"] not in {"m3u8", "mp4"}:
                group["other_assets"].append(asset_record["url"])
        if not group.get("source_trace") and row.get("source_trace"):
            group["source_trace"] = row["source_trace"]
        if group.get("source_type") in {None, "unknown"}:
            group["source_type"] = row.get("source_type") or infer_source_type(row.get("source_trace"))

    for group in grouped:
        group["streams"] = dedupe_urls(group["streams"])
        group["videos"] = dedupe_urls(group["videos"])
        group["documents"] = dedupe_urls(group["documents"])
        group["images"] = dedupe_urls(group["images"])
        group["other_assets"] = dedupe_urls(group["other_assets"])
        group["assets"] = dedupe_asset_records(group["assets"])
        group["stream_count"] = len(group["streams"])
        group["video_count"] = len(group["videos"])
        group["document_count"] = len(group["documents"])
        group["image_count"] = len(group["images"])
        group["other_asset_count"] = len(group["other_assets"])
        group["asset_count"] = len(group["assets"])
        group["source_type"] = group.get("source_type") or infer_source_type(group.get("source_trace"))
        group["source_label"] = source_type_label(group["source_type"])

    return grouped


def summarize_history_rows(rows: list[dict], grouped: bool = True) -> dict:
    items = group_history_rows(rows) if grouped else rows
    status_counts: dict[str, int] = {}
    asset_counts = {"stream": 0, "video": 0, "document": 0, "image": 0, "other": 0}
    source_counts: dict[str, int] = {}

    for item in items:
        status = item.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

        asset_counts["stream"] += int(item.get("stream_count", 0) or 0)
        asset_counts["video"] += int(item.get("video_count", 0) or 0)
        asset_counts["document"] += int(item.get("document_count", 0) or 0)
        asset_counts["image"] += int(item.get("image_count", 0) or 0)
        asset_counts["other"] += int(item.get("other_asset_count", 0) or 0)

        source = item.get("source_type") or "unknown"
        source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "total_items": len(items),
        "status_counts": status_counts,
        "asset_counts": asset_counts,
        "source_counts": source_counts,
    }


def dedupe_urls(urls):
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def dedupe_asset_records(assets: list[dict]) -> list[dict]:
    seen: set[str] = set()
    ordered: list[dict] = []
    for asset in assets:
        url = asset.get("url")
        if url and url not in seen:
            seen.add(url)
            ordered.append(asset)
    return ordered


def asset_category_for_kind(kind: str | None) -> str:
    if not kind:
        return "other"
    return ASSET_KIND_TO_CATEGORY.get(kind, "other")


def get_history_view(
    db_path: str,
    *,
    page: int = 1,
    per_page: int = 10,
    grouped: bool = True,
    status: str = "all",
    search: str = "",
    media: str = "all",
) -> dict:
    rows = fetch_history_rows(db_path, limit=None, status=status, search=search or None)
    items = group_history_rows(rows) if grouped else rows
    if media != "all":
        if grouped:
            if media == "streams":
                items = [item for item in items if item.get("stream_count", 0) > 0]
            elif media == "videos":
                items = [item for item in items if item.get("video_count", 0) > 0]
            elif media == "documents":
                items = [item for item in items if item.get("document_count", 0) > 0]
            elif media == "images":
                items = [item for item in items if item.get("image_count", 0) > 0]
            elif media == "other":
                items = [item for item in items if item.get("other_asset_count", 0) > 0]
            elif media == "empty":
                items = [item for item in items if item.get("asset_count", 0) == 0]
        else:
            if media == "streams":
                items = [item for item in items if item.get("m3u8_url")]
            elif media == "videos":
                items = [item for item in items if item.get("mp4_url")]
            elif media == "documents":
                items = [item for item in items if item.get("asset_category") == "document"]
            elif media == "images":
                items = [item for item in items if item.get("asset_category") == "image"]
            elif media == "other":
                items = [item for item in items if item.get("asset_category") == "other"]
            elif media == "empty":
                items = [item for item in items if not item.get("asset_url") and not item.get("m3u8_url") and not item.get("mp4_url")]
    total_items = len(items)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    start = (page - 1) * per_page
    paged_items = items[start : start + per_page]
    summary = summarize_history_rows(rows, grouped=grouped)
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
            "media": media,
        },
        "summary": summary,
    }


def get_analysis_group(db_path: str, entry_id: int) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT page_url, scanned_at FROM scans WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return None
    rows = fetch_history_rows(db_path, limit=None)
    grouped = group_history_rows([r for r in rows if r["page_url"] == row["page_url"] and r["scanned_at"] == row["scanned_at"]])
    if not grouped:
        return None
    item = grouped[0]
    item["trace_steps"] = parse_trace(item.get("source_trace"))
    return item


def delete_analysis_group(db_path: str, entry_id: int) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT page_url, scanned_at FROM scans WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return False
        cursor = conn.execute("DELETE FROM scans WHERE page_url = ? AND scanned_at = ?", (row["page_url"], row["scanned_at"]))
        conn.commit()
        return cursor.rowcount > 0


def clear_history(db_path: str) -> int:
    with get_connection(db_path) as conn:
        cursor = conn.execute("DELETE FROM scans")
        conn.commit()
        return cursor.rowcount
