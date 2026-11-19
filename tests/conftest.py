"""pytest 公共配置 / fixture。

插件主模块会注册 matcher 并实例化表情池，所以先提供干净的假环境 + ``nonebot.init()``
再导入插件；所有测试纯离线（不启动 NoneBot 事件循环、不连接 NapCat、不访问网络）。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="mface-pool-tests-"))
os.environ["MFACE_ENABLED"] = "false"
os.environ["MFACE_GROUPS"] = ""
os.environ["MFACE_STORE_PATH"] = str(_TEST_ROOT / "pool.json")
os.environ["MFACE_PRUNE_MARKER"] = str(_TEST_ROOT / "prune.flag")

import nonebot  # noqa: E402

nonebot.init(driver="~none")

from nonebot_plugin_mface_pool import config  # noqa: E402

# 群号/QQ 号全部使用占位值
GROUP_ID = 10000
USER_ID = 10001
SELF_ID = 10002

# NapCat 收报形态：商城表情被转成 image 段，附带 emoji_id / emoji_package_id / key
MFACE_DATA: dict[str, Any] = {
    "summary": "[动画表情]",
    "file": "1f-1f600.gif",
    "url": "https://example.invalid/mface/1f600.gif",
    "key": "abc123",
    "emoji_id": "1f600",
    "emoji_package_id": 12345,
}

# 收藏表情：image 段 + sub_type=1
STICKER_DATA: dict[str, Any] = {
    "summary": "[动画表情]",
    "file": "FBE714E9296477FC12603D3CCAB85246.png",
    "url": "https://example.invalid/sticker/FBE714E9296477FC12603D3CCAB85246.png",
    "sub_type": 1,
    "file_size": 59078,
}


class GroupMessageEvent:
    """占位事件类：when.py 按类名判断群消息，故必须叫这个名字。"""

    def __init__(
        self,
        group_id: int = GROUP_ID,
        user_id: int = USER_ID,
        self_id: int = SELF_ID,
        message: tuple[Any, ...] = (),
    ) -> None:
        self.group_id = group_id
        self.user_id = user_id
        self.self_id = self_id
        self.message = list(message)


@pytest.fixture(autouse=True)
def _clean_mface_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("MFACE_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def seg() -> Any:
    def _seg(seg_type: str, **data: Any) -> SimpleNamespace:
        return SimpleNamespace(type=seg_type, data=data)

    return _seg


@pytest.fixture()
def mface_segment(seg: Any) -> SimpleNamespace:
    return seg("image", **MFACE_DATA)


@pytest.fixture()
def sticker_segment(seg: Any) -> SimpleNamespace:
    return seg("image", **STICKER_DATA)


@pytest.fixture()
def group_event() -> Any:
    def _make(**overrides: Any) -> GroupMessageEvent:
        return GroupMessageEvent(**overrides)

    return _make


@pytest.fixture()
def make_cfg(tmp_path: Path) -> Any:
    """构造假 config（默认启用、群白名单={GROUP_ID}），避免读真实环境变量。"""

    def _make(**overrides: Any) -> config.MFaceConfig:
        kwargs: dict[str, Any] = {
            "enabled": True,
            "reply_rate": 0.0,
            "pool_max": 3,
            "recent_exclude": 2,
            "groups": frozenset({GROUP_ID}),
            "group_blacklist": frozenset(),
            "block_users": frozenset(),
            "detect_robots": True,
            "robot_cache_ttl": 3600.0,
            "weight_power": 1.2,
            "decay_half_life_days": 14.0,
            "sticker_ratio": 0.12,
            "store_path": tmp_path / "pool.json",
        }
        kwargs.update(overrides)
        return config.MFaceConfig(**kwargs)

    return _make
