"""随机挑选 / 最近不重复 / 频数加权 / 时间衰减（移植自原离线自检脚本）。"""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from nonebot_plugin_mface_pool import store

DAY = 86400.0


def mface(seg: Any, emoji_id: str, package_id: int, key: str = "k") -> dict[str, Any]:
    record = store.mface_from_segment(seg("image", emoji_id=emoji_id, emoji_package_id=package_id, key=key))
    assert record is not None
    return record


def test_pick_sendable_only_and_recent_exclude(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "pick" / "pool.json", 10, 2)
    ids = []
    for index in range(4):
        record = mface(seg, f"bbbb{index}", 2000 + index, key=f"k{index}")
        pool.add(record)
        ids.append(record["emoji_id"])
    unsendable = store.mface_from_segment(
        seg("image", emoji_id="cccc0", emoji_package_id=3000)  # 缺 key → 不可发送
    )
    assert unsendable is not None
    pool.add(unsendable)
    assert len(pool.sendable()) == 4

    picked = {pool.pick()["emoji_id"] for _ in range(60)}
    assert picked <= set(ids)

    pool.mark_sent({"emoji_id": ids[0], "emoji_package_id": 2000})
    pool.mark_sent({"emoji_id": ids[1], "emoji_package_id": 2001})
    picked = {pool.pick()["emoji_id"] for _ in range(60)}
    assert picked == {ids[2], ids[3]}

    # 全部标记为最近发过 → 退化为全池，而不是不发
    for index, emoji_id in enumerate(ids):
        pool.mark_sent({"emoji_id": emoji_id, "emoji_package_id": 2000 + index})
    assert pool.pick() is not None


def test_pick_exclude_parameter(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "pick2" / "pool.json", 10, 0)
    for index in range(3):
        pool.add(mface(seg, f"dddd{index}", 2100 + index))
    banned = ["2100:dddd0", "2101:dddd1"]
    picks = {pool.pick(exclude=banned)["emoji_id"] for _ in range(50)}
    assert picks == {"dddd2"}
    # 全被 exclude → 退回到「只排除本条自带」→ 仍能返回
    all_banned = ["2100:dddd0", "2101:dddd1", "2102:dddd2"]
    assert pool.pick(exclude=all_banned) is not None


def test_item_weight_formula() -> None:
    assert abs(store.item_weight({"seen": 1}, 1.2, 0) - 2.0**1.2) < 1e-9
    assert abs(store.item_weight({"seen": 100}, 1.2, 0) - 101.0**1.2) < 1e-9
    assert (
        store.item_weight({}, 1.2, 0)
        == store.item_weight({"seen": 0}, 1.2, 0)
        == store.item_weight({"seen": -5}, 1.2, 0)
        == store.item_weight({"seen": 1}, 1.2, 0)
    )
    assert store.item_weight({"seen": 10**12}, 1.2, 0) == store.item_weight({"seen": store.MAX_SEEN}, 1.2, 0)
    assert store.clamp_weight_power(0) == store.MIN_WEIGHT_POWER
    assert store.clamp_weight_power(100) == store.MAX_WEIGHT_POWER
    assert store.clamp_weight_power("abc") == store.DEFAULT_WEIGHT_POWER


def test_weighted_pick_distribution(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "weight" / "pool.json", 10, 0, weight_power=1.2)

    def add_seen(emoji_id: str, package_id: int, seen: int) -> None:
        item, _new = pool.add(mface(seg, emoji_id, package_id))
        item["seen"] = seen

    add_seen("cold", 3000, 1)
    add_seen("hot", 3001, 100)

    random.seed(0)
    picks = [pool.pick()["emoji_id"] for _ in range(10000)]
    hot_ratio = picks.count("hot") / len(picks)
    weight_cold = store.item_weight({"seen": 1}, 1.2, 0)
    weight_hot = store.item_weight({"seen": 100}, 1.2, 0)
    expected_hot = weight_hot / (weight_hot + weight_cold)
    assert hot_ratio > 0.90
    assert abs(hot_ratio - expected_hot) < 0.03
    assert picks.count("cold") > 0


def test_equal_weight_equal_probability(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "weight2" / "pool.json", 10, 0)
    for index in range(2):
        pool.add(mface(seg, f"same{index}", 4000 + index, key=f"k{index}"))
    random.seed(1)
    picks = [pool.pick()["emoji_id"] for _ in range(4000)]
    ratio = picks.count("same0") / len(picks)
    assert 0.4 < ratio < 0.6


def test_item_weight_invalid_seen_no_crash() -> None:
    weird = store.item_weight({"seen": "not-a-number"}, 1.2, 0)
    assert abs(weird - (1 + 1) ** 1.2) < 1e-9


def test_decay_formula() -> None:
    now = 1_800_000_000.0
    half = 14.0

    def weight(seen: int, age_days: float, half_life: float = half) -> float:
        item = {"seen": seen, "last_seen": now - age_days * DAY}
        return store.item_weight(item, 1.2, half_life, now)

    base = weight(1, 0.0)
    assert abs(weight(1, 14.0) / base - 0.5) < 1e-9
    assert abs(weight(1, 28.0) / base - 0.25) < 1e-9
    assert abs(weight(1, 56.0) / base - 0.0625) < 1e-9

    # 半衰期 0 → 关闭衰减
    assert weight(100, 0.0, 0) == weight(100, 3650.0, 0) == store.item_weight({"seen": 100}, 1.2, 0, now)
    # 未来时间戳不放大
    future = store.item_weight({"seen": 1, "last_seen": now + 10 * DAY}, 1.2, half, now)
    assert abs(future - base) < 1e-9
    # 缺失/非法 last_seen 按刚出现处理
    assert abs(store.item_weight({"seen": 1}, 1.2, half, now) - base) < 1e-9
    assert abs(store.item_weight({"seen": 1, "last_seen": "not-a-time"}, 1.2, half, now) - base) < 1e-9


def test_decay_clamp() -> None:
    assert store.clamp_decay_half_life("abc") == store.DEFAULT_DECAY_HALF_LIFE_DAYS
    assert store.clamp_decay_half_life(float("inf")) == store.DEFAULT_DECAY_HALF_LIFE_DAYS
    assert store.clamp_decay_half_life(-5) == 0.0
    assert store.clamp_decay_half_life(7.5) == 7.5


def test_stale_hot_loses_to_fresh_cold(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "decay" / "pool.json", 10, 0, 1.2, 14.0)
    stale, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="stale", emoji_package_id=5000, key="k")))
    fresh, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="fresh", emoji_package_id=5001, key="k")))
    stale["seen"] = 100  # 曾经很热
    stale["last_seen"] = time.time() - 180 * DAY  # 半年没人发
    fresh["seen"] = 1
    fresh["last_seen"] = time.time()

    ratio = store.item_weight(stale, 1.2, 14.0) / store.item_weight(fresh, 1.2, 14.0)
    assert ratio < 1.0

    random.seed(3)
    picks = [pool.pick()["emoji_id"] for _ in range(2000)]
    fresh_ratio = picks.count("fresh") / len(picks)
    assert fresh_ratio > 0.95


def test_same_seen_recent_wins(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "decay2" / "pool.json", 10, 0, 1.2, 14.0)
    old, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="old", emoji_package_id=5100, key="k")))
    new, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="new", emoji_package_id=5101, key="k")))
    old["last_seen"] = time.time() - 56 * DAY
    new["last_seen"] = time.time()
    expected = 0.0625 / (1 + 0.0625)
    random.seed(4)
    picks = [pool.pick()["emoji_id"] for _ in range(4000)]
    new_ratio = picks.count("new") / len(picks)
    assert abs(new_ratio - (1 - expected)) < 0.03
