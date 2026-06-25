from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = PROJECT_ROOT / ".build" / "releases"
PROJECT_NAME = "MuseArc"
COPY_ITEMS = [
    "src",
    "pyproject.toml",
    "README.md",
    "tools/chromaprint/bin",
]
COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
)


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=str(cwd or PROJECT_ROOT), check=True)


def _project_version() -> str:
    """
    从项目根目录下的pyproject.toml文件中读取版本号。

    参数：
        无参数。

    返回值：
        str: 返回版本号字符串；如果未找到，则返回默认版本"0.0.0"。
    """
    # 读取pyproject.toml文件内容
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # 遍历文件内容的每一行
    for line in content.splitlines():
        # 去除行首尾的空白字符
        text = line.strip()
        # 检查当前行是否以"version"开头
        if text.startswith("version"):
            # 按等号分割字符串，限制分割次数为1
            parts = text.split("=", 1)
            # 确保分割后有两部分
            if len(parts) == 2:
                # 提取版本号，去除可能的引号和空白
                return parts[1].strip().strip('"').strip("'")
    # 如果未找到版本号，返回默认版本
    return "0.0.0"


def _clean_dir(path: Path) -> None:
    """
    清理指定目录，删除现有内容并重新创建空目录。

    参数：
        path (Path): 要清理的目录路径。

    返回值：
        None
    """
    if path.exists():  # 如果路径存在
        shutil.rmtree(path)  # 删除整个目录树
    path.mkdir(parents=True, exist_ok=True)  # 创建目录，包括父目录，如果已存在则忽略错误


def _copy_release_source(dst: Path) -> Path:
    """
    将项目源代码复制到指定的目标目录中，创建一个源代码bundle。

    参数:
        dst (Path): 目标目录路径。

    返回:
        Path: 源代码bundle的路径。
    """
    # 创建源代码bundle的路径，基于项目名称
    bundle = dst / f"{PROJECT_NAME}_src"
    # 创建bundle目录，如果不存在则创建，包括父目录
    bundle.mkdir(parents=True, exist_ok=True)
    # 遍历需要复制的项目列表
    for item in COPY_ITEMS:
        # 计算源路径
        src = PROJECT_ROOT / item
        # 如果源路径不存在，跳过此项
        if not src.exists():
            continue
        # 计算目标路径
        target = bundle / item
        # 如果源是目录，递归复制整个目录
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True, ignore=COPY_IGNORE)
        else:
            # 如果源是文件，确保目标父目录存在
            target.parent.mkdir(parents=True, exist_ok=True)
            # 复制文件，保留元数据
            shutil.copy2(src, target)
    # 返回源代码bundle的路径
    return bundle


def _copy_wheel_artifact(dst: Path) -> Path:
    """将项目构建为wheel包并复制到指定目录

    Args:
        dst (Path): 目标目录路径，wheel包将被复制到该目录下的"wheel"子目录中

    Returns:
        Path: 最终wheel文件的完整路径

    Raises:
        RuntimeError: 如果在dist/目录中未找到wheel文件时抛出
    """
    # 使用当前Python解释器执行uv构建命令，在项目根目录下构建wheel包
    _run([sys.executable, "-m", "uv", "build", "--wheel"], cwd=PROJECT_ROOT)
    # 定义dist目录路径
    dist = PROJECT_ROOT / "dist"
    # 获取dist目录下所有.wheel文件，按修改时间降序排列（最新的排在前面）
    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    # 检查是否找到任何wheel文件
    if not wheels:
        raise RuntimeError("No wheel artifact found in dist/.")
    # 获取最新的wheel文件（列表中的第一个元素）
    wheel = wheels[0]
    # 创建输出目录：目标目录下的wheel子目录
    out_dir = dst / "wheel"
    # 递归创建目录，如果目录已存在则忽略错误
    out_dir.mkdir(parents=True, exist_ok=True)
    # 将wheel文件复制到输出目录，保留文件元数据
    shutil.copy2(wheel, out_dir / wheel.name)
    # 返回复制后的wheel文件完整路径
    return out_dir / wheel.name


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    """将指定目录压缩为ZIP文件。
    参数:
        source_dir (Path): 源目录路径。
        zip_path (Path): 压缩文件保存路径。
    返回值:
        None
    """
    # 如果压缩文件已存在，删除它以避免覆盖冲突
    if zip_path.exists():
        zip_path.unlink()
    # 创建ZIP文件对象，使用w模式写入，并设置DEFLATED压缩算法以减小文件大小
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 递归遍历源目录中的所有文件和子目录（包括隐藏文件）
        for path in source_dir.rglob("*"):
            # 仅处理文件，跳过目录条目
            if path.is_file():
                # 将文件写入ZIP，使用相对于源目录的路径以保留目录结构
                zf.write(path, path.relative_to(source_dir))


def main() -> None:
    """
    导出 MuseArc 项目的发布包。根据命令行指定的模式（源码、轮子包或两者），
    将项目发布内容打包并输出到指定或默认目录。
    
    参数：
        无显式参数，通过命令行参数控制行为。
    
    返回值：
        None。该函数执行打包和输出操作，无返回值。
    """
    # 创建命令行参数解析器，设置程序描述
    parser = argparse.ArgumentParser(description="Export release bundle for MuseArc")
    parser.add_argument(
        # 添加模式参数，定义可选值和默认值
        "--mode",
        choices=["source", "wheel", "all"],
        default="all",
        help="source: export source bundle; wheel: build wheel only; all: both",
    )
    parser.add_argument(
        # 添加输出目录参数，可选，默认为空
        "--output",
        default="",
        help="Optional output directory. Default: .build/releases/<version_timestamp>",
    )
    args = parser.parse_args()  # 解析命令行参数

    version = _project_version()  # 获取项目版本号
    # 生成时间戳字符串，格式为：年月日_时分秒
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 确定输出根目录：如果指定了输出参数且非空，则使用其绝对路径；否则使用默认的构建根目录
    out_root = Path(args.output).resolve() if str(args.output).strip() else (BUILD_ROOT / f"v{version}_{stamp}")
    _clean_dir(out_root)  # 清理输出目录

    source_bundle: Path | None = None  # 存储源码包路径，初始为空
    wheel_path: Path | None = None  # 存储轮子包路径，初始为空
    
    # 如果模式是 "source" 或 "all"，则处理源码打包
    if args.mode in {"source", "all"}:
        source_bundle = _copy_release_source(out_root)  # 复制发布源码到输出目录
        # 将源码目录打包为 zip 文件
        _zip_dir(source_bundle, out_root / f"{PROJECT_NAME}_src_v{version}.zip")

    # 如果模式是 "wheel" 或 "all"，则处理轮子包打包
    if args.mode in {"wheel", "all"}:
        wheel_path = _copy_wheel_artifact(out_root)  # 复制轮子包文件到输出目录

    # 打印成功信息及输出路径
    print(f"[OK] release exported to: {out_root}")
    if source_bundle is not None:  # 如果生成了源码包，打印其路径
        print(f"[SRC] {source_bundle}")
    if wheel_path is not None:  # 如果生成了轮子包，打印其路径
        print(f"[WHL] {wheel_path}")


if __name__ == "__main__":
    main()
