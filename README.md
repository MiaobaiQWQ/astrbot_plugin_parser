# astrbot_plugin_parser

统一、可扩展的流媒体链接解析器（AstrBot 插件），支持从消息文本与卡片（Json / Xml）中提取链接，并自动解析为图文卡片、图集或视频信息发送。

## 特性

- 自动识别并解析多平台视频/图文链接
- 支持从纯文本、Json 卡片、Xml 卡片中提取链接
- 支持渲染图片卡片，并在可用时使用合并转发增强展示
- 支持群聊/私信黑白名单与忽略 QQ 列表，避免循环与误触发

## 支持平台

- Bilibili（BV/av、动态/图文）
- 抖音
- 快手
- 小红书
- 微博
- Twitter/X
- YouTube（依赖 yt-dlp）
- TikTok
- Instagram（信息获取能力受限）

## 安装

1. 将本项目目录放入 AstrBot 的插件目录中（保持目录名为 `astrbot_plugin_parser`）
2. 按照 `requirements.txt` 安装依赖
3. 在 AstrBot 中启用插件，并按需配置

## 配置说明

配置项以 `astrbot_plugin_parser/_conf_schema.json` 为准，常用项如下：

| 配置项 | 含义 | 默认值 |
| --- | --- | --- |
| `cache_expire` | 渲染缓存过期时间（秒） | `300` |
| `max_concurrency` | 最大并发解析数 | `5` |
| `ignore_qq_list` | 忽略的 QQ 号列表（防止机器人循环） | `[]` |
| `group_whitelist` | 群聊白名单（群号；不为空时仅白名单生效） | `[]` |
| `group_blacklist` | 群聊黑名单（群号；命中则不处理） | `[]` |
| `private_whitelist` | 私信白名单（QQ 号；不为空时仅白名单生效） | `[]` |
| `private_blacklist` | 私信黑名单（QQ 号；命中则不处理） | `[]` |

Cookie / Token / API Key（如 B 站 Cookie、抖音 Cookie、YouTube API Key 等）用于提升解析成功率或获取更完整信息，按需填写即可。

## 致谢

- 本插件的图片卡片渲染能力参考并复用自 [Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)
- 另有一个使用本项目图片生成能力的项目当前暂未定位到仓库地址，后续找到会补充链接与致谢
