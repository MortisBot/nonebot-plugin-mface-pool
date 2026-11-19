# nonebot-plugin-mface-pool

_✨ 自动收集群友的 QQ 商城/收藏表情，并按低概率在群里随机回发。 ✨_

<a href="./LICENSE"><img src="https://img.shields.io/github/license/MortisBot/nonebot-plugin-mface-pool.svg" alt="license"></a>
<a href="https://pypi.python.org/pypi/nonebot-plugin-mface-pool"><img src="https://img.shields.io/pypi/v/nonebot-plugin-mface-pool.svg" alt="pypi"></a>
<img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="python">

## 安装

在 NoneBot2 项目根目录下执行：

```bash
nb plugin install nonebot-plugin-mface-pool
```

或手动安装：

```bash
pip install nonebot-plugin-mface-pool
```

并在 `pyproject.toml` 的 `[tool.nonebot]` 中追加：

```toml
plugins = ["nonebot_plugin_mface_pool"]
```

## 配置

在 `.env` 中配置（环境变量名即配置项大写）：

| 配置项 | 必填 | 默认值 | 说明 |
|:---:|:---:|:---:|:---|
| `MFACE_ENABLED` | 是 | `false` | 总开关 |
| `MFACE_GROUPS` | 是 | 空 | 生效群号，逗号分隔（空 = 不生效） |
| `MFACE_REPLY_RATE` | 否 | `0.02` | 群消息触发回发的概率 |
| `MFACE_STICKER_RATIO` | 否 | `0.12` | 非表情消息的额外概率系数 |
| `MFACE_POOL_MAX` | 否 | `100` | 表情池上限 |
| `MFACE_RECENT_EXCLUDE` | 否 | `10` | 最近不重复条数 |
| `MFACE_STORE_PATH` | 否 | `data/mface_pool/pool.json` | 数据文件 |

## 使用

无需命令，自动生效：

- 收到群友的商城/收藏表情时自动学习入库；
- 普通消息按概率从池中随机回发表情；
- `MFACE_GROUPS` 为空时不生效。

## 适配器

OneBot v11（`supported_adapters={"~onebot.v11"}`）。

## License

[MIT](./LICENSE)

> 维护模式：原项目已停止运营，本插件按 best-effort 维护，无 SLA。
