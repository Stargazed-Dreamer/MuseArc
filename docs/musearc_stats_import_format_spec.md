# MuseArc 播放统计导入格式规范

本文档描述 MuseArc 的 `import_playlist_stats` 接口所接受的 JSON 格式，供播放器端适配导出时参考。

---

## 1. 文件概览

| 属性 | 值 |
|------|-----|
| 文件类型 | JSON |
| 编码 | UTF-8 |
| 建议扩展名 | `.muse_stats.json` 或 `.json` |
| 导入入口 | MuseArc → 导入管理 → 导入统计数据 |

---

## 2. JSON 结构

```json
{
  "schema": "musearc_playlist_export_v1",
  "playlist_hash": "<唯一标识>",
  "playlist_name": "<来源名称>",
  "tracks": [
    {
      "track_id": "trk_<uuid_hex>",
      "source_sha256": "<sha256>",
      "storage_relpath": "data/tracks/ab/trk_xxx.flac",
      "stats": {
        "play_count": 11,
        "manual_play_count": 3,
        "complete_play_count": 8,
        "play_seconds": 116,
        "early_skip_count": 0,
        "peak_session_play_count": 5,
        "peak_session_play_at": 1781375020.0
      }
    }
  ]
}
```

---

## 3. 顶层字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `schema` | string | **是** | 固定值 `"musearc_playlist_export_v1"`，用于格式识别 |
| `playlist_hash` | string | **是** | 来源唯一标识。同一 `playlist_hash` 重复导入会**覆盖**旧数据而非累加；不同 hash 对同一首歌会按来源维度累加。建议基于内容生成（如对 tracks 列表做 SHA256 取前16位） |
| `playlist_name` | string | 否 | 来源名称，用于导入历史展示。缺省时使用文件名 |
| `tracks` | array | **是** | 歌曲统计数组，每个元素为一个对象（见下节） |

### playlist_hash 生成建议

```python
import hashlib, json

content_hash = hashlib.sha256(
    json.dumps(tracks_list, sort_keys=True, ensure_ascii=False).encode("utf-8")
).hexdigest()[:16]
playlist_hash = f"museplayer_stats_{content_hash}"
```

---

## 4. tracks[i] 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `track_id` | string | 推荐 | MuseArc 数据库中的歌曲 ID，格式为 `trk_` + 32位十六进制 UUID（如 `trk_7391cbbefd90428f87bf4e608ed27cd0`）。**注意：MusePlayer 的纯 UUID 需加 `trk_` 前缀** |
| `source_sha256` | string | **强烈推荐** | 音频源文件的 SHA256 哈希值（小写十六进制），是**最高优先级**的匹配依据，可跨库跨路径匹配同一首歌 |
| `storage_relpath` | string | 推荐 | 音频文件在 MuseArc 数据库内的相对路径（如 `data/tracks/ab/trk_xxx.flac`），作为第三匹配依据 |
| `stats` | object | **是** | 播放统计对象（见下节） |

### 匹配优先级

MuseArc 按以下顺序尝试将 tracks[i] 匹配到库内歌曲：

1. **`source_sha256`** — 最高优先级，跨库跨路径均可匹配
2. **`track_id`** — 同库匹配（需要 ID 格式一致）
3. **`storage_relpath`** — 兜底匹配

三个字段至少提供一个。提供越多，匹配成功率越高。

---

## 5. stats 对象字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `play_count` | int | **是** | 总播放次数（含随机播放和手动播放） |
| `manual_play_count` | int | **是** | 用户主动选择播放的次数（非随机播放触发） |
| `complete_play_count` | int | 否 | 完播次数（播放进度达到95%以上时+1） |
| `play_seconds` | int | **是** | 累计播放秒数（整数，向下取整） |
| `early_skip_count` | int | **是** | 早期跳过次数（播放进度不足5%就跳过的次数） |
| `peak_session_play_count` | int | 否 | 历史最高密集播放次数（单次启动期间最高播放次数） |
| `peak_session_play_at` | float | 否 | 历史最高密集播放次数的记录时间戳（Unix epoch 秒） |

### 字段映射参考（MusePlayer → MuseArc）

| MusePlayer 字段 | MuseArc 字段 | 转换说明 |
|-----------------|-------------|---------|
| `play_count` | `stats.play_count` | 直接映射 |
| `active_play_count` | `stats.manual_play_count` | 语义对应：主动播放次数 |
| `complete_play_count` | `stats.complete_play_count` | 直接映射：完播次数 |
| `played_seconds_total` | `stats.play_seconds` | 浮点→整数，`int(played_seconds_total)` |
| `early_skip_count` | `stats.early_skip_count` | 直接映射 |
| `peak_session_play_count` | `stats.peak_session_play_count` | 直接映射 |
| `peak_session_play_at` | `stats.peak_session_play_at` | 直接映射 |
| `played_percent_total` | *(无对应)* | MuseArc 不使用此字段，可忽略 |
| `updated_at` | *(无对应)* | MuseArc 不使用此字段，可忽略 |

---

## 6. 完整示例

### 最小可用示例（仅 track_id）

```json
{
  "schema": "musearc_playlist_export_v1",
  "playlist_hash": "museplayer_stats_a1b2c3d4e5f6a7b8",
  "tracks": [
    {
      "track_id": "trk_7391cbbefd90428f87bf4e608ed27cd0",
      "stats": {
        "play_count": 11,
        "manual_play_count": 0,
        "play_seconds": 116,
        "early_skip_count": 0
      }
    }
  ]
}
```

### 推荐示例（含 source_sha256，匹配率最高）

```json
{
  "schema": "musearc_playlist_export_v1",
  "playlist_hash": "museplayer_stats_a1b2c3d4e5f6a7b8",
  "playlist_name": "playback_stats",
  "tracks": [
    {
      "track_id": "trk_7391cbbefd90428f87bf4e608ed27cd0",
      "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "storage_relpath": "data/tracks/73/trk_7391cbbefd90428f87bf4e608ed27cd0.flac",
      "stats": {
        "play_count": 11,
        "manual_play_count": 3,
        "complete_play_count": 8,
        "play_seconds": 116,
        "early_skip_count": 0,
        "peak_session_play_count": 5,
        "peak_session_play_at": 1781375020.0
      }
    },
    {
      "track_id": "trk_3234fed98f7a430198bf9376273f4ebe",
      "source_sha256": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
      "stats": {
        "play_count": 4,
        "manual_play_count": 1,
        "complete_play_count": 2,
        "play_seconds": 60,
        "early_skip_count": 2,
        "peak_session_play_count": 3,
        "peak_session_play_at": 1781375020.0
      }
    }
  ]
}
```

---

## 7. 导入后效果

导入成功后，MuseArc 会为每首匹配到的歌曲写入以下标签：

| 标签名 | 来源 | 说明 |
|--------|------|------|
| `播放次数` | 所有来源 `play_count` 之和 | 累加不同 playlist_hash 的贡献 |
| `指定播放次数` | 所有来源 `manual_play_count` 之和 | 同上 |
| `完播次数` | 所有来源 `complete_play_count` 之和 | 同上 |
| `播放秒数` | 所有来源 `play_seconds` 之和 | 同上 |
| `早期跳过次数` | 所有来源 `early_skip_count` 之和 | 同上 |
| `最高密集播放次数` | 所有来源 `peak_session_play_count` 最大值 | 取最大值（非累加） |
| `最高密集播放时间` | 对应 `peak_session_play_at` | 与最高密集播放次数同来源 |
| `喜爱程度` | 公式计算 | -100~100 整数，基于上述统计综合计算 |

### 喜爱程度计算公式

```
a = play_count
b = manual_play_count
c = play_seconds
d = early_skip_count
e = 全库总播放次数（所有歌曲 a 求和）
f = 歌曲长度秒数
g = complete_play_count
h = peak_session_play_count

t1 = c / f / a        (平均每次播放完整度，近似值)
t1b = g / a           (完播率，直接测量，有数据时参与)
t2 = b / a            (主动播放占比)
t3 = a / e            (相对播放频次)
t4 = d / a            (早期跳过占比)
t5 = min(h / a, 1.0)  (密集播放信号，单次会话重复播放越多越喜欢)

有完播数据时:
  t = 0.1*t3 + 0.2*t1 + 0.2*t1b + 0.4*t2 + 0.1*t5 - t4
无完播数据时:
  t = 0.1*t3 + 0.4*t1 + 0.4*t2 + 0.1*t5 - t4
喜爱程度 = clamp(round(t * 100), -100, 100)
```

---

## 8. 去重规则

- 同一 `playlist_hash` 再次导入：**覆盖**该来源的旧贡献值，不会重复累加
- 不同 `playlist_hash` 对同一首歌：按来源维度**累加**
- 导入历史对相同 `playlist_hash` 或相同源文件路径只保留最新一条记录

---

## 9. 适配检查清单

播放器端导出适配时，请确认以下事项：

- [ ] 顶层包含 `"schema": "musearc_playlist_export_v1"`
- [ ] 顶层包含 `playlist_hash`（基于内容生成，确保相同数据产生相同 hash）
- [ ] `tracks` 为数组（非字典），每个元素含 `stats` 嵌套对象
- [ ] `track_id` 加了 `trk_` 前缀（MuseArc 格式为 `trk_<uuid>`，非纯 UUID）
- [ ] 尽量提供 `source_sha256`（音频文件 SHA256，匹配率最高）
- [ ] `play_seconds` 为整数（非浮点）
- [ ] `active_play_count` 映射为 `manual_play_count`
- [ ] `played_seconds_total`（浮点）映射为 `play_seconds`（整数）
