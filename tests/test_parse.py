"""消息段解析单测（移植自原离线自检脚本）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot_plugin_mface_pool import store


def test_mface_segment_parsing(mface_segment: Any) -> None:
    record = store.mface_from_segment(mface_segment)
    assert record is not None
    assert record["emoji_id"] == "1f600"
    assert record["emoji_package_id"] == 12345
    assert record["key"] == "abc123"
    assert record["summary"] == "[动画表情]"


def test_non_mface_segments_excluded(seg: Any) -> None:
    plain_image = seg(
        "image",
        summary="[图片]",
        file="x.jpg",
        url="https://example.invalid/x.jpg",
        sub_type=0,
    )
    assert store.mface_from_segment(plain_image) is None
    assert store.mface_from_segment(seg("face", id="297")) is None
    assert store.mface_from_segment(seg("text", text="hi")) is None
    assert store.mface_from_segment(None) is None


def test_native_mface_segment(seg: Any) -> None:
    native = seg("mface", emoji_id="1f601", emoji_package_id=999, key="k", summary="s")
    assert store.mface_from_segment(native) is not None
    bad = seg("mface", emoji_id="", emoji_package_id=999)
    assert store.mface_from_segment(bad) is None


def test_missing_key_collectable_not_sendable(seg: Any) -> None:
    parsed = store.mface_from_segment(seg("image", emoji_id="1f602", emoji_package_id=111))
    assert parsed is not None
    assert not store.is_sendable(parsed)
    assert store.to_send_segment(parsed)[0] == store.KIND_MFACE


def test_bad_package_rejected(seg: Any) -> None:
    bad = seg("image", emoji_id="1f603", emoji_package_id="not-a-number")
    assert store.mface_from_segment(bad) is None
    negative = seg("image", emoji_id="1f603", emoji_package_id=-1)
    assert store.mface_from_segment(negative) is None


def test_numeric_fields_normalized(seg: Any) -> None:
    parsed = store.mface_from_segment(seg("image", emoji_id=12345, emoji_package_id="777", key="k"))
    assert parsed is not None
    assert parsed["emoji_id"] == "12345"
    assert parsed["emoji_package_id"] == 777
    assert parsed["summary"] == store.DEFAULT_SUMMARY


def test_sticker_segment(sticker_segment: Any, seg: Any) -> None:
    sticker = store.record_from_segment(sticker_segment)
    assert sticker is not None
    assert sticker["kind"] == store.KIND_STICKER
    assert sticker["file"] == "FBE714E9296477FC12603D3CCAB85246.png"
    assert sticker["summary"] == "[动画表情]"
    assert not store.is_sendable(sticker)  # 未存字节时不可发送

    # 普通图片仍被排除
    plain = seg("image", summary="", file="x.jpg", sub_type=0)
    assert store.record_from_segment(plain) is None


def test_sticker_sendable_after_blob(tmp_path: Path, sticker_segment: Any) -> None:
    sticker = store.record_from_segment(sticker_segment)
    assert sticker is not None
    pool = store.MFacePool(tmp_path / "p.json", 5, 0, 1.2, 14.0)
    assert pool.save_blob(sticker, b"GIF89a fake image") is True
    assert store.is_sendable(sticker) is True
    seg_type, seg_data = store.with_send_payload(pool, sticker)
    assert seg_type == "image"
    assert seg_data["sub_type"] == 1
    assert str(seg_data["file"]).startswith("base64://")


def test_mface_takes_priority_over_sticker(mface_segment: Any) -> None:
    both = store.record_from_segment(mface_segment)
    assert both is not None
    assert both["kind"] == store.KIND_MFACE
    seg_type, seg_data = store.to_send_segment(both)
    assert seg_type == "mface"
    assert "key" in seg_data and "emoji_id" in seg_data


def test_unify_id_rules(seg: Any) -> None:
    mface = store.mface_from_segment(seg("image", emoji_id="1f600", emoji_package_id=12345, key="k"))
    assert mface is not None
    same = store.mface_from_segment(seg("image", emoji_id="1f600", emoji_package_id=12345, key="other"))
    assert same is not None
    assert store.unify_id(mface) == store.unify_id(same)
    assert store.unify_id(mface) == "12345:1f600"

    sticker = store.record_from_segment(seg("image", file="FBE714E9296477FC12603D3CCAB85246.png", sub_type=1))
    assert sticker is not None
    prefixed = store.record_from_segment(seg("image", file="1f-FBE714E9296477FC12603D3CCAB85246.png", sub_type=1))
    assert prefixed is not None
    assert store.unify_id(sticker) == store.unify_id(prefixed) == "FBE714E9296477FC12603D3CCAB85246.png"


def test_record_kind(seg: Any) -> None:
    assert store.record_kind({"kind": store.KIND_MFACE}) == store.KIND_MFACE
    assert store.record_kind({"kind": store.KIND_STICKER}) == store.KIND_STICKER
    assert store.record_kind({"key": "k", "file": "x"}) == store.KIND_MFACE
    assert store.record_kind({"file": "x.png"}) == store.KIND_STICKER
    assert store.record_kind({}) == store.KIND_MFACE
