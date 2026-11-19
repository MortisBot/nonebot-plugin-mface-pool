"""表情池：去重 / 淘汰 / 持久化 / 坏文件容错（移植自原离线自检脚本）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nonebot_plugin_mface_pool import store


def mface_record(seg: Any, emoji_id: str, package_id: int, key: str = "k") -> dict[str, Any]:
    record = store.mface_from_segment(seg("image", emoji_id=emoji_id, emoji_package_id=package_id, key=key))
    assert record is not None
    return record


def test_add_and_dedupe(tmp_path: Path, mface_segment: Any, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "pool.json", 3, 2)
    record = store.mface_from_segment(mface_segment)
    assert record is not None
    pool.add(record)
    assert len(pool.items) == 1

    rotated = store.mface_from_segment(
        seg(
            "image",
            emoji_id="1f600",
            emoji_package_id=12345,
            key="newkey",
            summary="[动画表情]",
        )
    )
    assert rotated is not None
    item, is_new = pool.add(rotated)
    assert is_new is False
    assert len(pool.items) == 1
    assert item["key"] == "newkey"
    assert item["seen"] == 2


def test_pool_max_eviction(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "pool.json", 3, 0)
    pool.add(mface_record(seg, "1f600", 12345))
    pool.add(mface_record(seg, "1f600", 12345, key="k2"))  # 重复收集 → 权重更高
    for index in range(3):
        pool.add(mface_record(seg, f"aaaa{index}", 1000 + index, key=f"k{index}"))
    assert len(pool.items) == 3
    names = [item["emoji_id"] for item in pool.items]
    assert "1f600" in names  # 高频保留
    assert "aaaa0" not in names  # 最早且最低权重被淘汰


async def test_save_and_reload(tmp_path: Path, mface_segment: Any) -> None:
    pool = store.MFacePool(tmp_path / "pool.json", 3, 2)
    record = store.mface_from_segment(mface_segment)
    assert record is not None
    pool.add(record)
    assert await pool.save() is True
    path = tmp_path / "pool.json"
    assert path.is_file()

    reloaded = store.MFacePool(path, 3, 2)
    assert len(reloaded.items) == len(pool.items)
    assert [item["emoji_id"] for item in reloaded.items] == [item["emoji_id"] for item in pool.items]
    raw = json.loads(path.read_text("utf-8"))
    assert raw.get("version") == store.SCHEMA_VERSION
    assert "items" in raw


def test_sticker_dedupe_and_coexist(tmp_path: Path, sticker_segment: Any, mface_segment: Any, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "pool.json", 10, 2)
    sticker = store.record_from_segment(sticker_segment)
    assert sticker is not None
    assert pool.save_blob(sticker, b"GIF89a fake image")
    _, is_new = pool.add(sticker)
    assert is_new is True and len(pool.items) == 1
    _, is_new = pool.add(sticker)
    assert is_new is False and len(pool.items) == 1

    prefixed_record = store.record_from_segment(
        seg(
            "image",
            summary="[动画表情]",
            file="1f-FBE714E9296477FC12603D3CCAB85246.png",
            sub_type=1,
        )
    )
    assert prefixed_record is not None
    _, is_new = pool.add(prefixed_record)
    assert is_new is False
    assert len(pool.items) == 1

    mface = store.record_from_segment(mface_segment)
    assert mface is not None
    pool.add(mface)
    assert len(pool.items) == 2
    assert len(pool.sendable()) == 2


def test_bad_file_quarantine(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text("{ not json", "utf-8")
    pool = store.MFacePool(path, 3, 2)
    assert pool.items == []
    backups = list(tmp_path.glob("pool.json.corrupt-*"))
    assert backups, "坏文件应被隔离为备份"
    assert not path.exists()


def test_load_normalizes_legacy_list(tmp_path: Path) -> None:
    path = tmp_path / "pool.json"
    path.write_text(
        json.dumps(
            [
                {"emoji_id": "1f600", "emoji_package_id": 12345, "key": "k", "seen": 2},
                {"file": "abc.png", "summary": "x"},
                {"garbage": True},
            ]
        ),
        "utf-8",
    )
    pool = store.MFacePool(path, 10, 2)
    # 无 file/emoji 的条目被丢弃，其余保留
    assert len(pool.items) == 2
    assert pool.items[0]["seen"] == 2
    assert pool.items[0]["kind"] == store.KIND_MFACE
    assert pool.items[1]["kind"] == store.KIND_STICKER


def test_load_trims_to_pool_max(tmp_path: Path, seg: Any) -> None:
    path = tmp_path / "pool.json"
    items = [{"emoji_id": f"aaaa{index}", "emoji_package_id": 1000 + index, "key": "k"} for index in range(5)]
    path.write_text(json.dumps({"version": store.SCHEMA_VERSION, "items": items}), "utf-8")
    pool = store.MFacePool(path, 2, 0)
    assert len(pool.items) == 2
    assert [item["emoji_id"] for item in pool.items] == ["aaaa3", "aaaa4"]


def test_pool_empty_noop(tmp_path: Path) -> None:
    pool = store.MFacePool(tmp_path / "pool.json", 10, 2)
    assert pool.pick() is None
    assert pool.sendable() == []
    assert pool._evict_excess() == []
    assert asyncio.run(pool.save()) is True
