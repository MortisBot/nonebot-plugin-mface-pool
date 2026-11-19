"""配置默认值 / 边界单测（移植自原离线自检脚本，按开源版安全默认值调整）。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from nonebot_plugin_mface_pool import config


def test_defaults_are_safe() -> None:
    cfg = config.load_config()
    assert cfg.enabled is False  # 开源版默认关闭
    assert cfg.groups == frozenset()  # 未配置群 = 插件不生效
    assert cfg.group_blacklist == frozenset()
    assert cfg.block_users == frozenset()
    assert cfg.reply_rate == pytest.approx(0.02)
    assert cfg.pool_max == 100
    assert cfg.recent_exclude == 10
    assert cfg.sticker_ratio == pytest.approx(0.12)
    assert cfg.weight_power == pytest.approx(config.DEFAULT_WEIGHT_POWER)
    assert cfg.decay_half_life_days == pytest.approx(config.DEFAULT_DECAY_HALF_LIFE_DAYS)
    assert cfg.detect_robots is True
    assert cfg.robot_cache_ttl == pytest.approx(3600.0)
    # 落盘路径是相对路径（不写死部署机绝对路径）
    assert str(cfg.store_path) == "data/mface_pool/pool.json"
    assert not str(cfg.store_path).startswith("/")


def test_allow_group_matrix() -> None:
    cfg = config.load_config()
    # 白名单为空 → 任何群都不允许
    assert cfg.allow_group(10000) is False
    # 显式配置白名单后只为列出的群生效
    cfg = replace(cfg, groups=frozenset({10000, 10001}), group_blacklist=frozenset({10001}))
    assert cfg.allow_group(10000) is True
    assert cfg.allow_group(10001) is False  # 黑名单优先
    assert cfg.allow_group(10002) is False


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MFACE_ENABLED", "true")
    monkeypatch.setenv("MFACE_REPLY_RATE", "0.5")
    monkeypatch.setenv("MFACE_GROUPS", "10000, 10001  10002;00")
    monkeypatch.setenv("MFACE_GROUPS_BLACKLIST", "10003")
    monkeypatch.setenv("MFACE_BLOCK_USERS", "10004")
    monkeypatch.setenv("MFACE_POOL_MAX", "7")
    monkeypatch.setenv("MFACE_RECENT_EXCLUDE", "2")
    monkeypatch.setenv("MFACE_WEIGHT_POWER", "1.7")
    monkeypatch.setenv("MFACE_DECAY_HALF_LIFE", "3.5")
    monkeypatch.setenv("MFACE_STICKER_RATIO", "0.3")
    monkeypatch.setenv("MFACE_STORE_PATH", "/tmp/mface-pool-test/pool.json")
    monkeypatch.setenv("MFACE_DETECT_ROBOTS", "false")
    monkeypatch.setenv("MFACE_ROBOT_CACHE_TTL", "60")
    cfg = config.load_config()
    assert cfg.enabled is True
    assert cfg.reply_rate == pytest.approx(0.5)
    assert cfg.groups == frozenset({10000, 10001, 10002})
    assert cfg.group_blacklist == frozenset({10003})
    assert cfg.block_users == frozenset({10004})
    assert cfg.pool_max == 7
    assert cfg.recent_exclude == 2
    assert cfg.weight_power == pytest.approx(1.7)
    assert cfg.decay_half_life_days == pytest.approx(3.5)
    assert cfg.sticker_ratio == pytest.approx(0.3)
    assert str(cfg.store_path) == "/tmp/mface-pool-test/pool.json"
    assert cfg.detect_robots is False
    assert cfg.robot_cache_ttl == pytest.approx(60.0)


def test_clamping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MFACE_REPLY_RATE", "5")
    monkeypatch.setenv("MFACE_POOL_MAX", "0")
    monkeypatch.setenv("MFACE_RECENT_EXCLUDE", "-3")
    monkeypatch.setenv("MFACE_WEIGHT_POWER", "99")
    monkeypatch.setenv("MFACE_STICKER_RATIO", "5")
    monkeypatch.setenv("MFACE_DECAY_HALF_LIFE", "-7")
    monkeypatch.setenv("MFACE_ROBOT_CACHE_TTL", "0")
    cfg = config.load_config()
    assert cfg.reply_rate == 1.0
    assert cfg.pool_max == 1
    assert cfg.recent_exclude == 0
    assert cfg.weight_power == pytest.approx(config.MAX_WEIGHT_POWER)
    assert cfg.sticker_ratio == 1.0
    assert cfg.decay_half_life_days == 0.0
    assert cfg.robot_cache_ttl == 1.0


def test_invalid_weight_power_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MFACE_WEIGHT_POWER", "abc")
    monkeypatch.setenv("MFACE_DECAY_HALF_LIFE", "abc")
    cfg = config.load_config()
    assert cfg.weight_power == pytest.approx(config.DEFAULT_WEIGHT_POWER)
    assert cfg.decay_half_life_days == pytest.approx(config.DEFAULT_DECAY_HALF_LIFE_DAYS)


def test_parse_helpers() -> None:
    assert config.parse_bool("yes", False) is True
    assert config.parse_bool("off", True) is False
    assert config.parse_bool(None, True) is True
    assert config.parse_int("42", 0) == 42
    assert config.parse_int("x", 7) == 7
    assert config.parse_float("1.5", 0.0) == pytest.approx(1.5)
    assert config.parse_float("x", 2.0) == pytest.approx(2.0)
    assert config.parse_id_set("10000 10001") == frozenset({10000, 10001})
    assert config.parse_id_set("") == frozenset()
