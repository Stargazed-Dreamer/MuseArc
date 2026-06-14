# 歌单导出格式（`musearc_playlist_export_v2`）

## 文件类型

- 扩展名：`*.muse_playlist.json`
- 编码：`UTF-8`
- 顶层字段：
  - `schema`：固定为 `musearc_playlist_export_v2`
  - `playlist_hash`：歌单唯一哈希（由导出时歌曲顺序+导出时间计算）
  - `playlist_name`：导出时的歌单名称
  - `ordered`：布尔值，表示歌单的曲目顺序是否有意义（默认 `true`）。当设为 `false` 时，导入后播放器可按自身排序规则重排曲目；当设为 `true` 时，若用户勾选"优先使用歌单指定的顺序"，则保持导出时的原始顺序
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
- `lyrics`：歌词文件数组（支持多个歌词文件，见下方歌词格式说明）
- `lyrics_storage_relpath`：主歌词文件相对路径（向后兼容，取 `lyrics[0].relpath`）
- `stats`：播放器回填统计对象
  - `play_count`：播放次数（整数）
  - `manual_play_count`：指定播放次数（整数）
  - `complete_play_count`：完播次数（整数，播放进度达到95%以上时+1）
  - `play_seconds`：播放总秒数（整数）
  - `early_skip_count`：早期跳过次数（前 5% 没播完就跳过）
  - `peak_session_play_count`：历史最高密集播放次数（整数，单次启动期间最高播放次数）
  - `peak_session_play_at`：历史最高密集播放次数的记录时间戳（浮点，Unix epoch 秒）

## 播放器内部扩展字段

以下字段不在导出格式中，但在播放器内部 `Track` 实体中使用：

- `extra_lyrics_paths`：额外歌词文件路径，用 `|` 分隔。拖入歌词文件时自动填充：第一个拖入的歌词设为主歌词（`source_lyrics_path`），后续拖入的添加到此字段
- `source_lyrics_path`：主歌词文件的绝对路径（播放器内部使用，对应导出格式中的 `lyrics_storage_relpath`）

## 歌词格式说明

每首歌曲的 `lyrics` 字段是一个数组，允许关联多个歌词文件。每个元素包含：

- `relpath`：歌词文件相对路径（相对于 `database_location`）
- `lang`：歌词语言类型，取值如下：
  - `"original"`：原始歌词（默认）
  - `"japanese"`：日语原文歌词（QQ音乐 QRC 格式，文件名含 `_qm`）
  - `"romaji"`：罗马音歌词（文件名含 `_qmRoma`）
  - `"translation"`：翻译歌词（文件名含 `_qmts`）

### 歌词文件格式

播放器支持以下歌词文件格式：

1. **LRC 格式**（`.lrc`）
   - 标准时间标签格式：`[mm:ss.xx]歌词文本`
   - 支持毫秒精度

2. **QRC 格式**（`.qrc` / `.qrc.txt`）
   - QQ音乐增强歌词格式，可包含 XML 封装
   - 行级时间标签：`[start_ms,duration_ms]文本`
   - 字级时间标签：`文本(start_ms,duration_ms)`（解析时用于提取纯文本）
   - `[kana:...]` 行：假名注音数据，用于在日语歌词上方显示平假名发音
   - 自动检测：文件名含 `_qm`、`_qmRoma`、`_qmts` 或内容匹配 QRC 模式

### 多歌词合并显示

当一首歌曲关联了多个歌词文件时（如日语原文 + 罗马音 + 翻译），播放器会按时间戳自动合并，在同一歌词行中分行显示：

- 第一行：假名注音（平假名，严格对齐日语原文汉字位置，无对应则不写）
- 第二行：日语原文（汉字间距加宽以对齐上方注音）
- 第三行：罗马音（空格断句对齐，无需完全对应日语歌词）
- 第四行：中文翻译（正常显示，无需对齐）

用户可通过设置控制显示：
- ☐ 显示日语歌词
- ☐ 日语歌词显示罗马音

### 拖入歌词文件

将 `.lrc` 或 QRC 格式文件拖入播放器窗口时：
- 如果当前正在播放歌曲，歌词文件会自动关联到该歌曲
- 第一个拖入的歌词设为主歌词（`source_lyrics_path`），后续拖入的添加到额外歌词列表（`extra_lyrics_paths`，用 `|` 分隔）
- 关联信息在下次保存时持久化到播放器状态/歌单文件

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
  - `完播次数`
  - `播放秒数`
  - `早期跳过次数`
  - `最高密集播放次数`
  - `最高密集播放时间`
  - `喜爱程度`（-100~100 整数）

## 喜爱程度计算（当前实现）

- 范围：`-100 ~ 100`
- 记号：
  - `a = play_count`
  - `b = manual_play_count`
  - `c = play_seconds`
  - `d = early_skip_count`
  - `g = complete_play_count`
  - `h = peak_session_play_count`
  - `e = 全库总播放次数（所有歌曲 a 求和）`
  - `f = 歌曲长度秒数`
- 公式：
  - `t1 = c / f / a`（平均每次播放完整度，近似值）
  - `t1b = g / a`（完播率，直接测量）
  - `t2 = b / a`（主动播放占比）
  - `t3 = a / e`（相对播放频次）
  - `t4 = d / a`（早期跳过占比）
  - `t5 = min(h / a, 1.0)`（密集播放信号）
  - 有完播数据时：`t = 0.1*t3 + 0.2*t1 + 0.2*t1b + 0.4*t2 + 0.1*t5 - t4`
  - 无完播数据时：`t = 0.1*t3 + 0.4*t1 + 0.4*t2 + 0.1*t5 - t4`
  - `喜爱程度 = clamp(round(t*100), -100, 100)`

## v1 → v2 变更

| 项目 | v1 | v2 |
|------|----|----|
| schema | `musearc_playlist_export_v1` | `musearc_playlist_export_v2` |
| `ordered` | 无 | 新增，布尔值，表示曲目顺序是否有意义 |
| `lyrics` | 无 | 新增，歌词文件数组，支持多语言歌词 |
| `lyrics_storage_relpath` | 单个字符串 | 保留，向后兼容，取 lyrics[0].relpath |
| 歌词语言 | 无 | 通过 `lang` 字段区分 original/japanese/romaji/translation |
| 假名注音 | 无 | QRC 格式支持 `[kana:...]` 行，用于日语歌词汉字上方显示平假名 |
