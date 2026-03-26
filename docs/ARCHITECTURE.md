# 架构设计

## 1. 目标与原则

- 核心能力（导入、去重、曲词匹配、导出、搜索）不依赖 UI。
- 所有库状态持久化到音乐库目录下，不依赖全局数据库。
- 面向后续扩展：播放器统计、喜好等级、更多元数据、外部服务接入。

## 2. 音乐库目录规范

```
<library_root>/
  db/musearc.db
  data/tracks/<2-char-shard>/<track_id>.opus
  data/lyrics/<2-char-shard>/<lyrics_id>.lrc
  manifests/imports/<import_batch_id>.json
  exports/
  trash/
```

说明：

- 音频只保存 `opus`，与源文件解耦。
- 所有内部文件使用 `track_id/lyrics_id` 命名，避免重名冲突。
- 用户感知名（歌名/作者）在数据库中维护，不参与路径命名。

## 3. 数据库设计（SQLite）

核心表：

- `tracks`：歌曲主表（含声纹、质量分、健康状态、可扩展 `ext_json`）
- `track_variants`：同曲不同版或高相似版本关系
- `lyrics`：歌词文件实体（原始来源 + 统一存储路径）
- `track_lyrics`：曲词关联（含匹配置信度、是否主歌词）
- `review_queue`：待人工审查队列
- `playlists` / `playlist_items`：库内歌单
- `import_batches`：导入批次报告
- `library_meta`：库元信息

## 4. 媒体后端（DLL/库调用）

- 不通过 `ffmpeg.exe` 参数启动外部进程。
- 使用 `PyAV` 直接调用底层编解码库（DLL）完成：
  - 探测（替代 ffprobe）
  - 解码/重采样
  - 编码导出（opus/mp3/flac/wav）

## 5. 去重策略（当前实现）

### 5.1 多层判定

1. 候选过滤：时长窗口（默认 ±6 秒）
2. 声纹相似度：
   - 解码成统一采样率音频
   - 提取 12 维音调特征序列
   - 生成音调转移编码（增强指纹）
   - 计算位移容错序列匹配 + 分布余弦相似
3. 决策：
   - 高相似：判断是否是 Live/Remix/Radio Edit 等版本差异
   - 高相似且非版本差异：按质量分决定替换或保留旧版本
   - 中等相似：进审查队列

### 5.2 特殊风险控制

- 同曲不同版：通过标题关键词推断版本类型，避免误删。
- 低质覆盖高质：引入 `quality_score` 比较。
- 采样误判：中间置信度进入审查，不自动删。

## 6. 曲词匹配策略（当前实现）

- 读取歌词时尝试多编码（utf-8/gb18030/utf-16 等）。
- 规则评分：文件名相似度 + token overlap + 标题近似。
- 可选 LLM 评分：接入 LM Studio 本地模型（OpenAI 兼容接口）。
- 低置信度全部入 `review_queue`，并保留建议匹配轨道供人工确认。

## 7. 可扩展性预留

- `tracks.ext_json`：后续写入播放次数、喜好等级、来源可信度。
- `review_queue`：后续可接“自动修复建议/一键应用”。
- `app.facade.MuseArcFacade`：给 UI 与播放器统一调用，不依赖 CLI。

## 8. 与播放器合并接口

UI 在曲目详情/列表项上预留动作（当前为占位）：

- `播放`
- `加到播放器歌单`
