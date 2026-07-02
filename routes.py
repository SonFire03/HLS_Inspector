from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from db import (
    clear_history,
    delete_analysis_group,
    ensure_database,
    fetch_history_rows,
    get_analysis_group,
    get_history_view,
    group_history_rows,
    infer_source_type,
    parse_trace,
    save_scan,
)
from extractor import AnalysisError, analyze_page, is_valid_http_url, now_iso


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "hls_inspector.db"
DEFAULT_HISTORY_PAGE_SIZE = 10
MAX_HISTORY_PAGE_SIZE = 50


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


def build_report_summary(history_items: list[dict]) -> dict:
    total_items = len(history_items)
    total_streams = sum(int(item.get("stream_count", 0) or 0) for item in history_items)
    total_videos = sum(int(item.get("video_count", 0) or 0) for item in history_items)
    success_count = sum(1 for item in history_items if item.get("status") == "success")
    no_stream_count = sum(1 for item in history_items if item.get("status") == "no_stream_found")
    error_count = sum(1 for item in history_items if item.get("status") == "error")
    invalid_count = sum(1 for item in history_items if item.get("status") == "invalid_url")
    direct_count = sum(1 for item in history_items if item.get("source_type") == "direct")
    return {
        "total_items": total_items,
        "total_streams": total_streams,
        "total_videos": total_videos,
        "success_count": success_count,
        "no_stream_count": no_stream_count,
        "error_count": error_count,
        "invalid_count": invalid_count,
        "direct_count": direct_count,
    }


def markdown_escape(value: object) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def build_report_markdown(history_items: list[dict], generated_at: str) -> str:
    stats = build_report_summary(history_items)
    lines = [
        "# HLS Inspector Report",
        "",
        f"- Generated at: {generated_at}",
        f"- Analyses: {stats['total_items']}",
        f"- Flux .m3u8: {stats['total_streams']}",
        f"- Vidéos .mp4: {stats['total_videos']}",
        f"- Success: {stats['success_count']}",
        f"- No stream found: {stats['no_stream_count']}",
        f"- Error: {stats['error_count']}",
        f"- Invalid URL: {stats['invalid_count']}",
        f"- Direct sources: {stats['direct_count']}",
        "",
        "## Analyses",
    ]

    if not history_items:
        lines.extend(["- No grouped analyses available.", ""])
        return "\n".join(lines)

    for item in history_items:
        lines.extend(
            [
                "",
                f"### {markdown_escape(item.get('page_title') or 'Sans titre')}",
                f"- ID: {markdown_escape(item.get('id'))}",
                f"- URL: {markdown_escape(item.get('page_url'))}",
                f"- Status: {markdown_escape(item.get('status'))}",
                f"- Source: {markdown_escape(item.get('source_label'))}",
                f"- Date: {markdown_escape(item.get('scanned_at'))}",
                f"- Flux count: {markdown_escape(item.get('stream_count', 0))}",
                f"- Video count: {markdown_escape(item.get('video_count', 0))}",
            ]
        )
        if item.get("streams"):
            lines.append("- Flux:")
            for stream in item["streams"]:
                lines.append(f"  - `{markdown_escape(stream)}`")
        if item.get("videos"):
            lines.append("- Vidéos:")
            for video in item["videos"]:
                lines.append(f"  - `{markdown_escape(video)}`")
        if item.get("error_message"):
            lines.append(f"- Error: {markdown_escape(item['error_message'])}")
    lines.append("")
    return "\n".join(lines)


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
            media="all",
        )
        return render_template("index.html", history_view=history_view)

    @app.get("/analysis/<int:entry_id>")
    def analysis_detail(entry_id: int):
        item = get_analysis_group(app.config["DATABASE_PATH"], entry_id)
        if item is None:
            return render_template("analysis_detail.html", item=None), 404
        return render_template("analysis_detail.html", item=item)

    @app.post("/api/analyze")
    def api_analyze():
        payload = request.get_json(silent=True) or {}
        url = (payload.get("url") or "").strip()

        if not is_valid_http_url(url):
            scanned_at = now_iso()
            source_trace = [{"stage": "validation", "message": "URL rejetée: schéma HTTP/HTTPS requis.", "url": url}]
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": "URL invalide",
                    "page_url": url,
                    "m3u8_url": None,
                    "mp4_url": None,
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
                    "videos": [],
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
            source_trace = exc.trace or [{"stage": "error", "message": str(exc), "url": exc.page_url or url}]
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": exc.page_title or "Sans titre",
                    "page_url": exc.page_url or url,
                    "m3u8_url": None,
                    "mp4_url": None,
                    "status": exc.status,
                    "error_message": str(exc),
                    "scanned_at": scanned_at,
                    "source_trace": json.dumps(source_trace, ensure_ascii=False),
                    "source_type": infer_source_type(json.dumps(source_trace, ensure_ascii=False)),
                },
            )
            return jsonify(
                {
                    "success": False,
                    "title": exc.page_title or "Sans titre",
                    "page_url": exc.page_url or url,
                    "streams": [],
                    "videos": [],
                    "status": exc.status,
                    "error_message": str(exc),
                    "scanned_at": scanned_at,
                    "trace": source_trace,
                    "source_type": infer_source_type(json.dumps(source_trace, ensure_ascii=False)),
                }
            )

        scanned_at = now_iso()
        has_media = bool(result["streams"] or result.get("mp4s"))
        if result["streams"]:
            for stream_url in result["streams"]:
                save_scan(
                    app.config["DATABASE_PATH"],
                    {
                        "page_title": result["title"],
                        "page_url": result["page_url"],
                        "m3u8_url": stream_url,
                        "mp4_url": None,
                        "status": "success",
                        "error_message": None,
                        "scanned_at": scanned_at,
                        "source_trace": json.dumps(result["trace"], ensure_ascii=False),
                        "source_type": result["source_type"],
                    },
                )
        if result.get("mp4s"):
            for video_url in result["mp4s"]:
                save_scan(
                    app.config["DATABASE_PATH"],
                    {
                        "page_title": result["title"],
                        "page_url": result["page_url"],
                        "m3u8_url": None,
                        "mp4_url": video_url,
                        "status": "success",
                        "error_message": None,
                        "scanned_at": scanned_at,
                        "source_trace": json.dumps(result["trace"], ensure_ascii=False),
                        "source_type": result["source_type"],
                    },
                )
        if not has_media:
            save_scan(
                app.config["DATABASE_PATH"],
                {
                    "page_title": result["title"],
                    "page_url": result["page_url"],
                    "m3u8_url": None,
                    "mp4_url": None,
                    "status": "no_stream_found",
                    "error_message": None,
                    "scanned_at": scanned_at,
                    "source_trace": json.dumps(result["trace"], ensure_ascii=False),
                    "source_type": result["source_type"],
                },
            )

        return jsonify(
            {
                "success": has_media,
                "title": result["title"],
                "page_url": result["page_url"],
                "streams": result["streams"],
                "videos": result.get("mp4s", []),
                "status": "success" if has_media else "no_stream_found",
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
        media = (request.args.get("media") or "all").strip()
        grouped = (request.args.get("grouped") or "1").strip() != "0"
        return jsonify(
            get_history_view(
                app.config["DATABASE_PATH"],
                page=page,
                per_page=per_page,
                grouped=grouped,
                status=status,
                search=search,
                media=media,
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
        return Response(payload, mimetype="application/json", headers={"Content-Disposition": "attachment; filename=hls_inspector_history.json"})

    @app.get("/export/csv")
    def export_csv():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["id", "page_title", "page_url", "m3u8_url", "mp4_url", "status", "error_message", "scanned_at", "source_trace"])
        for row in rows:
            writer.writerow([
                row["id"],
                row["page_title"],
                row["page_url"],
                row["m3u8_url"],
                row["mp4_url"],
                row["status"],
                row["error_message"],
                row["scanned_at"],
                row["source_trace"],
            ])
        return Response(buffer.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=hls_inspector_history.csv"})

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
        writer.writerow(["analysis_id", "page_title", "page_url", "status", "source_type", "streams", "stream_count", "videos", "video_count", "error_message", "scanned_at", "source_trace"])
        for item in history_items:
            writer.writerow([
                item["id"],
                item["page_title"],
                item["page_url"],
                item["status"],
                item["source_type"],
                json.dumps(item["streams"], ensure_ascii=False),
                item["stream_count"],
                json.dumps(item["videos"], ensure_ascii=False),
                item["video_count"],
                item["error_message"],
                item["scanned_at"],
                item["source_trace"],
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_history_detailed.csv"},
        )

    @app.get("/export/report/html")
    def export_report_html():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        history_items = group_history_rows(rows)
        report_stats = build_report_summary(history_items)
        generated_at = now_iso()
        return render_template(
            "report.html",
            generated_at=generated_at,
            history_items=history_items,
            total_items=report_stats["total_items"],
            report_stats=report_stats,
        )

    @app.get("/export/report/markdown")
    def export_report_markdown():
        rows = fetch_history_rows(app.config["DATABASE_PATH"], limit=None)
        history_items = group_history_rows(rows)
        generated_at = now_iso()
        payload = build_report_markdown(history_items, generated_at)
        return Response(
            payload,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=hls_inspector_report.md"},
        )

    return app
