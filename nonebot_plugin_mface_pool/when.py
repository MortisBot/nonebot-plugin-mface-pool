"""mface / 收藏表情 插件的事件前置判断。

纯函数、不依赖 NoneBot 事件总线，便于独立自检。配置通过参数注入；
不传时按当前环境变量加载（与插件主模块一致）。
"""

from __future__ import annotations

import random
from typing import Any

from nonebot.adapters import Event

from .config import MFaceConfig, load_config
from .store import record_from_segment

_config: MFaceConfig | None = None


def _debug_log(message: str, *args: Any) -> None:
    """延迟导入 logger，避免为纯函数模块引入 NoneBot 依赖。"""
    try:
        from nonebot import logger

        logger.debug(message, *args)
    except Exception:  # pragma: no cover - 自检/无 NoneBot 环境下静默
        pass


def get_config() -> MFaceConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _cfg(config: MFaceConfig | None) -> MFaceConfig:
    return config if config is not None else get_config()


def _is_group_event(event: Event) -> bool:
    """只处理群消息：按类名判断，避免为通用逻辑绑定具体适配器子类。"""
    return type(event).__name__ == "GroupMessageEvent"


def _base_ok(event: Event, cfg: MFaceConfig) -> bool:
    if not cfg.enabled:
        return False
    if not cfg.groups:  # MFACE_GROUPS 未配置 → 插件不生效（空列表不等于全群）
        return False
    if not _is_group_event(event):
        return False
    group_id = getattr(event, "group_id", None)
    if group_id is None or not cfg.allow_group(int(group_id)):
        return False
    user_id = getattr(event, "user_id", None)
    if user_id is None:
        return False
    self_id = getattr(event, "self_id", None)
    if self_id is not None and str(user_id) == str(self_id):
        return False  # 机器人自己的消息（双保险，NapCat 当前也不会回灌）
    if int(user_id) in cfg.block_users:
        return False
    return True


def find_collectable_records(event: Event) -> list[dict[str, Any]]:
    """提取本条消息里所有可收集的表情记录（商城表情 / 收藏表情）。"""
    records = []
    for segment in getattr(event, "message", ()) or ():
        record = record_from_segment(segment)
        if record is not None:
            records.append(record)
    return records


# 兼容旧名字
find_mface_records = find_collectable_records


def collect_candidate(event: Event, config: MFaceConfig | None = None) -> bool:
    """收集分支的廉价前置条件：确实是商城表情或收藏表情才进入 handler。"""
    cfg = _cfg(config)
    if not _base_ok(event, cfg):
        return False
    return bool(find_collectable_records(event))


def reply_candidate(event: Event, config: MFaceConfig | None = None) -> bool:
    """随机回复分支：对群内所有合格消息按概率抽签。

    有效概率按「表情消息 / 全部消息」分两档：

    * 含表情的消息 → ``reply_rate``（0.02）
    * 其它消息     → ``reply_rate × sticker_ratio``（0.02 × 0.12 = 0.0024）

    因此总发送量 ≈ 全部消息 × 2% × 12%，即「以约 12% 的表情密度为基准折算」。
    含表情的消息同时仍会被收集分支收下，两个分支互不排斥。
    """
    cfg = _cfg(config)
    if not _base_ok(event, cfg):
        return False
    if cfg.reply_rate <= 0:
        return False

    is_sticker = bool(find_collectable_records(event))
    rate = cfg.reply_rate if is_sticker else cfg.reply_rate * cfg.sticker_ratio
    if rate <= 0:
        return False

    roll = random.random()
    if roll < rate:
        # debug 级：平时 LOG_LEVEL=INFO 不会输出；需要排查「为什么不发」时调低日志级别即可看到抽签过程
        _debug_log(
            "[mface_pool] reply roll HIT roll={:.5f} rate={:.5f} sticker={} group={} user={}",
            roll,
            rate,
            is_sticker,
            getattr(event, "group_id", None),
            getattr(event, "user_id", None),
        )
        return True
    return False


__all__ = [
    "collect_candidate",
    "find_collectable_records",
    "find_mface_records",
    "get_config",
    "reply_candidate",
]
