# nonebot-plugin-mface-pool

自动收集群友的 **QQ 商城表情 / 收藏表情**，然后在群里低概率随机回发。只用本地 JSON 持久化，不依赖数据库。

## 特性

- 收到 mface / 收藏表情 → 自动入池，自动去重
- 回发按「出现频数 × 时间衰减」加权，避开最近发过的
- 支持群/用户黑白名单，自动跳过机器人
- 默认**关闭**，`MFACE_GROUPS` 为空时不生效
- 仅支持 NapCat 系实现（依赖 `mface` 消息段与 `image + sub_type=1`）

## 安装

```bash
nb plugin install nonebot-plugin-mface-pool
```

或 `pip install nonebot-plugin-mface-pool`，并在 `[tool.nonebot]` 的 `plugins` 里加 `nonebot_plugin_mface_pool`。

## 配置

| 配置项 | 默认 | 说明 |
|:---:|:---:|:---|
| `MFACE_ENABLED` | `false` | 总开关 |
| `MFACE_GROUPS` | 空 | 生效群；**空 = 不生效** |
| `MFACE_GROUPS_BLACKLIST` | 空 | 排除的群 |
| `MFACE_BLOCK_USERS` | 空 | 排除的用户 |
| `MFACE_REPLY_RATE` | `0.02` | 表情消息的回发概率 |
| `MFACE_STICKER_RATIO` | `0.12` | 其它消息的额外概率系数 |
| `MFACE_POOL_MAX` | `100` | 池上限 |
| `MFACE_RECENT_EXCLUDE` | `10` | 最近不重复条数 |
| `MFACE_STORE_PATH` | `data/mface_pool/pool.json` | 数据文件 |

## 使用

无需命令：

1. 配好 `MFACE_ENABLED=true` 与 `MFACE_GROUPS`；
2. 群里有人发表情 → 自动学习；
3. 之后按概率自动回发。

## 隐私

表情池会缓存群友发送的图片字节（`blobs/`），只在本地保存；删 `MFACE_STORE_PATH` 所在目录即可清空。

## License

[MIT](./LICENSE)
