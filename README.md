# nonebot-plugin-mface-pool

> 同系列插件与部署/架构文档：[`MortisBot/qqbot-plugins`](https://github.com/MortisBot/qqbot-plugins)（索引与文档仓）

QQ 表情（商城表情 / 收藏表情）自动收集 + 群内低概率随机回发（NoneBot2 / OneBot v11）。

- **收集**：群友发出商城表情或收藏表情时自动入库，池上限、去重、按权重淘汰。
- **回发**：群里所有合格消息按低概率抽签，从池中随机挑一条表情发出；
  优先避开最近发过的若干条，按「收集频数 × 时间衰减」加权。
- **过滤**：默认跳过机器人自己的消息、`is_robot` 的 QQ 官方机器人、黑名单用户与黑名单群。
- **持久化**：纯本地 JSON（`pool.json`）+ 收藏表情图片字节（`blobs/`），不依赖数据库。

> 开源版默认**关闭**；`MFACE_GROUPS` 为空表示「未配置 → 插件完全不生效」（不会默认全群）。

## 安装

```bash
# 方式一：作为 NoneBot 插件安装
nb plugin install nonebot-plugin-mface-pool

# 方式二：从源码安装（本仓库 monorepo）
pip install -e packages/nonebot-plugin-mface-pool
```

依赖：`nonebot2>=2.5,<3`、`nonebot-adapter-onebot>=2.4`。
适配器：**OneBot v11**（`~onebot.v11`，NapCat / go-cqhttp 等反向 WS 实现）。

## 快速开始

```dotenv
MFACE_ENABLED=true
MFACE_GROUPS=10000            # 必须显式配置，未配置则插件不生效
MFACE_STORE_PATH=data/mface_pool/pool.json
```

无需任何命令，配置后自动在指定群收集/回发。第一次运行会在
`MFACE_STORE_PATH` 所在目录创建 `pool.json` 与 `blobs/`。

## 配置

所有配置通过环境变量读取，均有默认值。

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `MFACE_ENABLED` | `false` | 插件总开关 |
| `MFACE_GROUPS` | 空 | **生效群白名单；空 = 插件不生效**。逗号/空格/分号分隔 |
| `MFACE_GROUPS_BLACKLIST` | 空 | 群黑名单（优先于白名单） |
| `MFACE_BLOCK_USERS` | 空 | 不收集/不回发其消息的用户 QQ 黑名单 |
| `MFACE_REPLY_RATE` | `0.02` | 回发概率上限（0~1），含表情消息用该概率抽签 |
| `MFACE_STICKER_RATIO` | `0.12` | 表情密度基准：非表情消息概率 = `REPLY_RATE × 该值`（0~1） |
| `MFACE_POOL_MAX` | `100` | 表情池上限（最小 1）；超额按权重最低淘汰 |
| `MFACE_RECENT_EXCLUDE` | `10` | 最近 N 条已发送记录不重复（0 = 不限制；上限为池上限） |
| `MFACE_WEIGHT_POWER` | `1.2` | 收集频数加权指数 `(seen+1)^power`（0.1~5.0） |
| `MFACE_DECAY_HALF_LIFE` | `14` | 时间衰减半衰期（天）；0 = 关闭衰减，负数视为关闭 |
| `MFACE_DETECT_ROBOTS` | `true` | 是否通过 `get_group_member_info` 的 `is_robot` 过滤官方机器人 |
| `MFACE_ROBOT_CACHE_TTL` | `3600` | 机器人标记缓存秒数（最小 1） |
| `MFACE_STORE_PATH` | `data/mface_pool/pool.json` | 持久化路径（相对 bot 运行目录；生产建议绝对路径/持久卷） |
| `MFACE_PRUNE_MARKER` | `/tmp/mface_pool_prune.flag` | 一次性清理标记文件；存在时启动即剔除不可发送条目 |

概率口径：`REPLY_RATE=0.02` + `STICKER_RATIO=0.12` 时，非表情消息命中率约 `0.24%`，
含表情消息约 `2%`——即以「约 12% 的表情密度」为基准把总发送量折算到全部消息的约 `0.24%`。

## 表情池行为

- **去重**：
  - 商城表情按 `emoji_package_id:emoji_id`（`emoji_id` 是内容寻址 id，`key` 轮换不影响）；
  - 收藏表情按 `file`（内容 MD5）的纯文件名，带子目录前缀也视为同一条。
- **淘汰**：超额时按 `item_weight`（`(seen+1)^power × 0.5^(未出现天数/半衰期)`）最低者出局；
  权重相同时按 `last_seen` 更旧 → 池内更靠前。新收集条目 `last_seen` 最新，不会被刚收进来就挤掉。
- **发送形态**：商城表情用 `mface` 段；收藏表情必须已保存图片字节（`blobs/`，上限 5MB/条），
  发送时用 `image` + `sub_type=1` + `base64://` 数据（裸 MD5 与过期 url 都不可靠）。
- **提取/频数**：重复收集同一条目会累加 `seen`、刷新 `last_seen`、更新 `key`/`summary`/`url`。
- **原子落盘**：临时文件 + `os.replace`，写盘失败只告警不影响回复；坏文件自动隔离为
  `pool.json.corrupt-<时间戳>` 并从空池开始。

## NapCat / 私有协议兼容范围

以下形态来自对 NapCat 实际收发的观察（不同 NapCat/QQ 版本可能变化，升级后请先小范围验证）：

| 方向 | 商城表情（mface） | 收藏表情（sticker） |
|---|---|---|
| 接收 | 被转成 `image` 段，额外带 `emoji_id` / `emoji_package_id` / `key`（**没有** `type=mface` 来报段） | `image` 段 + `sub_type=1` + `summary=[动画表情]`，无 `emoji_*` 字段 |
| 发送 | `{"type":"mface","data":{emoji_package_id, emoji_id, key, summary}}` | `{"type":"image","data":{file:"base64://…", sub_type:1, summary}}` |

- 也兼容原生 `type=mface` 来报段（若 NapCat 未来改回）。
- 普通图片（`sub_type=0`、无 `emoji_id`）与 `face` 小黄脸不会被收集。
- 收藏表情 url 带 rkey，几小时即失效，所以必须在收到消息时立刻落盘字节；
  `file` 是内容 MD5，可作为稳定去重键。

## 隐私说明

- **会缓存群友发送的表情图片字节**到本地 `blobs/` 目录（每张上限 5MB），并记录
  表情 id/摘要等元数据；这是「回发同款表情」功能所必需。
- 数据只存在部署机本地，不会上传到任何第三方；插件不访问除 QQ 官方图片 CDN 以外的网络
  （收藏表情字节通过消息里的官方 URL 下载，且恢复完整 TLS 证书校验）。
- 日志只打印群号、QQ 号、表情 id/摘要、池大小与错误类型，不打印图片内容。
- 请只在**你负责的、已告知成员的群**启用；`MFACE_GROUPS` 必须显式配置，
  不要把插件指向不相关群。群成员要求删除数据时，删除 `MFACE_STORE_PATH` 与 `blobs/` 即可。
- 插件会按需调用 `get_group_member_info` 查询发送者是否机器人（结果缓存）。
- 停用后不再收集/回发，但已落盘数据不会自动删除。

## 维护模式

本项目来自个人 bot 部署的开源整理版，按「被动维护」模式提供：
功能已满足自用需求，欢迎 issue/PR 修 bug 与适配新版 NapCat / NoneBot，
但不保证持续功能开发。QQ 私有协议字段变化可能导致收集/发送失效，请以实测为准。

## License

MIT，见仓库根目录 [LICENSE](../../LICENSE)。
