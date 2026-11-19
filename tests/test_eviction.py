"""出池规则：超额时按权重最低淘汰（与挑选同一套价值标准）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from nonebot_plugin_mface_pool import store

DAY = 86400.0


def add(seg: Any, pool: store.MFacePool, name: str, seen: int, age_days: float) -> dict[str, Any]:
    item, is_new = pool.add(
        store.mface_from_segment(seg("image", emoji_id=name, emoji_package_id=6000 + len(name), key="k"))
    )
    assert is_new is True
    item["seen"] = seen
    item["last_seen"] = time.time() - age_days * DAY
    return item


def test_evict_lowest_weight(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "evict" / "pool.json", 3, 0)
    add(seg, pool, "STALE_HOT", 500, 90)
    add(seg, pool, "OLD_LOW", 1, 120)
    add(seg, pool, "ACTIVE", 5, 0)
    assert len(pool.items) == 3

    before = {item["emoji_id"]: store.item_weight(item, 1.2, 14.0) for item in pool.items}
    pool.add(store.mface_from_segment(seg("image", emoji_id="FRESH", emoji_package_id=7000, key="k")))
    names = [item["emoji_id"] for item in pool.items]
    assert len(names) == 3
    weakest = min(before, key=lambda name: before[name])
    assert weakest not in names
    assert "FRESH" in names
    assert max(before, key=lambda name: before[name]) in names


def test_recent_but_low_weight_evicted(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "evict2" / "pool.json", 2, 0)
    x, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="X", emoji_package_id=8001, key="k")))
    y, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="Y", emoji_package_id=8002, key="k")))
    x["seen"] = 1
    x["last_seen"] = time.time() - 1 * DAY
    y["seen"] = 50
    y["last_seen"] = time.time() - 45 * DAY
    assert store.item_weight(x, 1.2, 14.0) < store.item_weight(y, 1.2, 14.0)
    pool.add(store.mface_from_segment(seg("image", emoji_id="Z", emoji_package_id=8003, key="k")))
    left = [item["emoji_id"] for item in pool.items]
    assert "X" not in left and "Y" in left


def test_duplicate_does_not_evict(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "evict3" / "pool.json", 2, 0)
    for name, package_id in (("P", 9101), ("Q", 9102)):
        pool.add(store.mface_from_segment(seg("image", emoji_id=name, emoji_package_id=package_id, key="k")))
    assert pool._evict_excess() == []
    assert len(pool.items) == 2
    _item, is_new = pool.add(store.mface_from_segment(seg("image", emoji_id="P", emoji_package_id=9101, key="k")))
    assert is_new is False
    assert len(pool.items) == 2
    assert pool.items[-1]["emoji_id"] == "P"


def test_tie_break_by_last_seen(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "evict4" / "pool.json", 2, 0)
    base_now = time.time()
    for name, package_id, age_days in (
        ("OLDER", 9201, 10.0),
        ("NEWER", 9202, 5.0),
        ("FRESHEST", 9203, 0.0),
    ):
        item, _ = pool.add(store.mface_from_segment(seg("image", emoji_id=name, emoji_package_id=package_id, key="k")))
        item["seen"] = 1
        item["last_seen"] = base_now - age_days * DAY
    left = [item["emoji_id"] for item in pool.items]
    assert "OLDER" not in left
    assert left == ["NEWER", "FRESHEST"]


def test_strict_tie_break_by_position(tmp_path: Path) -> None:
    pool = store.MFacePool(tmp_path / "evict5" / "pool.json", 2, 0)
    same_last_seen = time.time() - 3 * DAY

    def fake_item(name: str, last_seen: float) -> dict[str, Any]:
        return {
            "kind": store.KIND_STICKER,
            "file": f"{name}.png",
            "url": "",
            "emoji_id": "",
            "emoji_package_id": 0,
            "key": "",
            "summary": "[动画表情]",
            "first_seen": last_seen,
            "last_seen": last_seen,
            "seen": 1,
        }

    pool.items = [
        fake_item("TIE_FIRST", same_last_seen),
        fake_item("TIE_SECOND", same_last_seen),
        fake_item("FRESHEST", time.time()),
    ]
    fixed_now = same_last_seen + 3 * DAY
    assert store.item_weight(pool.items[0], 1.2, 14.0, fixed_now) == store.item_weight(
        pool.items[1], 1.2, 14.0, fixed_now
    )
    removed = pool._evict_excess()
    assert [item["file"] for item in removed] == ["TIE_FIRST.png"]
    assert [item["file"] for item in pool.items] == ["TIE_SECOND.png", "FRESHEST.png"]
