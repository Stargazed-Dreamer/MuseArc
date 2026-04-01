# MuseArc 详细设计说明（当前代码实现）

本文档面向开发者，补充 UI 之外的内部实现细节，重点覆盖：
- 代码架构与分层
- 导入/导出主流程
- 音频指纹链路
- 数据结构与数据库模型
- 主窗口与页面模块拆分原则

## 1. 代码架构

### 1.1 分层约束
- `musearc/ui`：纯界面层，负责交互、视图状态、信号槽。
- `musearc/app`：外观层（Facade），向 UI 提供稳定 API，封装事务边界与跨服务编排。
- `musearc/services`：领域服务层，承载导入、导出、去重、匹配等业务流程。
- `musearc/infra`：基础设施层，包含数据库仓储、媒体解码/探测/转码、外部接口。
- `musearc/core`：基础模型、枚举、路径与哈希等无副作用工具。

### 1.2 事务边界
- 所有写库操作通过 `MuseArcFacade` 内部 `with db.session()` 进入。
- UI 不直接操作数据库连接，统一通过 Facade 调用。
- 导入流程在 `ImportService.import_path()` 中逐步保存断点状态，确保可恢复。

## 2. 主窗口与页面架构

### 2.1 主窗口职责
- `main_window.py`：
  - 顶级容器装配（侧栏、页面栈、底部播放器栏）。
  - 播放队列统一入口（`queue_and_play_tracks / queue_and_play_paths`）。
- `main_window_logic.py`：
  - 菜单、历史撤销、页面刷新、库切换、关闭时资源清理。
  - `更多` 菜单工具入口（如：ID3+歌词元信息批量修复窗口）。

### 2.2 页面拆分（本次重构）
- `main_window_pages_tracks.py`：`TracksPage`、`PlaylistPage`
- `main_window_pages_ops.py`：`FullScanPage`、`TrashPage`、`TagManagementPage`
- `main_window_pages_lyrics.py`：`LyricsManagementPage`
- `main_window_pages_common.py`：页面共享播放调用工具
- `main_window_pages.py`：仅聚合导出，避免大文件继续膨胀

设计目标：
- 避免单文件承载全部页面逻辑（高耦合、难维护）。
- 每个文件按“业务域”聚合，避免过度细碎的小文件。

## 3. 导入流程（核心）

实现入口：`musearc/services/importer.py::ImportService.import_path`

### 3.1 扫描与状态
- 扫描音频/歌词候选集合。
- 加载 resume state（若存在）并合并文件状态。
- `file_states` 持续写回，用于 UI 实时展示和中断恢复。

### 3.2 路径级快速排除
- 音频：
  - 库中已存在路径（含已删除）
  - 历史跳过路径索引（`skipped_audio_paths.json`）
  - 已在 pending 审查队列
- 歌词：
  - 历史已处理路径索引（`seen_lyrics_paths.json`）
  - 已在 pending 审查队列

目的：减少重复 I/O、避免重复审查项。

### 3.3 音频处理链
1. Probe（时长、采样率、声道、码率、标签）。
   - 标签解析优先 ID3/容器字段，文件名仅做兜底；
   - 对疑似乱码标签做多编码修复。
2. 时长阈值过滤（疑似试听/哑文件进入审查）。
3. -14 LUFS 归一 + 指纹提取（当前基于 libchromaprint 绑定）。
4. 指纹重复决策（保留旧/保留新/审查）。
   - 替换策略综合格式等级与质量分，VBR 缺失码率时会用文件体积估算。
5. 归档复制并同时计算 `source_sha256`（单次读取源文件）。
6. 依据 `source_sha256` 二次去重，处理已删除重导入等分支。

### 3.4 歌词处理链
1. 读取文本并做 HTML 反转义。
2. 纯音乐占位歌词直接跳过歌词导入，并尝试标记歌曲 `instrumental`。
3. `text_hash` 去重：
   - 命中已删除歌词 -> 进入审查
   - 命中未删除歌词 -> 直接跳过
4. 新歌词入库后执行匹配：
   - 优先当前批次歌曲
   - 不足时回退到全库歌曲
   - 低置信度进入审查并附建议候选
5. 歌词审查支持“预览两条歌词并时间轴合并”：
   - 合并后写回前者；
   - 后者软删除并退出当前审查；
   - 操作可撤回/重做（快照回放）。

## 4. 导出流程

实现入口：`musearc/app/facade.py` 与 `services/exporter.py`

### 4.1 导出模式
- 导出歌单清单（JSON）
- 导出实际音频文件（可按每首格式计划导出）
- 两种模式可同时勾选

### 4.2 歌单 JSON 关键字段
- `playlist_hash`：由导出时间与曲目集合生成
- `database_location`：原库位置提示，便于播放器定位
- `tracks[]`：
  - `track_id`
  - `storage_relpath`
  - `source_sha256`
  - `lyrics_storage_relpath`
  - `stats`（播放统计占位）

### 4.3 统计导入
- 通过 `playlist_hash` + 曲目标识回写统计标签。
- 支持历史去重与“同一歌单多次导入取最新贡献”。

## 5. 音频指纹设计

### 5.1 当前策略
- 统一单算法：Chromaprint（lib 方式，不依赖外部 exe）。
- 指纹前统一 loudness normalize 到 -14 LUFS，提升跨来源可比性。

### 5.2 指纹数据落库
- `fingerprint_version`
- `fingerprint_digest`
- `fingerprint_payload`

### 5.3 去重决策输入
- 指纹相似度
- 质量评分（时长、码率、格式权重）
- 文件名/标题相似度辅助

## 6. 数据结构与数据库模型（摘要）

### 6.1 tracks
- 核心字段：`track_id`, `title`, `artist`, `album`
- 路径字段：`storage_relpath`, `source_relpath`, `source_fullpath`
- 去重字段：`source_sha256`, `fingerprint_*`
- 状态字段：`deleted_at`, `file_health`
- 扩展字段：`ext_json(tags/cover 等)`

### 6.2 lyrics
- 核心字段：`lyrics_id`, `text_hash`, `storage_relpath`
- 元数据：`lyrics_title`, `lyrics_artist`, `lyrics_album`, `lyrics_author`
- 状态：`deleted_at`

### 6.3 track_lyrics
- 歌曲与歌词的关联表，支持 `is_primary` 主映射语义。

### 6.4 review_queue
- 导入异常、重复冲突、歌词匹配不确定项统一进入该表。
- `status`：`pending / resolved / ignored`

### 6.5 其它
- `playlists` / `playlist_items`
- `fullscan_works` / `fullscan_work_items`
- `undo_actions`
- `tag_fields`

## 7. 内置播放器（简易实现）

实现文件：`ui/player_bar.py`

能力范围：
- 底部单行控制栏（音量、播放/暂停、进度、上一首、下一首、关闭）
- 仅队列顺序播放，不提供独立播放列表视图
- 关闭即停止并隐藏，不做后台播放持久化

接入规则：
- 所有页面播放入口统一调用顶级窗口方法重建队列。
- 歌单“播放歌单”按当前歌单顺序入队。
- 审查界面支持“同组入队，从点击项起播”。

## 8. 维护约定

1. UI 层禁止直接写数据库。
2. 新功能优先复用 Facade 能力，避免页面间逻辑复制。
3. 导入流程新增分支必须同步更新：
   - `docs/import_pipeline_cn.md`
   - `docs/import_pipeline.md`
4. 单文件超过约 1k 行优先按业务域拆分，不引入过度碎片化模块。

## 9. 指纹分段策略（2026-04）

重复识别采用分段阈值：

- `score >= 0.50`：同一歌曲区间，执行自动保留/替换决策。
- `0.30 <= score < 0.50`：审查区间，进入 `review_queue`。
- `0.10 <= score < 0.30`：原版/伴奏提示区间，默认保留并建立 `instrumental_variant_hint` 关系。
- `0.01 <= score < 0.10`：翻唱提示区间，默认保留并建立 `cover_version_hint` 关系。
- `score < 0.01`：低相关区间，默认视为不同歌曲。

代码入口：

- 判定逻辑：`src/musearc/services/dedupe.py::DuplicateEvaluator.decide`
- 关系落库：`src/musearc/services/importer_pipeline.py`（`repo.add_variant(...)`）
- 默认阈值：`src/musearc/config/models.py::ImportThresholds`
