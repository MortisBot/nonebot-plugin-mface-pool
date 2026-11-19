"""收藏表情图片字节持久化（发送不再依赖 rkey / NapCat 缓存）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nonebot_plugin_mface_pool import store


def test_url_without_bytes_not_sendable() -> None:
    assert not store.is_sendable(
        {
            "kind": store.KIND_STICKER,
            "file": "ABC123.png",
            "url": "https://example.invalid/x.png",
        }
    )
    assert not store.is_sendable({"kind": store.KIND_STICKER, "file": "ABC123.png"})


async def test_blob_save_load_send(tmp_path: Path, seg: Any) -> None:
    cfg_path = tmp_path / "blob" / "pool.json"
    blob_dir = tmp_path / "blob" / "blobs"
    pool = store.MFacePool(cfg_path, 5, 0, 1.2, 14.0, blob_dir)

    seg_data = store.record_from_segment(
        seg(
            "image",
            summary="[动画表情]",
            file="ABC123.png",
            url="https://example.invalid/x.png",
            sub_type=1,
        )
    )
    assert seg_data is not None

    assert pool.save_blob(seg_data, b"\x89PNG fake image bytes") is True
    assert pool._blob_path(seg_data).is_file()

    item, _new = pool.add(seg_data)
    assert item.get("_has_data") is True

    seg_type, data = store.with_send_payload(pool, item)
    assert seg_type == "image"
    assert str(data.get("file", "")).startswith("base64://")
    assert data.get("sub_type") == 1

    # JSON 里不落内部字段（下划线开头）
    raw = json.loads(pool._dump())
    assert all(not key.startswith("_") for item in raw["items"] for key in item)

    # 重新加载后 _has_data 能从磁盘恢复
    assert await pool.save() is True
    pool2 = store.MFacePool(cfg_path, 5, 0, 1.2, 14.0, blob_dir)
    assert pool2.items and pool2.items[0].get("_has_data") is True


async def test_orphan_blob_gc(tmp_path: Path, seg: Any) -> None:
    cfg_path = tmp_path / "blob2" / "pool.json"
    blob_dir = tmp_path / "blob2" / "blobs"
    pool = store.MFacePool(cfg_path, 3, 0, 1.2, 14.0, blob_dir)
    for index in range(6):
        record = store.record_from_segment(
            seg(
                "image",
                summary="x",
                file=f"F{index}.png",
                sub_type=1,
                url=f"https://example.invalid/{index}.png",
            )
        )
        assert record is not None
        pool.save_blob(record, b"data" + bytes([index]))
        pool.add(record)
    orphan = blob_dir / "ORPHAN.png.bin"
    orphan.write_bytes(b"orphan")
    removed = pool._gc_blobs()
    assert removed >= 1
    assert not orphan.exists()
    assert all(pool._blob_path(item).is_file() for item in pool.items)


def test_oversized_blob_rejected(tmp_path: Path, seg: Any) -> None:
    pool = store.MFacePool(tmp_path / "blob3" / "pool.json", 5, 0)
    record = store.record_from_segment(seg("image", file="BIG.png", sub_type=1))
    assert record is not None
    huge = b"x" * (store.DEFAULT_MAX_BLOB_BYTES + 1)
    assert pool.save_blob(record, huge) is False
    assert pool.save_blob(record, b"") is False


def test_eviction_deletes_blob(tmp_path: Path, seg: Any) -> None:
    cfg_path = tmp_path / "blob4" / "pool.json"
    blob_dir = tmp_path / "blob4" / "blobs"
    pool = store.MFacePool(cfg_path, 1, 0, 1.2, 14.0, blob_dir)
    for index in range(2):
        record = store.record_from_segment(seg("image", file=f"E{index}.png", sub_type=1))
        assert record is not None
        pool.save_blob(record, b"data")
        pool.add(record)
        asyncio.run(pool.save())
    assert not (blob_dir / "E0.png.bin").exists()  # 被淘汰条目的 blob 删除
    assert (blob_dir / "E1.png.bin").exists()  # 池内条目保留
