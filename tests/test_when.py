"""事件前置条件：收集 / 回复抽签 / 群黑白名单 / 机器人过滤（移植并适配开源语义）。"""

from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any

from nonebot_plugin_mface_pool import when


def test_collect_candidate(make_cfg: Any, group_event: Any, mface_segment: Any, seg: Any) -> None:
    cfg = make_cfg()
    mface_event = group_event(message=[mface_segment])
    plain_event = group_event(message=[seg("text", text="今天打了什么")])
    self_event = group_event(user_id=group_event().self_id, message=[mface_segment])

    assert when.collect_candidate(mface_event, cfg) is True
    assert when.collect_candidate(plain_event, cfg) is False
    assert when.collect_candidate(self_event, cfg) is False


def test_empty_groups_disables_plugin(make_cfg: Any, group_event: Any, mface_segment: Any) -> None:
    """MFACE_GROUPS 未配置（空列表）→ 收集与回复都不生效，而不是默认全群。"""
    cfg = make_cfg(groups=frozenset(), reply_rate=1.0, sticker_ratio=1.0)
    event = group_event(message=[mface_segment])
    assert when.collect_candidate(event, cfg) is False
    assert when.reply_candidate(event, cfg) is False


def test_reply_probability_zero(make_cfg: Any, group_event: Any, mface_segment: Any, seg: Any) -> None:
    cfg = make_cfg(reply_rate=0.0)
    plain_event = group_event(message=[seg("text", text="hi")])
    mface_event = group_event(message=[mface_segment])
    assert when.reply_candidate(plain_event, cfg) is False
    assert when.reply_candidate(mface_event, cfg) is False


def test_reply_probability_one(make_cfg: Any, group_event: Any, mface_segment: Any, seg: Any) -> None:
    always = make_cfg(reply_rate=1.0, sticker_ratio=1.0)
    plain_event = group_event(message=[seg("text", text="hi")])
    mface_event = group_event(message=[mface_segment])
    assert when.reply_candidate(mface_event, always) is True
    assert when.reply_candidate(plain_event, always) is True
    assert when.collect_candidate(mface_event, always) is True


def test_sticker_ratio_zero(make_cfg: Any, group_event: Any, mface_segment: Any, seg: Any) -> None:
    sticker_only = make_cfg(reply_rate=1.0, sticker_ratio=0.0)
    plain_event = group_event(message=[seg("text", text="hi")])
    mface_event = group_event(message=[mface_segment])
    assert when.reply_candidate(plain_event, sticker_only) is False
    assert when.reply_candidate(mface_event, sticker_only) is True


def test_reply_probability_distribution(make_cfg: Any, group_event: Any, mface_segment: Any, seg: Any) -> None:
    plain_event = group_event(message=[seg("text", text="hi")])
    mface_event = group_event(message=[mface_segment])
    probe = make_cfg(reply_rate=0.02, sticker_ratio=0.12)

    random.seed(11)
    n = 40000
    hits = sum(1 for _ in range(n) if when.reply_candidate(plain_event, probe))
    expected = 0.02 * 0.12
    assert abs(hits / n - expected) < 0.002

    random.seed(11)
    n2 = 20000
    hits2 = sum(1 for _ in range(n2) if when.reply_candidate(mface_event, probe))
    assert abs(hits2 / n2 - 0.02) < 0.005


def test_disabled_and_lists(make_cfg: Any, group_event: Any, seg: Any) -> None:
    plain_event = group_event(message=[seg("text", text="hi")])
    always = make_cfg(reply_rate=1.0, sticker_ratio=1.0)
    assert when.reply_candidate(plain_event, always) is True

    assert when.reply_candidate(plain_event, make_cfg(enabled=False, reply_rate=1.0)) is False
    # 白名单外
    assert when.reply_candidate(plain_event, make_cfg(reply_rate=1.0, groups=frozenset({10001}))) is False
    # 黑名单优先
    blacklist = make_cfg(reply_rate=1.0, groups=frozenset({10000}), group_blacklist=frozenset({10000}))
    assert when.reply_candidate(plain_event, blacklist) is False
    # 屏蔽用户
    blocked = make_cfg(reply_rate=1.0, block_users=frozenset({group_event().user_id}))
    assert when.reply_candidate(plain_event, blocked) is False


def test_private_event_excluded(make_cfg: Any, seg: Any) -> None:
    always = make_cfg(reply_rate=1.0, sticker_ratio=1.0)
    private = SimpleNamespace(user_id=10001, self_id=10002, message=[seg("text", text="hi")], group_id=10000)
    assert when.reply_candidate(private, always) is False


def test_find_collectable_records(make_cfg: Any, group_event: Any, mface_segment: Any) -> None:
    event = group_event(message=[mface_segment])
    records = when.find_collectable_records(event)
    assert len(records) == 1
    assert when.find_mface_records is when.find_collectable_records


def test_get_config_uses_env(make_cfg: Any) -> None:
    # conftest 已把 MFACE_ENABLED=false / MFACE_GROUPS 空 写进环境；默认配置必须关闭
    when._config = None
    cfg = when.get_config()
    assert cfg.enabled is False
    assert cfg.allow_group(10000) is False
    when._config = None
