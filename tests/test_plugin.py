"""插件入口：metadata、机器人过滤、TLS 验签、启动清理标记。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import nonebot_plugin_mface_pool as plugin
from nonebot_plugin_mface_pool import store


def test_plugin_metadata() -> None:
    meta = plugin.__plugin_meta__
    assert meta.name
    assert meta.homepage == "https://github.com/MortisBot/nonebot-plugin-mface-pool"
    assert meta.supported_adapters == {"~onebot.v11"}
    assert "~onebot-v11" not in repr(meta)
    assert "MFACE_GROUPS" in (meta.usage or "")


def test_robot_cache() -> None:
    cache = plugin._RobotCache(ttl=3600, max_size=2)
    assert cache.get("a") is None
    cache.put("a", True)
    cache.put("b", False)
    assert cache.get("a") is True
    assert cache.get("b") is False
    cache.put("c", True)  # 超过 max_size，淘汰最旧的 a
    assert cache.get("a") is None
    assert cache.get("b") is False
    assert cache.get("c") is True


def test_robot_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [1000.0]
    monkeypatch.setattr(plugin.time, "monotonic", lambda: clock[0])
    cache = plugin._RobotCache(ttl=10, max_size=4)
    cache.put("a", True)
    clock[0] += 5
    assert cache.get("a") is True
    clock[0] += 6
    assert cache.get("a") is None


class FakeBot:
    def __init__(self, info: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.info = info or {}
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_api(self, action: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((action, kwargs))
        if self.error is not None:
            raise self.error
        return self.info


def _event(user_id: int = 10001, group_id: int = 10000) -> Any:
    return type("Event", (), {"user_id": user_id, "group_id": group_id})()


async def test_sender_is_robot_detects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "CFG", replace(plugin.CFG, detect_robots=True, block_users=frozenset()))
    monkeypatch.setattr(plugin, "_robot_cache", plugin._RobotCache(3600))
    bot = FakeBot({"is_robot": True})
    assert await plugin._sender_is_robot(bot, _event()) is True
    assert bot.calls and bot.calls[0][0] == "get_group_member_info"
    # 命中缓存，不再查询
    assert await plugin._sender_is_robot(bot, _event()) is True
    assert len(bot.calls) == 1


async def test_sender_is_robot_normal_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "CFG", replace(plugin.CFG, detect_robots=True, block_users=frozenset()))
    monkeypatch.setattr(plugin, "_robot_cache", plugin._RobotCache(3600))
    assert await plugin._sender_is_robot(FakeBot({"is_robot": False}), _event()) is False


async def test_sender_is_robot_api_failure_is_not_robot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin, "CFG", replace(plugin.CFG, detect_robots=True, block_users=frozenset()))
    monkeypatch.setattr(plugin, "_robot_cache", plugin._RobotCache(3600))
    bot = FakeBot(error=RuntimeError("api down"))
    assert await plugin._sender_is_robot(bot, _event()) is False


async def test_sender_is_robot_shortcuts(monkeypatch: pytest.MonkeyPatch) -> None:
    # detect_robots=False → 直接放行
    monkeypatch.setattr(plugin, "CFG", replace(plugin.CFG, detect_robots=False))
    assert await plugin._sender_is_robot(FakeBot({"is_robot": True}), _event()) is False
    # block_users 命中 → 视为机器人，不查 API
    monkeypatch.setattr(
        plugin,
        "CFG",
        replace(plugin.CFG, detect_robots=True, block_users=frozenset({10001})),
    )
    bot = FakeBot({"is_robot": False})
    assert await plugin._sender_is_robot(bot, _event(user_id=10001)) is True
    assert bot.calls == []


def test_download_bytes_uses_tls_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    """下载表情字节必须使用完整 TLS 验签（httpx verify=True）。"""
    captured: dict[str, Any] = {}

    class FakeResponse:
        content = b"image-bytes"

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(plugin.httpx, "AsyncClient", FakeClient)
    data = asyncio.run(plugin._download_bytes("https://example.invalid/sticker.png"))
    assert data == b"image-bytes"
    assert captured.get("verify") is True
    assert captured.get("follow_redirects") is True


def test_download_bytes_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("boom")

    monkeypatch.setattr(plugin.httpx, "AsyncClient", FakeClient)
    assert asyncio.run(plugin._download_bytes("https://example.invalid/x.png")) is None


def test_apply_pending_prune(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seg: Any) -> None:
    marker = tmp_path / "prune.flag"
    marker.write_text("1", "utf-8")
    monkeypatch.setattr(plugin, "PRUNE_MARKER", marker)
    pool = store.MFacePool(tmp_path / "pool.json", 5, 0)
    unsendable, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="1f600", emoji_package_id=1)))
    sendable, _ = pool.add(store.mface_from_segment(seg("image", emoji_id="1f601", emoji_package_id=2, key="k")))
    monkeypatch.setattr(plugin, "pool", pool)
    plugin._apply_pending_prune()
    assert not marker.exists()
    assert len(pool.items) == 1
    assert pool.items[0]["emoji_id"] == sendable["emoji_id"]
    assert unsendable["emoji_id"] != pool.items[0]["emoji_id"]
