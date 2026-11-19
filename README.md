<div align="center">

# nonebot-plugin-mface-pool

_✨ 自动收集群友的 QQ 商城/收藏表情，并按低概率随机回发 ✨_

[![LICENSE](https://img.shields.io/github/license/MortisBot/nonebot-plugin-mface-pool.svg)](./LICENSE)
[![pypi](https://img.shields.io/pypi/v/nonebot-plugin-mface-pool.svg)](https://pypi.python.org/pypi/nonebot-plugin-mface-pool)
![python](https://img.shields.io/badge/python-3.10+-blue.svg)

</div>

## 📖 介绍

收到群友发送的 mface（QQ 商城表情）或收藏表情时自动入池，之后按「出现频数 × 时间衰减」加权、避开最近发过的，在群里低概率随机回发。只用本地文件持久化，不依赖数据库。

## 💿 安装

<details open>
<summary>使用 nb-cli 安装</summary>

在 NoneBot2 项目根目录下执行：

```bash
nb plugin install nonebot-plugin-mface-pool --upgrade
```

国内网络可换镜像源：

```bash
nb plugin install nonebot-plugin-mface-pool --upgrade -i "https://pypi.tuna.tsinghua.edu.cn/simple"
```

</details>

<details>
<summary>使用包管理器安装</summary>

在 NoneBot2 项目根目录下执行其一：

```bash
uv add nonebot-plugin-mface-pool
# 或
pdm add nonebot-plugin-mface-pool
# 或
poetry add nonebot-plugin-mface-pool
```

直接装仓库主分支（PyPI 包发布前可用）：

```bash
uv add git+https://github.com/MortisBot/nonebot-plugin-mface-pool@main
```

然后在 `pyproject.toml` 的 `[tool.nonebot]` 中追加：

```toml
plugins = ["nonebot_plugin_mface_pool"]
```

</details>

## ⚙️ 配置

在 NoneBot2 项目的 `.env` 中配置（配置项即环境变量名，大写）：

| 配置项 | 必填 | 默认值 | 说明 |
|:---:|:---:|:---:|:---|
| `MFACE_ENABLED` | 是 | `false` | 总开关 |
| `MFACE_GROUPS` | 是 | 空 | 生效群号，逗号分隔（空 = 不生效） |
| `MFACE_GROUPS_BLACKLIST` | 否 | 空 | 排除的群 |
| `MFACE_BLOCK_USERS` | 否 | 空 | 排除的用户 |
| `MFACE_REPLY_RATE` | 否 | `0.02` | 表情消息的回发概率 |
| `MFACE_STICKER_RATIO` | 否 | `0.12` | 其它消息的额外概率系数 |
| `MFACE_POOL_MAX` | 否 | `100` | 表情池上限 |
| `MFACE_RECENT_EXCLUDE` | 否 | `10` | 最近不重复条数 |
| `MFACE_STORE_PATH` | 否 | `data/mface_pool/pool.json` | 数据文件 |

## 🎉 使用

无需命令，自动生效：

1. 配好 `MFACE_ENABLED=true` 与非空 `MFACE_GROUPS`；
2. 群里有人发表情 → 自动学习；
3. 之后按概率自动回发。

## 🔌 适配器

OneBot v11（`supported_adapters={"~onebot.v11"}`）。仅支持 NapCat 系实现（依赖 `mface` 消息段与 `image + sub_type=1`）。

## 📄 License

[MIT](./LICENSE) · 维护模式（best-effort，无 SLA）
