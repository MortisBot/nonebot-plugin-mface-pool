"""NoneBot2 插件：QQ 表情（商城表情 mface / 收藏表情）自动收集 + 概率随机发送。

开源版默认关闭，且 ``MFACE_GROUPS`` 为空时插件完全不生效（不会默认作用于所有群）。

行为：
  1. 群友发出表情时自动收集，池上限默认 100 条，JSON 持久化：
       - 商城表情：存 emoji_id / emoji_package_id / key / summary，
         按 `emoji_package_id:emoji_id` 去重，发送用 `mface` 段；
       - 收藏表情：存 file(内容 MD5) / url / summary，按 file 去重，
         发送用 `image` + `sub_type=1`（已实测 QQ 渲染为正常收藏表情）；
  2. 群内消息按低概率（默认 2%）从池中随机挑一个表情发送，
     优先避开最近发过的若干条；含表情的消息同样参与抽签
     （收到表情回一个表情是正常群聊行为），两个分支互不排斥；
  3. 排除机器人自己与 QQ 官方机器人（get_group_member_info 的 is_robot）。

⚠️ 协议要点（已对运行中的 NapCat 实测确认，勿改）：
  * 商城表情 接收：转成 **image 段**，额外附带
    `emoji_id` / `emoji_package_id` / `key`，**没有** `type=mface` 的来报段；
  * 商城表情 发送：`{"type": "mface", "data": {emoji_package_id, emoji_id, key, summary}}`；
  * 收藏表情 接收：image 段 + `sub_type=1` + `summary=[动画表情]`，无任何 emoji_* 字段；
  * 收藏表情 发送：`{"type": "image", "data": {file, sub_type: 1, summary}}`。

不依赖任何数据库；配置项见 README 的 ``MFACE_*`` 环境变量表。
会缓存群友发送的收藏表情图片字节到本地（``data/mface_pool/``），详见 README 隐私说明。
"""

from __future__ import annotations

import asyncio

import httpx
import os
import time
from collections import OrderedDict
from pathlib import Path

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .config import MFaceConfig, load_config
from .store import (
    KIND_STICKER,
    MFacePool,
    is_sendable,
    record_kind,
    with_send_payload,
)
from .when import collect_candidate, find_collectable_records, reply_candidate

__plugin_meta__ = PluginMetadata(
    name="mface 表情池",
    description=(
        "自动收集群友发送的 QQ 商城表情（mface）与收藏表情，并在群内按低概率随机发送；"
        "只用本地 JSON 持久化，不依赖数据库。需显式配置 MFACE_ENABLED=true 与 MFACE_GROUPS。"
    ),
    usage=(
        "无需命令，自动生效。通过 MFACE_* 环境变量配置开关、概率、表情池上限、"
        "最近不重复数量、频数加权指数/半衰期与群黑白名单；MFACE_GROUPS 为空则不生效。"
    ),
    type="application",
    homepage="https://github.com/MortisBot/nonebot-plugin-mface-pool",
    supported_adapters={"~onebot.v11"},
)

CFG: MFaceConfig = load_config()
pool = MFacePool(
    CFG.store_path,
    CFG.pool_max,
    CFG.recent_exclude,
    CFG.weight_power,
    CFG.decay_half_life_days,
)
logger.info(
    "[mface_pool] enabled={} rate={} pool_max={} recent_exclude={} "
    "weight_power={} decay_half_life={} sticker_ratio={} groups={} blacklist={} load={}",
    CFG.enabled,
    CFG.reply_rate,
    CFG.pool_max,
    CFG.recent_exclude,
    CFG.weight_power,
    CFG.decay_half_life_days or "off",
    CFG.sticker_ratio,
    sorted(CFG.groups) or "all",
    sorted(CFG.group_blacklist) or "none",
    len(pool.items),
)


class _RobotCache:
    """群成员机器人标记缓存，避免每条消息都查一次群成员信息。"""

    def __init__(self, ttl: float, max_size: int = 4096) -> None:
        self.ttl = float(ttl)
        self.max_size = max(1, int(max_size))
        self._data: OrderedDict[str, tuple[float, bool]] = OrderedDict()

    def get(self, key: str) -> bool | None:
        hit = self._data.get(key)
        if hit is None:
            return None
        if time.monotonic() - hit[0] > self.ttl:
            self._data.pop(key, None)
            return None
        return hit[1]

    def put(self, key: str, value: bool) -> None:
        self._data[key] = (time.monotonic(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_size:
            self._data.popitem(last=False)


_robot_cache = _RobotCache(CFG.robot_cache_ttl)


async def _sender_is_robot(bot: Bot, event: GroupMessageEvent) -> bool:
    """群成员的 is_robot（QQ 官方机器人标记）。

    查询失败时按「不是机器人」处理，绝不影响正常收集与回复；
    日志只记 QQ 号，不打昵称等敏感信息。
    """
    if not CFG.detect_robots:
        return False
    if event.user_id in CFG.block_users:
        return True

    key = f"{event.group_id}:{event.user_id}"
    cached = _robot_cache.get(key)
    if cached is not None:
        return cached

    try:
        info = await bot.call_api(
            "get_group_member_info",
            group_id=event.group_id,
            user_id=event.user_id,
            no_cache=False,
        )
    except Exception as exc:
        logger.debug(
            "[mface_pool] get_group_member_info failed user={}: {}",
            event.user_id,
            exc,
        )
        return False

    is_robot = bool(info.get("is_robot")) if isinstance(info, dict) else False
    _robot_cache.put(key, is_robot)
    return is_robot


# 收集时下载图片字节的超时与并发上限
#   * 单条超时 20s：URL 一旦失效会很快 400，正常下载 1~2s
#   * 并发上限 4：一条消息含多个表情时并行下载，但避免打满线程池
_STICKER_FETCH_TIMEOUT = 20.0
_STICKER_FETCH_CONCURRENCY = 4
_fetch_sem = asyncio.Semaphore(_STICKER_FETCH_CONCURRENCY)


async def _download_bytes(url: str) -> bytes | None:
    """异步下载图片字节（httpx，保留 TLS 验签，不阻塞事件循环）。"""
    try:
        # 保留完整 TLS 验签：表情图片 URL 来自 QQ 官方 CDN，证书合法；
        # 关闭校验会引入中间人风险（旧内网部署曾临时关过，开源版必须恢复）。
        async with httpx.AsyncClient(
            timeout=_STICKER_FETCH_TIMEOUT,
            follow_redirects=True,
            verify=True,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            return resp.content
    except Exception as exc:
        logger.debug("[mface_pool] 下载表情字节失败: {}: {}", exc.__class__.__name__, exc)
        return None


async def _persist_sticker_bytes(record: dict) -> bool:
    """收藏表情：在消息到达时立刻把图片字节存下来。

    url 带 rkey、几小时后就 400；裸 MD5 又只有 NapCat 缓存里还在时才有效。
    所以必须在这一刻（url 必然有效）把真实数据落盘，发送时用 base64 投喂。
    """
    if record_kind(record) != KIND_STICKER:
        return False
    url = record.get("url") or ""
    if not url:
        return False
    async with _fetch_sem:
        data = await _download_bytes(url)
    if not data:
        return False
    return pool.save_blob(record, data)


# 一次性清理标记：脚本/运维想「立即踢掉池内废数据」时创建该文件，
# 插件启动时会真删掉这些条目并落盘（避免运行中改盘被内存状态覆盖）。
# 路径可被 MFACE_PRUNE_MARKER 覆盖（容器/多实例时建议每个实例一个路径）。
PRUNE_MARKER = Path(os.environ.get("MFACE_PRUNE_MARKER", "/tmp/mface_pool_prune.flag"))


def _apply_pending_prune() -> None:
    if not PRUNE_MARKER.is_file():
        return
    try:
        PRUNE_MARKER.unlink(missing_ok=True)
    except OSError:
        pass
    before = len(pool.items)
    pool.items = [item for item in pool.items if is_sendable(item)]
    if len(pool.items) != before:
        pool._gc_blobs()
        logger.info(
            "[mface_pool] 启动清理：移除废数据 {} 条，池内 {} 条",
            before - len(pool.items),
            len(pool.items),
        )


# ── 收集分支 ────────────────────────────────────────────────────

collect_matcher = on_message(
    rule=Rule(collect_candidate),
    priority=97,
    block=False,
)


@collect_matcher.handle()
async def handle_collect(bot: Bot, event: GroupMessageEvent) -> None:
    try:
        if await _sender_is_robot(bot, event):
            logger.debug(
                "[mface_pool] skip collect from robot user={} group={}",
                event.user_id,
                event.group_id,
            )
            return

        records = find_collectable_records(event)
        # 同一消息内的多个表情并行下载，避免逐个串行等待
        fetched = (
            await asyncio.gather(*(_persist_sticker_bytes(record) for record in records))
            if len(records) > 1
            else [await _persist_sticker_bytes(record) for record in records]
        )
        added = 0
        for record, _has_bytes in zip(records, fetched, strict=False):
            _item, is_new = pool.add(record)
            if is_new:
                added += 1
                logger.info(
                    "[mface_pool] collected group={} user={} kind={} file={} "
                    "emoji_package_id={} emoji_id={} summary={} pool={}",
                    event.group_id,
                    event.user_id,
                    record_kind(record),
                    record.get("file") or "-",
                    record.get("emoji_package_id") or "-",
                    record.get("emoji_id") or "-",
                    record["summary"],
                    len(pool.items),
                )
        if added:
            await pool.save()
    except Exception:  # pragma: no cover - 绝不让异常影响其它插件
        logger.exception("[mface_pool] unexpected error in collect handler")


# ── 随机回复分支 ────────────────────────────────────────────────

reply_matcher = on_message(
    rule=Rule(reply_candidate),
    priority=98,
    block=False,
)


@reply_matcher.handle()
async def handle_reply(bot: Bot, event: GroupMessageEvent) -> None:
    record = None
    try:
        record = pool.pick()
        if record is None:
            logger.debug(
                "[mface_pool] pool has no sendable mface, skip group={}",
                event.group_id,
            )
            return

        if await _sender_is_robot(bot, event):
            logger.debug(
                "[mface_pool] skip random reply for robot user={} group={}",
                event.user_id,
                event.group_id,
            )
            return

        segment_type, segment_data = with_send_payload(pool, record)
        await bot.call_api(
            "send_group_msg",
            group_id=event.group_id,
            message=[MessageSegment(segment_type, segment_data)],
        )
        pool.mark_sent(record)
        await pool.save()
        logger.info(
            "[mface_pool] sent group={} kind={} segment={} file={} emoji_package_id={} emoji_id={} seen={} summary={}",
            event.group_id,
            record_kind(record),
            segment_type,
            record.get("file") or "-",
            record.get("emoji_package_id") or "-",
            record.get("emoji_id") or "-",
            record.get("seen") or 1,
            record["summary"],
        )
    except Exception as exc:
        logger.error(
            "[mface_pool] send failed group={}: {}: {}",
            getattr(event, "group_id", None),
            exc.__class__.__name__,
            exc,
        )


# 退出时落盘一次，避免丢最后几次改动
try:

    @get_driver().on_shutdown
    async def _persist_on_shutdown() -> None:
        await pool.save()

except Exception:  # pragma: no cover - 自检场景可能没有 driver
    pass

_apply_pending_prune()
