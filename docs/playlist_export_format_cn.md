# 歌单导出格式（`musearc_playlist_export_v1`）

## 文件类型

- 扩展名：`*.muse_playlist.json`
- 编码：`UTF-8`
- 顶层字段：
  - `schema`：固定为 `musearc_playlist_export_v1`
  - `playlist_hash`：歌单唯一哈希（由导出时歌曲顺序+导出时间计算）
  - `playlist_name`：导出时的歌单名称
  - `exported_at`：UTC ISO8601 时间
  - `database_location`：导出时数据库根目录绝对路径
  - `track_count`：歌曲数量
  - `stats_summary`：统计汇总保留字段（对象）
  - `tracks`：歌曲数组

## tracks[i] 字段

- `track_id`：数据库歌曲 ID（如 `trk_xxx`）
- `storage_relpath`：数据库内音频文件相对路径（如 `data/tracks/ab/trk_xxx.flac`）
- `source_sha256`：音频来源 SHA256（用于跨库/跨路径匹配）
- `title`：标题
- `artist`：艺术家
- `album`：专辑
- `lyrics_storage_relpath`：数据库内歌词相对路径（无则空字符串）
- `stats`：播放器回填统计对象
  - `play_count`：播放次数（整数）
  - `manual_play_count`：指定播放次数（整数）
  - `play_seconds`：播放总秒数（整数）
  - `early_skip_count`：早期跳过次数（前 5% 没播完就跳过）

## 统计回导规则

- 使用 `playlist_hash` 去重：
  - 同一 `playlist_hash` 再次导入会覆盖旧贡献，不会重复累加。
- 不同歌单（不同 `playlist_hash`）对同一首歌会按歌单维度累加。
- 映射优先级：
  1. `source_sha256`
  2. `track_id`
  3. `storage_relpath`
- 导入后会写入标签：
  - `播放次数`
  - `指定播放次数`
  - `播放秒数`
  - `早期跳过次数`
  - `喜爱程度`（-100~100 整数）

## 喜爱程度计算（当前实现）

- 范围：`-100 ~ 100`
- 记号：
  - `a = play_count`
  - `b = manual_play_count`
  - `c = play_seconds`
  - `d = early_skip_count`
  - `e = 全库总播放次数（所有歌曲 a 求和）`
  - `f = 歌曲长度秒数`
- 公式：
  - `t1 = c / f / a`
  - `t2 = b / a`
  - `t3 = a / e`
  - `t4 = d / a`
  - `t = 0.1*t3 + 0.4*t1 + 0.5*t2 - t4`
  - `喜爱程度 = clamp(round(t*100), -100, 100)`
