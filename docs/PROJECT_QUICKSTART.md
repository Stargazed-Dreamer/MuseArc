# MuseArc 项目速览（AI 会话上下文引导）

> 本文档目标：让新会话在 **3 次文件读取内** 建立足够上下文，避免反复探索耗尽上下文窗口。

## 1. 项目定位

音乐库管理工具：导入 → 去重 → 歌词匹配 → 审查 → 导出。Python + PySide6 桌面应用。

## 2. 架构分层（自上而下）

```
ui/                    PySide6 界面层
app/facade*.py         业务门面（UI 与 Service 之间的桥梁）
services/              核心业务逻辑
infra/                 基础设施（DB / 媒体处理 / LLM）
core/                  纯值对象（枚举、模型、ID、哈希、路径）
config/                运行时配置
```

## 3. 关键文件索引（按修改频率排序）

### 高频修改区

| 文件 | 职责 | 行数 |
|------|------|------|
| `services/importer_pipeline.py` | **导入主流程**（扫描→音频→歌词→审查） | ~1400 |
| `services/importer.py` | ImportService 类 + 工具函数（质量评分、元数据推导、歌词语言推断） | ~1030 |
| `services/dedupe.py` | 去重决策逻辑（指纹相似度分段 + 格式/质量优先级） | ~280 |
| `ui/review_page.py` | 审查页 UI（歌曲审查 + 歌词审查） | 大 |
| `ui/main_window*.py` | 主窗口（拆分为 logic/pages/components/helpers） | 各中等 |

### 中频修改区

| 文件 | 职责 |
|------|------|
| `services/import_runtime.py` | 断点恢复状态（ResumeState）+ 暂停/取消控制（ImportControl） |
| `services/scanner.py` | 扫描源目录，按扩展名分音频/歌词（~40行，极简） |
| `services/lyrics_match.py` | 歌词匹配服务（被 pipeline 内联规则替代，保留兼容） |
| `infra/db/repositories.py` | 数据库仓库（入口，mixins 拆分） |
| `infra/db/repositories_mixins_meta_import.py` | 导入相关 DB 操作（批次、审查队列） |
| `infra/db/repositories_mixins_tracks_lyrics.py` | 歌词-歌曲关联 DB 操作 |
| `infra/media/prober.py` | 音频探测（ffprobe 包装 + 乱码修复） |
| `infra/media/fingerprint.py` | Chromaprint 指纹引擎 + 相似度计算 |
| `infra/media/transcoder.py` | ffmpeg 转码/响度归一 |
| `core/models.py` | 所有数据模型（ProbeInfo/TrackInsert/LyricsInsert/ReviewItem 等） |
| `core/enums.py` | 枚举（DuplicateDecision/ReviewKind/TrackKind/FileHealth） |
| `config/models.py` | 运行时配置模型（阈值、LM Studio 等） |

### 低频修改区（了解即可）

| 文件 | 职责 |
|------|------|
| `app/facade.py` | Facade 主类，组合各 mixin |
| `app/facade_mixins_import_export.py` | Facade 导入/导出方法 |
| `ui/import_worker.py` | QThread 导入工作器（~60行，薄封装） |
| `ui/import_dialog.py` | 导入进度对话框（~65行） |
| `infra/db/schema.sql` | 数据库建表 SQL |
| `infra/db/connection.py` | SQLite 连接管理 |
| `infra/llm/client.py` | LM Studio 歌词匹配客户端 |
| `services/library.py` | 库查询服务 |
| `services/exporter.py` | 导出服务 |

## 4. 导入流程速查（最核心的流程）

```
用户点击导入
  → ImportWorker.run()                          [ui/import_worker.py]
    → Facade.import_from()                      [app/facade_mixins_import_export.py]
      → ImportService.import_path()             [services/importer.py:899]
        → run_import_path()                     [services/importer_pipeline.py:50]
```

### run_import_path 主循环结构

```
1. 扫描：scan_import_source() → audio_files, lyrics_files
2. 初始化/恢复 ResumeState
3. 加载索引：库内路径、历史跳过路径、待审查队列
4. ┌─ 音频循环 ─────────────────────────────────────┐
   │ a. 路径快速排除（库内/历史跳过/待审查）          │
   │ b. MediaProbe.probe() → 探测失败→审查           │
   │ c. 低时长过滤 → 审查                            │
   │ d. 指纹提取（批量并行，响度归一-14 LUFS）        │
   │ e. DuplicateEvaluator.decide() → 去重决策       │
   │    - KEEP_EXISTING → 跳过                       │
   │    - REVIEW → 入审查队列                        │
   │    - KEEP_NEW → 替换旧版（软删除+合并元数据）    │
   │    - KEEP_BOTH → 共存（写变体关系）              │
   │ f. 归档复制 + source_sha256 去重                 │
   │ g. insert_track → 入库                          │
   └────────────────────────────────────────────────┘
5. ┌─ 歌词循环 ─────────────────────────────────────┐
   │ a. 路径快速排除（历史已处理/待审查）             │
   │ b. 读取歌词 + HTML反转义                         │
   │ c. 纯音乐占位歌词 → 跳过+标记instrumental        │
   │ d. 文本哈希去重（命中已删除→审查，未删除→跳过）   │
   │ e. 歌词匹配规则（优先级）：                      │
   │    1) ti→title 精确匹配（同目录直接绑定）        │
   │    2) 文件名→文件名（进审查）                    │
   │    3) 歌词ti→歌曲文件名（进审查）                │
   │    4) 跨目录重跑（一律审查）                     │
   │    5) 无匹配 → 审查                             │
   │ f. 冲突：已有歌词时文本相似度≥0.90→跳过          │
   └────────────────────────────────────────────────┘
6. 收尾：finish_import_batch + 写清单
```

### 指纹相似度分段（由 config thresholds 驱动）

| 区间 | 判定 | 决策 |
|------|------|------|
| 0.50~1.00 | 同一歌曲 | 去重决策（质量/格式优先） |
| 0.30~0.50 | 人工审查 | REVIEW |
| 0.10~0.30 | 同曲不同编配 | KEEP_BOTH + 变体提示 |
| 0.01~0.10 | 翻唱 | KEEP_BOTH + 变体提示 |
| <0.01 | 不同歌曲 | KEEP_BOTH |

## 5. 数据存储布局

```
realLib/
  data/tracks/xx/    音频文件（分片存储，xx为ID前两位hex）
  data/lyrics/xx/    歌词文件（同上）
  db/musearc.db      SQLite 数据库
  manifests/
    imports/
      resume/            断点恢复状态 JSON
      skipped_audio_paths.json   历史跳过音频路径索引
      seen_lyrics_paths.json     历史已处理歌词路径索引
      {batch_id}.json    导入报告清单
```

## 6. 关键设计模式

- **Facade + Mixin**：`MuseArcFacade` 通过 `facade_mixins_*.py` 拆分方法
- **Repository + Mixin**：`LibraryRepository` 通过 `repositories_mixins_*.py` 拆分
- **Pipeline 拆分**：`ImportService` 仅做初始化，主流程在 `importer_pipeline.py` 的 `run_import_path()` 函数中
- **闭包状态**：pipeline 内大量使用闭包（`set_processing/set_skipped/emit` 等）管理状态，非类方法
- **断点恢复**：`ResumeState` 记录 `processed_relpaths`，重入时跳过已处理文件
- **路径索引**：两个 JSON 文件持久化跳过/已处理路径，避免重复导入
- **审查队列**：所有不确定决策入 `review` 表，UI 审查页处理

## 7. 快速定位指南

| 想了解... | 读这个文件 |
|-----------|-----------|
| 导入流程细节 | `services/importer_pipeline.py` |
| 去重算法 | `services/dedupe.py` |
| 歌词匹配规则 | `services/importer_pipeline.py` L1127-1361 |
| 质量评分公式 | `services/importer.py` `_quality_score()` |
| 元数据推导（标题/艺术家） | `services/importer.py` `_derive_title_artist()` |
| 歌词语言推断 | `services/importer.py` `_infer_lyrics_language_kind()` |
| 数据库表结构 | `infra/db/schema.sql` |
| 数据模型定义 | `core/models.py` |
| 配置阈值 | `config/models.py` |
| 审查页 UI | `ui/review_page.py` + `ui/review_page_mixins_*.py` |
| 导入对话框 | `ui/import_dialog.py`（进度）+ `ui/import_management_page.py`（管理） |
| 暂停/取消机制 | `services/import_runtime.py` `ImportControl` |

## 8. 注意事项

- `importer_pipeline.py` 是单函数 ~1400 行，闭包嵌套深，修改需注意作用域
- 音频指纹依赖 `chromaprint`（Windows 下 DLL 内置在 `tools/chromaprint/bin/`；Linux/macOS 需安装系统库：`apt install chromaprint-tools` / `brew install chromaprint`）
- 歌词匹配在 pipeline 中已内联实现，`lyrics_match.py` 的 `LyricsMatcher` 仅用于纯音乐占位匹配
- 数据库操作在 `with self.ctx.db.session() as conn:` 上下文中执行
- UI 信号通过 `ImportWorker.progress` 传递 `ImportProgress` 字典
