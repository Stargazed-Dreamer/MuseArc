from __future__ import annotations

from pathlib import Path

import typer

from musearc.config.store import load_runtime_config, save_runtime_config
from musearc.services.exporter import ExportService
from musearc.services.importer import ImportService
from musearc.services.library import open_or_create_library
from musearc.services.library_ops import LibraryOpsService

app = typer.Typer(help="MuseArc CLI")
config_app = typer.Typer(help="运行时配置")
app.add_typer(config_app, name="config")


@app.command("init")
def init_library(library: str | None = typer.Option(None, help="音乐库路径")) -> None:
    ctx = open_or_create_library(library)
    typer.echo(f"library_ready: {ctx.layout.root}")


@app.command("import")
def import_from_source(
    source: str = typer.Option(..., help="导入来源目录"),
    library: str | None = typer.Option(None, help="音乐库路径，不传则使用上次路径"),
) -> None:
    ctx = open_or_create_library(library)
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise typer.BadParameter(f"source does not exist: {source_path}")

    with ctx.db.session() as conn:
        from musearc.infra.db.repositories import LibraryRepository

        repo = LibraryRepository(conn)
        report = ImportService(ctx.layout.root, ctx.runtime_config).import_path(repo, source_path)

    typer.echo(
        "\n".join(
            [
                f"import_batch_id={report.import_batch_id}",
                f"scanned_files={report.scanned_files}",
                f"imported_tracks={report.imported_tracks}",
                f"duplicate_tracks={report.duplicate_tracks}",
                f"imported_lyrics={report.imported_lyrics}",
                f"matched_lyrics={report.matched_lyrics}",
                f"review_items={report.review_items}",
                f"errors={len(report.errors)}",
            ]
        )
    )


@app.command("search")
def search_tracks(
    query: str = typer.Option("", help="关键词"),
    library: str | None = typer.Option(None, help="音乐库路径"),
    limit: int = typer.Option(100, min=1, max=100000),
) -> None:
    ctx = open_or_create_library(library)
    with ctx.db.session() as conn:
        from musearc.infra.db.repositories import LibraryRepository

        svc = LibraryOpsService(LibraryRepository(conn))
        rows = svc.search(query, limit)

    for row in rows:
        typer.echo(
            f"{row['track_id']} | {row.get('file_name','')} | {row['artist']} - {row['title']} | {row['album']} | {row['duration_sec']:.1f}s"
        )


@app.command("export")
def export_tracks(
    track_ids: list[str] = typer.Option(..., "--track", help="可重复指定多个 track id"),
    out: str = typer.Option(..., help="导出目录"),
    fmt: str = typer.Option("mp3", help="导出格式，如 mp3/flac/opus"),
    bitrate: str | None = typer.Option("320k", help="目标码率"),
    sample_rate: int | None = typer.Option(None, help="重采样率"),
    library: str | None = typer.Option(None, help="音乐库路径"),
) -> None:
    ctx = open_or_create_library(library)
    out_dir = Path(out).expanduser().resolve()

    with ctx.db.session() as conn:
        from musearc.infra.db.repositories import LibraryRepository

        repo = LibraryRepository(conn)
        exported = ExportService(ctx.layout.root).export_tracks(
            repo,
            track_ids,
            out_dir,
            fmt=fmt,
            bitrate=bitrate,
            sample_rate=sample_rate,
        )

    for path in exported:
        typer.echo(f"exported: {path}")


@app.command("review")
def review_list(
    library: str | None = typer.Option(None, help="音乐库路径"),
    limit: int = typer.Option(50, min=1, max=1000),
) -> None:
    ctx = open_or_create_library(library)
    with ctx.db.session() as conn:
        from musearc.infra.db.repositories import LibraryRepository

        svc = LibraryOpsService(LibraryRepository(conn))
        rows = svc.pending_reviews(limit)

    for row in rows:
        typer.echo(f"[{row['priority']}] {row['kind']} {row['review_id']} | {row['title']}")


@app.command("ui")
def launch_ui(
    library: str | None = typer.Option(None, help="音乐库路径，不传则使用上次路径"),
) -> None:
    from musearc.ui.app import run_ui

    raise typer.Exit(run_ui(library))


@config_app.command("show")
def config_show() -> None:
    cfg = load_runtime_config()
    typer.echo(cfg.model_dump_json(indent=2))


@config_app.command("set")
def config_set(
    lmstudio_enabled: bool | None = typer.Option(None, "--lmstudio-enabled/--no-lmstudio-enabled"),
    lmstudio_endpoint: str | None = typer.Option(None, help="LM Studio endpoint"),
    lmstudio_model: str | None = typer.Option(None, help="LM Studio model name"),
    force_save_threshold: int | None = typer.Option(None, min=1, max=1000, help="多选模式自动保存阈值"),
    undo_max_actions: int | None = typer.Option(None, min=1, max=10000, help="撤回最大保留条数"),
) -> None:
    cfg = load_runtime_config()
    if lmstudio_enabled is not None:
        cfg.lmstudio.enabled = lmstudio_enabled
    if lmstudio_endpoint:
        cfg.lmstudio.endpoint = lmstudio_endpoint
    if lmstudio_model:
        cfg.lmstudio.model = lmstudio_model
    if force_save_threshold is not None:
        cfg.ui.force_save_threshold = force_save_threshold
    if undo_max_actions is not None:
        cfg.ui.undo_max_actions = undo_max_actions

    save_runtime_config(cfg)
    typer.echo("config_updated")


if __name__ == "__main__":
    app()
