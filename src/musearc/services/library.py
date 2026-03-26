from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from musearc.config.models import LibraryLayout, RuntimeConfig
from musearc.config.store import load_runtime_config, save_runtime_config
from musearc.infra.db.connection import DbManager


@dataclass(slots=True)
class LibraryContext:
    layout: LibraryLayout
    db: DbManager
    runtime_config: RuntimeConfig


def open_or_create_library(path: str | None) -> LibraryContext:
    cfg = load_runtime_config()

    if path and path.strip():
        root = Path(path).expanduser().resolve()
    elif cfg.last_library_path:
        root = Path(cfg.last_library_path).expanduser().resolve()
    else:
        root = (Path.home() / "Music" / "MuseArcLibrary").resolve()

    layout = LibraryLayout(root=root)
    db = DbManager(layout)
    db.ensure_layout()
    db.init_schema()

    cfg.last_library_path = str(root)
    save_runtime_config(cfg)

    with db.session() as conn:
        from musearc.infra.db.repositories import LibraryRepository

        repo = LibraryRepository(conn)
        repo.set_meta("schema_version", "1")
        repo.set_meta("library_root", str(root))

    return LibraryContext(layout=layout, db=db, runtime_config=cfg)
