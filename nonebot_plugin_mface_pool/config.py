"""mface 表情池插件配置。

纯标准库实现，不依赖 NoneBot，方便单独复用与自检。

所有配置均通过 ``MFACE_*`` 环境变量读取，全部有默认值：
默认**关闭**（``MFACE_ENABLED=false``）；``MFACE_GROUPS`` 为空表示「未配置 → 插件不生效」，
必须显式列出要收集/回发表情的群；落盘路径默认为相对路径 ``data/mface_pool/pool.json``
（相对于 bot 运行目录），可用环境变量覆盖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 默认落盘路径（相对 bot 运行目录；容器/部署时建议用 MFACE_STORE_PATH 指向持久化目录）
DEFAULT_STORE_PATH = "data/mface_pool/pool.json"

# 按收集频数加权的指数：权重 = (seen + 1) ** weight_power
#   1.0 = 线性；越大越偏向高频表情（冷门小众表情概率越低）
DEFAULT_WEIGHT_POWER = 1.2
MIN_WEIGHT_POWER = 0.1
MAX_WEIGHT_POWER = 5.0

# 时间衰减半衰期（天）：多久没被再次收集，权重就减半
#   用来压住「以前很热、现在没人发」的表情。0 = 关闭衰减。
DEFAULT_DECAY_HALF_LIFE_DAYS = 14.0

# 表情密度基准值：非表情消息的有效概率 = reply_rate × 该值。
#   含义是「以约 12% 的表情密度为基准，把发送量折算到全部消息的 2% × 12%」。
DEFAULT_STICKER_RATIO = 0.12

_TRUE_VALUES = {"1", "true", "yes", "y", "on", "enable", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "n", "off", "disable", "disabled"}


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    return default


def parse_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_id_set(value: object) -> frozenset[int]:
    """解析 QQ 号 / 群号集合，逗号/空格/分号分隔；空表示空集合。"""
    ids: set[int] = set()
    text = str(value or "")
    for token in text.replace(",", " ").replace(";", " ").split():
        number = parse_int(token, 0)
        if number > 0:
            ids.add(number)
    return frozenset(ids)


@dataclass(frozen=True, slots=True)
class MFaceConfig:
    """插件整体配置。"""

    enabled: bool
    reply_rate: float
    pool_max: int
    recent_exclude: int
    groups: frozenset[int]
    group_blacklist: frozenset[int]
    block_users: frozenset[int]
    detect_robots: bool
    robot_cache_ttl: float
    weight_power: float
    decay_half_life_days: float
    sticker_ratio: float
    store_path: Path

    def allow_group(self, group_id: int) -> bool:
        """群黑白名单判定。

        黑名单优先；**白名单为空 = 未配置 = 插件不生效**（不再默认全群）。
        这是开源版的有意行为：表情池会缓存群友表情，必须由部署者显式指定收集群。
        """
        if group_id in self.group_blacklist:
            return False
        if not self.groups:
            return False
        return group_id in self.groups


def load_config() -> MFaceConfig:
    pool_max = max(1, parse_int(_env("MFACE_POOL_MAX"), 100))
    recent_exclude = parse_int(_env("MFACE_RECENT_EXCLUDE"), 10)
    recent_exclude = min(max(0, recent_exclude), pool_max)

    weight_power = parse_float(_env("MFACE_WEIGHT_POWER"), DEFAULT_WEIGHT_POWER)
    weight_power = min(MAX_WEIGHT_POWER, max(MIN_WEIGHT_POWER, weight_power))

    decay_half_life = parse_float(_env("MFACE_DECAY_HALF_LIFE"), DEFAULT_DECAY_HALF_LIFE_DAYS)
    if decay_half_life < 0:  # 负数统一视为关闭衰减
        decay_half_life = 0.0

    sticker_ratio = parse_float(_env("MFACE_STICKER_RATIO"), DEFAULT_STICKER_RATIO)
    sticker_ratio = min(1.0, max(0.0, sticker_ratio))

    path_text = _env("MFACE_STORE_PATH", DEFAULT_STORE_PATH)

    return MFaceConfig(
        enabled=parse_bool(_env("MFACE_ENABLED"), False),
        reply_rate=min(1.0, max(0.0, parse_float(_env("MFACE_REPLY_RATE"), 0.02))),
        pool_max=pool_max,
        recent_exclude=recent_exclude,
        groups=parse_id_set(_env("MFACE_GROUPS")),
        group_blacklist=parse_id_set(_env("MFACE_GROUPS_BLACKLIST")),
        block_users=parse_id_set(_env("MFACE_BLOCK_USERS")),
        detect_robots=parse_bool(_env("MFACE_DETECT_ROBOTS"), True),
        robot_cache_ttl=max(1.0, parse_float(_env("MFACE_ROBOT_CACHE_TTL"), 3600.0)),
        weight_power=weight_power,
        decay_half_life_days=decay_half_life,
        sticker_ratio=sticker_ratio,
        store_path=Path(path_text or DEFAULT_STORE_PATH),
    )
