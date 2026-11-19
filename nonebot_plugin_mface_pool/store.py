"""mface / 收藏表情 表情池：去重、容量淘汰、原子 JSON 落盘。

纯标准库实现，不依赖 NoneBot，方便单独自检。

⚠️ 协议要点（已对运行中的 NapCat 实测确认，勿改）：
  * 商城表情 - 接收：NapCat 转成 **image 段**，额外附带
    `emoji_id` / `emoji_package_id` / `key`，**没有** `type=mface` 的来报段。
  * 商城表情 - 发送：`{"type": "mface", "data": {emoji_package_id, emoji_id, key, summary}}`。
  * 收藏表情 - 接收：image 段 + `sub_type=1` + `summary=[动画表情]`，
    没有任何 emoji_* 字段；`file` 是内容 MD5，可作稳定去重键。
  * 收藏表情 - 发送：`{"type": "image", "data": {file, sub_type: 1, summary}}`。
    已实测：NapCat 接受该形态，QQ 客户端渲染为正常收藏表情贴图。
"""

from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import time
from collections import deque
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# 判定「这个 image 段其实是商城表情」所必需的字段
MFACE_REQUIRED_KEYS = ("emoji_id", "emoji_package_id")

# 收藏表情在 image 段上的特征：sub_type == 1（已实测）
STICKER_SUB_TYPE = 1

# summary 缺失时的展示回退（NapCat 自身也用同样的占位）
DEFAULT_SUMMARY = "[商城表情]"

# 记录类型
KIND_MFACE = "mface"
KIND_STICKER = "sticker"

# 发送时的 OneBot 段类型（收藏表情走 image + sub_type=1）
KIND_STICKER_SEGMENT = "image"

# 收藏表情的图片字节目录名（相对 pool.json 所在目录）
BLOB_DIR_NAME = "blobs"
BLOB_SUFFIX = ".bin"
# 单个表情允许保存的最大字节数，超过则丢弃（避免把磁盘撑爆）
DEFAULT_MAX_BLOB_BYTES = 5 * 1024 * 1024

# ── 按收集频数加权挑选的常量 ────────────────────────────────────
# 权重 = (seen + 1) ** power
#   * 用 seen + 1 而不是 seen：否则所有 seen=1 的表情权重都是 1，
#     建池初期毫无区分度，等于没加权。
#   * 默认 power=1.2 由 MFACE_WEIGHT_POWER 控制（1.0 = 线性，越大越偏高频）。
DEFAULT_WEIGHT_POWER = 1.2
MIN_WEIGHT_POWER = 0.1  # 指数过小/为 0 时权重会全部退化为 1
MAX_WEIGHT_POWER = 5.0  # 防止配置写错导致幂爆炸
MAX_SEEN = 1_000_000  # 防止脏数据里异常大的 seen 撑爆幂运算

# ── 时间衰减（压住「以前很热、现在没人发」的表情）────────────────
# 权重 = 频数权重 × 0.5 ** (距今未出现天数 / 半衰期天数)
#   即：多久没被再次收集就衰减多少。0 = 关闭衰减，退回纯频数加权。
DEFAULT_DECAY_HALF_LIFE_DAYS = 14.0
_SECONDS_PER_DAY = 86400.0


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def unify_id(record: Mapping[str, Any]) -> str:
    """去重用的稳定唯一标识，按类型各用一套键。

    * 商城表情：`emoji_package_id:emoji_id`
      `emoji_id` 是内容寻址的 hex id（收发两侧一致），叠加表情包 id 后
      可保证「同一个表情」永不重复入库，即使 key 换了也仍算同一条。
    * 收藏表情：`file`（内容是 MD5），同一个贴图重复发送得到同一个值。

    注意：商城表情的 `file` 是 NapCat 依据 emoji_id 拼出来的 gif 名，
    不同版本/不同入口可能不同，所以**不能**用 file 当商城表情的身份。
    """
    if record_kind(record) == KIND_STICKER:
        return _thin_file(record.get("file"))
    return f"{_int(record.get('emoji_package_id'))}:{_text(record.get('emoji_id'))}"


# 兼容旧名字
mface_id = unify_id


def record_kind(record: Mapping[str, Any]) -> str:
    """记录类型：有 key 的按商城表情发，否则按收藏表情发。"""
    kind = _text(record.get("kind"))
    if kind in (KIND_MFACE, KIND_STICKER):
        return kind
    if _text(record.get("key")):
        return KIND_MFACE
    return KIND_STICKER if _thin_file(record.get("file")) else KIND_MFACE


_HEX_RE = re.compile(r"^[0-9a-fA-F]{16,}$")


def _thin_file(value: Any) -> str:
    """取收藏表情的稳定标识：`file` 的纯文件名部分。

    NapCat 发送/落地时可能带上子目录前缀，形如 `1f-FBE714....png`，
    而收报里是裸文件名 `FBE714....png`；两者必须视为同一条。
    只有当「剥掉前缀后」确实是一长串 hex（MD5）时才剥，
    以免误伤商城表情那种 `1f-1f600.gif` 的命名。
    """
    text = _text(value)
    if not text:
        return ""
    if "/" in text or "\\" in text:
        text = text.replace("\\", "/").rsplit("/", 1)[-1]
    if "-" in text:
        _prefix, _, remainder = text.partition("-")
        stem = remainder.rsplit(".", 1)[0]
        if _HEX_RE.match(stem):
            return remainder
    return text


def mface_from_segment(segment: Any) -> dict[str, Any] | None:
    """商城表情：解析成 mface 记录；不是则返回 None。

    同时兼容两种形态：
      * 当前 NapCat：`type == "image"` 且带 `emoji_id` + `emoji_package_id`
      * 以后 NapCat 若改回原生 `type == "mface"` 段
    普通图片（无 emoji_id）与 face 小黄脸会被自然排除。
    """
    try:
        seg_type = str(getattr(segment, "type", "") or "")
        raw = getattr(segment, "data", None)
    except Exception:  # pragma: no cover - 防御性兜底
        return None
    if not isinstance(raw, Mapping):
        return None

    is_real_mface = seg_type == "mface"
    looks_like_mface = seg_type == "image" and all(key in raw for key in MFACE_REQUIRED_KEYS)
    if not (is_real_mface or looks_like_mface):
        return None

    emoji_id = _text(raw.get("emoji_id"))
    package_id = _int(raw.get("emoji_package_id"))
    if not emoji_id or package_id is None or package_id < 0:
        return None

    return {
        "kind": KIND_MFACE,
        "emoji_id": emoji_id,
        "emoji_package_id": package_id,
        "key": _text(raw.get("key")),
        "summary": _text(raw.get("summary")) or DEFAULT_SUMMARY,
        "file": _thin_file(raw.get("file")),
        "url": _text(raw.get("url")),
    }


def sticker_from_segment(segment: Any) -> dict[str, Any] | None:
    """收藏表情：image 段 + `sub_type=1` + 非空 file；不是则返回 None。

    普通图片（sub_type=0）会被排除；商城表情因带 emoji_id 走 mface 分支，
    这里天然不会重复收集。
    """
    try:
        seg_type = str(getattr(segment, "type", "") or "")
        raw = getattr(segment, "data", None)
    except Exception:  # pragma: no cover - 防御性兜底
        return None
    if seg_type != "image" or not isinstance(raw, Mapping):
        return None
    if all(key in raw for key in MFACE_REQUIRED_KEYS):
        return None  # 商城表情交给 mface 分支
    if _int(raw.get("sub_type")) != STICKER_SUB_TYPE:
        return None

    file_ref = _thin_file(raw.get("file"))
    if not file_ref:
        return None

    return {
        "kind": KIND_STICKER,
        "file": file_ref,
        "url": _text(raw.get("url")),
        "summary": _text(raw.get("summary")) or DEFAULT_SUMMARY,
        "emoji_id": "",
        "emoji_package_id": 0,
        "key": "",
    }


def record_from_segment(segment: Any) -> dict[str, Any] | None:
    """把消息段解析成可收集的记录（商城表情优先，其次收藏表情）。"""
    return mface_from_segment(segment) or sticker_from_segment(segment)


def is_sendable(record: Mapping[str, Any]) -> bool:
    """是否具备发送所需的全部字段。

    * 商城表情：NapCat 的 mface schema 要求 4 个字段齐备；
      缺少 key 的条目仍可收集，但不参与随机发送。
    * 收藏表情：必须已保存图片字节（``_has_data``）。
      只有裸 MD5（file）或 url 都**不够**——NapCat 发送接口不认裸 MD5，
      url 带 rkey 几分钟就过期，所以才要在收集时把字节存下来。
    """
    if record_kind(record) == KIND_STICKER:
        if not _thin_file(record.get("file")):
            return False
        # 必须是「已保存的真实图片字节」。实测 url 的 rkey 几分钟就失效、
        # 裸 MD5 只有 NapCat 缓存里还在时才有效，所以只认字节。
        return bool(record.get("_has_data"))

    if not _text(record.get("emoji_id")):
        return False
    if _int(record.get("emoji_package_id")) is None:
        return False
    return bool(_text(record.get("key")))


def with_send_payload(pool: MFacePool, record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """在记录上按需挂载图片字节，再生成发送用 (段类型, data)。

    收藏表情的 base64 只在真正要发送时从磁盘读出，避免池子一大就常驻内存。
    """
    if record_kind(record) == KIND_STICKER and not record.get("_data_b64"):
        data_b64 = pool._load_blob_b64(record)
        if data_b64:
            record["_data_b64"] = data_b64
    return to_send_segment(record)


def to_send_segment(record: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """把记录转成发送用的 (段类型, data)。

    * 商城表情 → ("mface", {emoji_package_id, emoji_id, key, summary})
    * 收藏表情 → ("image", {file, sub_type: 1, summary})
    """
    if record_kind(record) == KIND_STICKER:
        data: dict[str, Any] = {
            "sub_type": STICKER_SUB_TYPE,
            "summary": _text(record.get("summary")) or DEFAULT_SUMMARY,
        }
        data_b64 = _text(record.get("_data_b64"))
        if data_b64:
            # 首选：收集时保存的真实图片数据，编码为 base64 投喂（已实测可发送）
            data["file"] = "base64://" + data_b64
        else:
            # 兜底：没有字节数据时退回 url（可能已过期，仅在有 url 时才可能成功）
            url = _text(record.get("url"))
            if url:
                data["file"] = url
            else:
                data["file"] = _thin_file(record.get("file"))
        return KIND_STICKER_SEGMENT, data

    return KIND_MFACE, {
        "emoji_package_id": int(record["emoji_package_id"]),
        "emoji_id": _text(record["emoji_id"]),
        "key": _text(record["key"]),
        "summary": _text(record.get("summary")) or DEFAULT_SUMMARY,
    }


def clamp_weight_power(value: Any) -> float:
    """把权重指数收敛到安全区间；非法值回落到默认值。"""
    try:
        power = float(value)
    except (TypeError, ValueError):
        return DEFAULT_WEIGHT_POWER
    if not math.isfinite(power):
        return DEFAULT_WEIGHT_POWER
    return min(MAX_WEIGHT_POWER, max(MIN_WEIGHT_POWER, power))


def clamp_decay_half_life(value: Any) -> float:
    """把时间衰减半衰期（天）收敛到合法值；非法回落默认，负数视为关闭。"""
    try:
        half_life = float(value)
    except (TypeError, ValueError):
        return DEFAULT_DECAY_HALF_LIFE_DAYS
    if not math.isfinite(half_life):
        return DEFAULT_DECAY_HALF_LIFE_DAYS
    if half_life < 0:
        return 0.0
    return half_life


def _last_seen_value(item: Mapping[str, Any]) -> float:
    """取 `last_seen` 用于排序/打平；缺失或非法按 0（视为最旧）处理。"""
    value = item.get("last_seen")
    if not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return 0.0
    return number


def item_weight(
    item: Mapping[str, Any],
    power: float = DEFAULT_WEIGHT_POWER,
    decay_half_life_days: float = DEFAULT_DECAY_HALF_LIFE_DAYS,
    now: float | None = None,
) -> float:
    """挑选权重 = 频数权重 × 时间衰减因子。

        (seen + 1) ** power  ×  0.5 ** (距今未出现天数 / 半衰期天数)

    频数部分：`seen` 缺失/为 0/负数都按 1 处理；异常大的值会被 clamp，
    避免脏数据把幂运算撑爆。

    衰减部分：多久没被再次收集就衰减多少，用来压住「以前很热、现在没人发」
    的表情。`decay_half_life_days <= 0` 表示关闭衰减，行为等同纯频数加权。
    `last_seen` 缺失或不在将来以外的异常值都按「刚出现」处理（因子 1.0）；
    未来时间戳（时钟回拨/脏数据）会被夹住，**绝不放大**权重。
    """
    seen = _int(item.get("seen"))
    if seen is None or seen < 1:
        seen = 1
    seen = min(seen, MAX_SEEN)
    weight = (seen + 1) ** clamp_weight_power(power)

    half_life = clamp_decay_half_life(decay_half_life_days)
    if half_life > 0:
        last_seen = item.get("last_seen")
        if not isinstance(last_seen, (int, float)) or not math.isfinite(float(last_seen)):
            # 没有可靠时间戳时按刚出现处理，避免被误判成极远期而衰减到 0
            return weight
        reference = time.time() if now is None else now
        age_days = max(0.0, (reference - float(last_seen)) / _SECONDS_PER_DAY)
        weight *= 0.5 ** (age_days / half_life)

    return weight


def _normalize_item(raw: Any) -> dict[str, Any] | None:
    """把磁盘上的一条记录规整成内部形态；无法修复则丢弃。"""
    if not isinstance(raw, Mapping):
        return None

    file_ref = _thin_file(raw.get("file"))
    emoji_id = _text(raw.get("emoji_id"))
    package_id = _int(raw.get("emoji_package_id"))

    # 收藏表情只需要 file；商城表情需要 emoji_id + package_id
    if not file_ref and (not emoji_id or package_id is None or package_id < 0):
        return None

    now = time.time()
    first_seen = raw.get("first_seen")
    last_seen = raw.get("last_seen")
    return {
        "kind": record_kind(raw),
        "file": file_ref,
        "url": _text(raw.get("url")),
        "emoji_id": emoji_id,
        "emoji_package_id": package_id if package_id is not None else 0,
        "key": _text(raw.get("key")),
        "summary": _text(raw.get("summary")) or DEFAULT_SUMMARY,
        "first_seen": float(first_seen) if isinstance(first_seen, (int, float)) else now,
        "last_seen": float(last_seen) if isinstance(last_seen, (int, float)) else now,
        "seen": max(1, _int(raw.get("seen")) or 1),
    }


class MFacePool:
    """内存表情池 + JSON 持久化。

    所有改内存的操作都是同步的（无 await），因此天然不会被事件循环打断；
    只有写盘是异步的，并用一把 asyncio.Lock 串行化、以「临时文件 + os.replace」
    原子替换，避免并发写坏文件。
    """

    def __init__(
        self,
        store_path: Path,
        pool_max: int,
        recent_exclude: int,
        weight_power: float = DEFAULT_WEIGHT_POWER,
        decay_half_life_days: float = DEFAULT_DECAY_HALF_LIFE_DAYS,
        blob_dir: Path | None = None,
    ) -> None:
        self.store_path = Path(store_path)
        # 图片字节目录：默认放在 pool.json 同级的 blobs/ 下
        self.blob_dir = Path(blob_dir) if blob_dir is not None else self.store_path.parent / BLOB_DIR_NAME
        self.pool_max = max(1, int(pool_max))
        self.recent_exclude = max(0, int(recent_exclude))
        # 按收集频数加权的指数：权重 = (seen + 1) ** weight_power
        self.weight_power = clamp_weight_power(weight_power)
        # 时间衰减半衰期（天）；0 = 关闭衰减
        self.decay_half_life_days = clamp_decay_half_life(decay_half_life_days)
        self.items: list[dict[str, Any]] = []
        self.recent_sent: deque[str] = deque(maxlen=self.recent_exclude)
        self._lock = None  # 延迟到有事件循环时再创建
        self.load()

    # ── 持久化 ──────────────────────────────────────────────────

    def _get_lock(self):
        if self._lock is None:
            import asyncio

            self._lock = asyncio.Lock()
        return self._lock

    def load(self) -> None:
        path = self.store_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._quarantine(exc)
            return

        raw_items: Iterable[Any] = ()
        raw_recent: Iterable[Any] = ()
        if isinstance(data, Mapping):
            raw_items = data.get("items") or ()
            raw_recent = data.get("recent_sent") or ()
        elif isinstance(data, list):  # 兼容早期纯列表格式
            raw_items = data

        normalized = []
        for raw in raw_items:
            item = _normalize_item(raw)
            if item is not None:
                normalized.append(item)
        # 超限时只保留尾部（最近最活跃的）
        self.items = normalized[-self.pool_max :]

        recent: list[str] = []
        for token in raw_recent:
            text = _text(token)
            if text and text not in recent:
                recent.append(text)
        if self.recent_exclude > 0:
            self.recent_sent = deque(recent[-self.recent_exclude :], self.recent_exclude)

        self._refresh_blob_flags()

    def _quarantine(self, exc: Exception) -> None:
        stamp = time.strftime("%Y%m%d%H%M%S")
        backup = self.store_path.with_name(f"{self.store_path.name}.corrupt-{stamp}")
        try:
            os.replace(self.store_path, backup)
        except OSError:
            backup = None  # type: ignore[assignment]
        self.items = []
        self.recent_sent = deque(maxlen=self.recent_exclude)
        from nonebot import logger

        if backup:
            logger.warning(
                "[mface_pool] 表情池文件解析失败({}: {})，已备份为 {}，本次从空池开始",
                exc.__class__.__name__,
                exc,
                backup.name,
            )
        else:
            logger.warning(
                "[mface_pool] 表情池文件解析失败({}: {})，且备份失败，本次从空池开始",
                exc.__class__.__name__,
                exc,
            )

    # ── 图片字节（blob）─────────────────────────────────────────

    def _blob_path(self, record: Mapping[str, Any]) -> Path:
        name = _thin_file(record.get("file"))
        if not name:
            return self.blob_dir / f"unknown{BLOB_SUFFIX}"
        return self.blob_dir / f"{name}{BLOB_SUFFIX}"

    def _refresh_blob_flags(self) -> None:
        """按磁盘上的 blob 文件，刷新每个条目的 _has_data / _data_b64。

        内存里只保留「有字节数据」这个布尔标记，不预加载 base64，
        避免池子一大就吃掉几十 MB 内存。
        """
        for item in self.items:
            if record_kind(item) != KIND_STICKER:
                continue
            exists = self._blob_path(item).is_file()
            item["_has_data"] = exists
            item.pop("_data_b64", None)

    def save_blob(self, record: Mapping[str, Any], data: bytes) -> bool:
        """保存收藏表情的图片字节；失败只告警。"""
        from nonebot import logger

        if not data:
            return False
        if len(data) > DEFAULT_MAX_BLOB_BYTES:
            logger.warning(
                "[mface_pool] 表情字节过大({} bytes)，跳过保存 file={}",
                len(data),
                _thin_file(record.get("file")) or "-",
            )
            return False
        try:
            self.blob_dir.mkdir(parents=True, exist_ok=True)
            path = self._blob_path(record)
            tmp = path.with_name(f"{path.name}.tmp")
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            record["_has_data"] = True
            record.pop("_data_b64", None)
            return True
        except Exception as exc:
            logger.warning(
                "[mface_pool] 保存表情字节失败 file={}: {}: {}",
                _thin_file(record.get("file")) or "-",
                exc.__class__.__name__,
                exc,
            )
            return False

    def _load_blob_b64(self, record: Mapping[str, Any]) -> str:
        """按需把 blob 读成 base64（仅在真正要发送时调用）。"""
        try:
            path = self._blob_path(record)
            if not path.is_file():
                return ""
            return base64.b64encode(path.read_bytes()).decode()
        except Exception:
            return ""

    def _delete_blob(self, record: Mapping[str, Any]) -> None:
        try:
            self._blob_path(record).unlink(missing_ok=True)
        except Exception:
            pass

    def _gc_blobs(self) -> int:
        """清理孤儿 blob（不在池内的 .bin），返回删除数量。"""
        if not self.blob_dir.is_dir():
            return 0
        keep = {
            f"{_thin_file(i.get('file'))}{BLOB_SUFFIX}"
            for i in self.items
            if record_kind(i) == KIND_STICKER and _thin_file(i.get("file"))
        }
        removed = 0
        try:
            for path in self.blob_dir.glob(f"*{BLOB_SUFFIX}"):
                if path.name not in keep:
                    path.unlink(missing_ok=True)
                    removed += 1
        except Exception:
            pass
        return removed

    def _dump(self) -> str:
        return json.dumps(
            {
                "version": SCHEMA_VERSION,
                # 剔除内部字段（下划线开头），保持 JSON 干净
                "items": [{k: v for k, v in item.items() if not k.startswith("_")} for item in self.items],
                "recent_sent": list(self.recent_sent),
            },
            ensure_ascii=False,
            indent=2,
        )

    async def save(self) -> bool:
        """原子落盘；失败只告警，不影响内存状态与回复流程。"""
        from nonebot import logger

        try:
            async with self._get_lock():
                self.store_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.store_path.with_name(f"{self.store_path.name}.tmp")
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(self._dump())
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.store_path)
                try:
                    dir_fd = os.open(str(self.store_path.parent), os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            return True
        except Exception as exc:
            logger.warning("[mface_pool] 写入表情池失败: {}: {}", exc.__class__.__name__, exc)
            return False

    # ── 池操作 ──────────────────────────────────────────────────

    def add(self, record: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """加入一条记录；返回 (记录, 是否新增)。重复则刷新 key/摘要与活跃时间。"""
        target = unify_id(record)
        now = time.time()

        for index, existing in enumerate(self.items):
            if unify_id(existing) != target:
                continue
            existing["first_seen"] = existing.get("first_seen", now)
            existing["last_seen"] = now
            existing["seen"] = int(existing.get("seen") or 1) + 1
            if _text(record.get("key")):
                existing["key"] = _text(record["key"])
            summary = _text(record.get("summary"))
            if summary:
                existing["summary"] = summary
            # 刷新 url（新一次收集会带来新的 rkey）与字节标记
            if _text(record.get("url")):
                existing["url"] = _text(record["url"])
            existing["_has_data"] = bool(record.get("_has_data") or self._blob_path(existing).is_file())
            self.items.append(self.items.pop(index))  # 移到尾部 = 最近活跃
            return existing, False

        item = {
            "kind": record_kind(record),
            "file": _thin_file(record.get("file")),
            "url": _text(record.get("url")),
            "emoji_id": _text(record.get("emoji_id")),
            "emoji_package_id": _int(record.get("emoji_package_id")) or 0,
            "key": _text(record.get("key")),
            "summary": _text(record.get("summary")) or DEFAULT_SUMMARY,
            "first_seen": now,
            "last_seen": now,
            "seen": 1,
            "_has_data": bool(record.get("_has_data")),
        }
        if self._blob_path(item).is_file():
            item["_has_data"] = True
        self.items.append(item)
        self._evict_excess()
        return item, True

    def _evict_excess(self) -> list[dict[str, Any]]:
        """超额时按「权重最低」淘汰，与挑选共用同一套价值标准。

        价值 = `item_weight`（收集频数 × 时间衰减）。效果：
          * 长期没人发的高频旧表情会因时间衰减而权重最低，第一个出局；
          * 新收集的表情 `last_seen` 最新，权重乘数最高，不会被刚收进来就挤掉
            （只有它确实比池内所有条目都低价值时才会被淘汰）。

        打平顺序（保证结果稳定可复现）：
          1. 权重更低的先出局；
          2. 权重相同时 `last_seen` 更旧的先出局；
          3. 再相同则按池内位置，越靠前（越久未被重新收集）越先出局。
        """
        excess = self._excess_count()
        if excess <= 0:
            return []

        now = time.time()

        def _value(item: Mapping[str, Any]) -> float:
            return item_weight(
                item,
                self.weight_power,
                self.decay_half_life_days,
                now,
            )

        ranked = sorted(
            enumerate(self.items),
            key=lambda pair: (_value(pair[1]), _last_seen_value(pair[1]), pair[0]),
        )
        removed = [item for _index, item in ranked[:excess]]
        removed_ids = {id(item) for item in removed}
        self.items = [item for item in self.items if id(item) not in removed_ids]
        for item in removed:
            self._delete_blob(item)
        return removed

    def _excess_count(self) -> int:
        return max(0, len(self.items) - self.pool_max)

    def sendable(self) -> list[dict[str, Any]]:
        return [item for item in self.items if is_sendable(item)]

    def _choose_weighted(self, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """在候选集内按「收集频数 × 时间衰减」加权随机挑选。

        权重 = `(seen + 1) ** weight_power × 0.5 ** (距今未出现天数 / 半衰期)`，
        所以既偏向群里发得多的，也会让长期没人发的旧热门自然退位。
        权重异常时兜底回退到等概率。
        """
        now = time.time()  # 同一批候选共用同一参考时刻
        weights = [item_weight(item, self.weight_power, self.decay_half_life_days, now) for item in candidates]
        total = sum(weights)
        if not math.isfinite(total) or total <= 0 or not all(math.isfinite(weight) for weight in weights):
            return random.choice(candidates)
        return random.choices(candidates, weights=weights, k=1)[0]

    def pick(self, exclude: Iterable[str] = ()) -> dict[str, Any] | None:
        """挑一条可发送的表情：先避开刚发过的，再按收集频数加权随机。

        排除优先级：本条消息自带的表情 + 最近发送记录；
        若全被排除则退化为「只排除本条自带」，再空则用全池。
        每一级内部都按 `(seen + 1) ** weight_power` 加权。
        """
        pool = self.sendable()
        if not pool:
            return None

        banned = {_text(token) for token in exclude if _text(token)}
        recent = set(self.recent_sent)

        def _key(item: Mapping[str, Any]) -> str:
            return unify_id(item)

        fresh = [item for item in pool if _key(item) not in recent and _key(item) not in banned]
        if fresh:
            return self._choose_weighted(fresh)

        without_self = [item for item in pool if _key(item) not in banned]
        if without_self:
            return self._choose_weighted(without_self)

        return self._choose_weighted(pool)

    def mark_sent(self, record: Mapping[str, Any]) -> None:
        """记录刚发送过的表情，供后续挑选去重（只保留最近 N 条）。"""
        if self.recent_exclude <= 0:
            return
        key = unify_id(record)
        if not key or key == "0:":
            return
        if key in self.recent_sent:
            self.recent_sent.remove(key)
        self.recent_sent.append(key)


__all__ = [
    "BLOB_DIR_NAME",
    "BLOB_SUFFIX",
    "DEFAULT_DECAY_HALF_LIFE_DAYS",
    "DEFAULT_MAX_BLOB_BYTES",
    "DEFAULT_SUMMARY",
    "DEFAULT_WEIGHT_POWER",
    "KIND_MFACE",
    "KIND_STICKER",
    "MFACE_REQUIRED_KEYS",
    "MFacePool",
    "SCHEMA_VERSION",
    "STICKER_SUB_TYPE",
    "clamp_decay_half_life",
    "clamp_weight_power",
    "is_sendable",
    "item_weight",
    "mface_from_segment",
    "mface_id",
    "record_from_segment",
    "record_kind",
    "sticker_from_segment",
    "to_send_segment",
    "with_send_payload",
    "unify_id",
]
