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
    content = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for line in content.splitlines():
        text = line.strip()
        if text.startswith("version"):
            parts = text.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"').strip("'")
    return "0.0.0"


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _copy_release_source(dst: Path) -> Path:
    bundle = dst / f"{PROJECT_NAME}_src"
    bundle.mkdir(parents=True, exist_ok=True)
    for item in COPY_ITEMS:
        src = PROJECT_ROOT / item
        if not src.exists():
            continue
        target = bundle / item
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True, ignore=COPY_IGNORE)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    return bundle


def _copy_wheel_artifact(dst: Path) -> Path:
    _run([sys.executable, "-m", "uv", "build", "--wheel"], cwd=PROJECT_ROOT)
    dist = PROJECT_ROOT / "dist"
    wheels = sorted(dist.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise RuntimeError("No wheel artifact found in dist/.")
    wheel = wheels[0]
    out_dir = dst / "wheel"
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wheel, out_dir / wheel.name)
    return out_dir / wheel.name


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in source_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source_dir))


def main() -> None:
    parser = argparse.ArgumentParser(description="Export release bundle for MuseArc")
    parser.add_argument(
        "--mode",
        choices=["source", "wheel", "all"],
        default="all",
        help="source: export source bundle; wheel: build wheel only; all: both",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output directory. Default: .build/releases/<version_timestamp>",
    )
    args = parser.parse_args()

    version = _project_version()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.output).resolve() if str(args.output).strip() else (BUILD_ROOT / f"v{version}_{stamp}")
    _clean_dir(out_root)

    source_bundle: Path | None = None
    wheel_path: Path | None = None
    if args.mode in {"source", "all"}:
        source_bundle = _copy_release_source(out_root)
        _zip_dir(source_bundle, out_root / f"{PROJECT_NAME}_src_v{version}.zip")

    if args.mode in {"wheel", "all"}:
        wheel_path = _copy_wheel_artifact(out_root)

    print(f"[OK] release exported to: {out_root}")
    if source_bundle is not None:
        print(f"[SRC] {source_bundle}")
    if wheel_path is not None:
        print(f"[WHL] {wheel_path}")


if __name__ == "__main__":
    main()
