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
    """初始化音乐库。

    功能：打开或创建指定路径的音乐库，并输出初始化完成的消息。
    参数：
        library (str | None): 音乐库路径，可选字符串，默认为None。
    返回值：None
    """
    ctx = open_or_create_library(library)  # 调用open_or_create_library函数，获取或创建音乐库上下文
    typer.echo(f"library_ready: {ctx.layout.root}")  # 输出初始化完成的消息，显示音乐库根路径


@app.command("import")
def import_from_source(
    source: str = typer.Option(..., help="导入来源目录"),
    library: str | None = typer.Option(None, help="音乐库路径，不传则使用上次路径"),
) -> None:
    """从指定来源目录导入音乐文件到音乐库。
    
    功能：扫描指定目录中的音乐文件，并将新发现的文件导入到音乐库中，同时处理元数据提取和歌词匹配。
    参数：
        source (str): 必填，要导入音乐的来源目录路径。
        library (str | None): 可选，音乐库的路径。如果为None，则使用上次使用的音乐库。
    返回值：None
    """
    # 打开或创建音乐库上下文，用于后续操作
    ctx = open_or_create_library(library)
    # 将来源路径展开为绝对路径并解析符号链接
    source_path = Path(source).expanduser().resolve()
    # 检查来源目录是否存在，如果不存在则抛出参数错误
    if not source_path.exists():
        raise typer.BadParameter(f"source does not exist: {source_path}")

    # 使用数据库连接会话进行导入操作
    with ctx.db.session() as conn:
        # 在会话上下文中导入库仓库类，确保使用正确的连接
        from musearc.infra.db.repositories import LibraryRepository

        # 创建库仓库实例，用于数据库操作
        repo = LibraryRepository(conn)
        # 执行导入操作，获取导入报告
        report = ImportService(ctx.layout.root, ctx.runtime_config).import_path(repo, source_path)

    # 格式化并输出导入报告信息
    typer.echo(
        "\n".join(
            [
                f"import_batch_id={report.import_batch_id}",  # 导入批次ID
                f"scanned_files={report.scanned_files}",      # 扫描到的文件数
                f"imported_tracks={report.imported_tracks}",  # 成功导入的曲目数
                f"duplicate_tracks={report.duplicate_tracks}",# 重复的曲目数
                f"imported_lyrics={report.imported_lyrics}",  # 成功导入的歌词数
                f"matched_lyrics={report.matched_lyrics}",    # 匹配到的歌词数
                f"review_items={report.review_items}",        # 需要审核的项目数
                f"errors={len(report.errors)}",               # 错误数量
            ]
        )
    )


@app.command("search")
def search_tracks(
    query: str = typer.Option("", help="关键词"),
    library: str | None = typer.Option(None, help="音乐库路径"),
    limit: int = typer.Option(100, min=1, max=100000),
) -> None:
    """搜索音乐库中的曲目。

    根据给定的关键词在指定的音乐库中搜索曲目，并按数量限制返回结果。

    Args:
        query: 用于搜索的关键词字符串，默认为空字符串。
        library: 音乐库的文件路径。如果为 None，则使用默认位置或创建新的库。
        limit: 最大返回结果数量，范围在 1 到 100000 之间，默认为 100。

    Returns:
        None: 此函数不返回任何值，但会将搜索结果直接输出到控制台。
    """
    ctx = open_or_create_library(library)  # 获取或创建指定的音乐库连接上下文
    with ctx.db.session() as conn:  # 在数据库会话中执行操作
        from musearc.infra.db.repositories import LibraryRepository

        svc = LibraryOpsService(LibraryRepository(conn))  # 初始化库操作服务
        rows = svc.search(query, limit)  # 执行搜索并获取结果行

    for row in rows:  # 遍历每一行搜索结果
        typer.echo(
            f"{row['track_id']} | {row.get('file_name','')} | {row['artist']} - {row['title']} | {row['album']} | {row['duration_sec']:.1f}s"
        )  # 格式化并输出曲目信息：ID、文件名、艺术家-标题、专辑、时长（秒）


@app.command("export")
def export_tracks(
    track_ids: list[str] = typer.Option(..., "--track", help="可重复指定多个 track id"),
    out: str = typer.Option(..., help="导出目录"),
    fmt: str = typer.Option("mp3", help="导出格式，如 mp3/flac/opus"),
    bitrate: str | None = typer.Option("320k", help="目标码率"),
    sample_rate: int | None = typer.Option(None, help="重采样率"),
    library: str | None = typer.Option(None, help="音乐库路径"),
) -> None:
    """导出指定的音轨到指定目录。
    
    功能：
        将音乐库中指定的音轨导出到用户指定的目录，并根据参数进行格式转换和重采样。
    参数：
        track_ids (list[str]): 需要导出的音轨ID列表，可以通过多次指定 --track 参数提供多个ID。
        out (str): 导出文件的输出目录路径。
        fmt (str, optional): 导出的音频格式，默认为 "mp3"。支持 "mp3", "flac", "opus" 等格式。
        bitrate (str | None, optional): 目标码率，默认为 "320k"。如果为 None 则保持原码率。
        sample_rate (int | None, optional): 重采样率，如果为 None 则保持原始采样率。
        library (str | None, optional): 音乐库的路径。如果为 None，则使用默认配置的音乐库路径。
    返回值：
        None: 该函数无返回值，但会通过标准输出打印每个导出文件的路径。
    """
    # 创建或打开音乐库上下文，用于访问数据库和布局信息
    ctx = open_or_create_library(library)
    # 将输出目录路径字符串转换为 Path 对象，并解析为绝对路径
    out_dir = Path(out).expanduser().resolve()

    # 使用数据库会话上下文管理器，确保会话在操作后正确关闭
    with ctx.db.session() as conn:
        # 导入库仓库模块，用于访问音乐库中的数据
        from musearc.infra.db.repositories import LibraryRepository

        # 初始化库仓库实例，连接到数据库会话
        repo = LibraryRepository(conn)
        # 调用导出服务的 export_tracks 方法执行实际的导出操作
        # 参数包括仓库、音轨ID列表、输出目录以及音频格式和质量参数
        exported = ExportService(ctx.layout.root).export_tracks(
            repo,
            track_ids,
            out_dir,
            fmt=fmt,
            bitrate=bitrate,
            sample_rate=sample_rate,
        )

    # 遍历导出的文件路径列表，并逐个输出到终端
    for path in exported:
        typer.echo(f"exported: {path}")


@app.command("review")
def review_list(
    library: str | None = typer.Option(None, help="音乐库路径"),
    limit: int = typer.Option(50, min=1, max=1000),
) -> None:
    """从音乐库中获取待审核的条目并打印出来。

    Args:
        library (str | None): 音乐库的路径，为None则使用默认路径。
        limit (int): 要获取的待审核条目数量，范围在1到1000之间，默认为50。

    Returns:
        None: 该函数不返回任何值，但会直接打印待审核条目信息。
    """
    # 根据提供的库路径打开或创建音乐库上下文
    ctx = open_or_create_library(library)
    # 使用数据库会话连接
    with ctx.db.session() as conn:
        # 从数据库仓库模块导入LibraryRepository
        from musearc.infra.db.repositories import LibraryRepository

        # 创建一个库操作服务实例，并传入库仓库
        svc = LibraryOpsService(LibraryRepository(conn))
        # 调用服务方法获取待审核的行，数量由limit指定
        rows = svc.pending_reviews(limit)

    # 遍历每一行待审核数据
    for row in rows:
        # 使用typer格式化并输出每条待审核信息，包含优先级、类型、ID和标题
        typer.echo(f"[{row['priority']}] {row['kind']} {row['review_id']} | {row['title']}")


@app.command("ui")
def launch_ui(
    library: str | None = typer.Option(None, help="音乐库路径，不传则使用上次路径"),
) -> None:
    """启动音乐库管理的用户界面（UI）。
    
    功能：通过命令行参数可选地接收音乐库路径，启动对应的图形或终端界面。
    参数：
        library (str | None): 音乐库的文件路径。如果未提供，则使用上一次使用的路径。
    返回值：无（None）。
    """
    from musearc.ui.app import run_ui  # 延迟导入UI模块，避免不必要的启动开销

    raise typer.Exit(run_ui(library))  # 运行UI并将返回值作为退出码传递给typer，确保程序按预期退出


@config_app.command("show")
def config_show() -> None:
    """显示当前运行时配置信息。
    
    功能：
        从运行时加载配置并将其以格式化的JSON字符串形式输出到控制台。
    
    参数：
        无参数。
    
    返回值：
        无返回值（None）。
    """
    # 从运行时环境中加载配置对象
    cfg = load_runtime_config()
    # 将配置对象转换为格式化的JSON字符串并输出到控制台
    # indent=2 表示使用2个空格缩进，使输出更易读
    typer.echo(cfg.model_dump_json(indent=2))


@config_app.command("set")
def config_set(
    lmstudio_enabled: bool | None = typer.Option(None, "--lmstudio-enabled/--no-lmstudio-enabled"),
    lmstudio_endpoint: str | None = typer.Option(None, help="LM Studio endpoint"),
    lmstudio_model: str | None = typer.Option(None, help="LM Studio model name"),
    force_save_threshold: int | None = typer.Option(None, min=1, max=1000, help="多选模式自动保存阈值"),
    undo_max_actions: int | None = typer.Option(None, min=1, max=10000, help="撤回最大保留条数"),
) -> None:
    """用于修改运行时配置的命令行函数。
    
    该函数通过命令行选项接收参数，用于更新LM Studio和UI相关的运行时配置。
    
    参数:
        lmstudio_enabled (bool | None): 是否启用LM Studio服务，None表示不修改。
        lmstudio_endpoint (str | None): LM Studio服务的端点地址，None或空字符串表示不修改。
        lmstudio_model (str | None): LM Studio使用的模型名称，None或空字符串表示不修改。
        force_save_threshold (int | None): 多选模式下自动保存的阈值，取值范围1-1000，None表示不修改。
        undo_max_actions (int | None): 撤回操作保留的最大条数，取值范围1-10000，None表示不修改。
    
    返回值:
        None: 该函数不返回任何值，仅执行配置更新和输出确认信息。
    """
    # 加载当前的运行时配置
    cfg = load_runtime_config()
    
    # 如果用户提供了lmstudio_enabled参数，则更新配置中对应的启用状态
    if lmstudio_enabled is not None:
        cfg.lmstudio.enabled = lmstudio_enabled
    
    # 如果用户提供了非空的lmstudio_endpoint参数，则更新配置中对应的端点地址
    if lmstudio_endpoint:
        cfg.lmstudio.endpoint = lmstudio_endpoint
    
    # 如果用户提供了非空的lmstudio_model参数，则更新配置中对应的模型名称
    if lmstudio_model:
        cfg.lmstudio.model = lmstudio_model
    
    # 如果用户提供了force_save_threshold参数，则更新配置中对应的自动保存阈值
    if force_save_threshold is not None:
        cfg.ui.force_save_threshold = force_save_threshold
    
    # 如果用户提供了undo_max_actions参数，则更新配置中对应的撤回保留条数
    if undo_max_actions is not None:
        cfg.ui.undo_max_actions = undo_max_actions

    # 将更新后的配置保存到文件
    save_runtime_config(cfg)
    # 向用户输出配置已更新的确认信息
    typer.echo("config_updated")


if __name__ == "__main__":
    app()
