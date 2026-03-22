import json
import re
from typing import Optional
from astrbot.api.all import *
try:
    from astrbot.api.message_components import Node, Nodes
except ImportError:
    pass # Assume in all
from .parsers import manager
from .utils import extract_urls, extract_urls_from_json, extract_urls_from_xml, clean_url
from .render import Renderer
import asyncio

@register("astrbot_plugin_parser", "drdon1234 & Zhalslar", "统一、可扩展的流媒体链接解析器", "2.4.0")
class ParserPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.renderer = Renderer(cache_expire=config.get("cache_expire", 300))

    def _get_sender_id(self, event: AstrMessageEvent) -> Optional[str]:
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None) if message_obj else None
        user_id = getattr(sender, "user_id", None) if sender else None
        if user_id is None:
            return None
        return str(user_id)

    def _get_group_id(self, event: AstrMessageEvent) -> Optional[str]:
        message_obj = getattr(event, "message_obj", None)
        if not message_obj:
            return None

        for attr in ("group_id", "groupId", "group_uin", "groupUin"):
            group_id = getattr(message_obj, attr, None)
            if group_id:
                return str(group_id)

        session_id = getattr(message_obj, "session_id", None)
        if isinstance(session_id, str) and session_id.startswith("group_"):
            return session_id.split("_", 1)[1] or None

        return None

    def _is_allowed_by_lists(self, target_id: str, whitelist: list, blacklist: list) -> bool:
        target_id = str(target_id)
        whitelist = [str(x) for x in (whitelist or []) if x is not None and str(x).strip()]
        blacklist = [str(x) for x in (blacklist or []) if x is not None and str(x).strip()]

        if target_id in blacklist:
            return False
        if whitelist and target_id not in whitelist:
            return False
        return True

    def _format_count(self, count: int) -> str:
        if count >= 10000:
            return f"{count/10000:.1f}万"
        return str(count)

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size/1024:.2f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size/(1024*1024):.2f} MB"
        else:
            return f"{size/(1024*1024*1024):.2f} GB"

    @event_message_type(EventMessageType.ALL, priority=1)
    async def on_message(self, event: AstrMessageEvent):
        sender_id = self._get_sender_id(event)
        ignore_list = self.config.get("ignore_qq_list", [])
        if sender_id and ignore_list and sender_id in [str(x) for x in ignore_list]:
            return

        group_id = self._get_group_id(event)
        if group_id:
            if not self._is_allowed_by_lists(
                group_id,
                self.config.get("group_whitelist", []),
                self.config.get("group_blacklist", []),
            ):
                return
        elif sender_id:
            if not self._is_allowed_by_lists(
                sender_id,
                self.config.get("private_whitelist", []),
                self.config.get("private_blacklist", []),
            ):
                return

        # 1. 从纯文本提取 URL
        text = event.message_str
        urls = extract_urls(text)
        
        # 1.1 尝试提取 B 站 BV/av 号 (如果不在 URL 中)
        # 匹配 BV 号 (BV1xx411c7X)
        bv_matches = re.findall(r"(BV[a-zA-Z0-9]{10})", text)
        for bv in bv_matches:
            # 简单去重：检查是否已经作为 URL 的一部分被提取了
            if not any(bv in u for u in urls):
                urls.append(f"https://www.bilibili.com/video/{bv}")
        
        # 匹配 av 号 (av170001)
        av_matches = re.findall(r"(av\d+)", text)
        for av in av_matches:
            if not any(av in u for u in urls):
                urls.append(f"https://www.bilibili.com/video/{av}")

        
        # 2. 从消息链组件 (Json 卡片等) 提取 URL
        if event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if isinstance(comp, Json):
                    # Json 组件没有 json_str 属性，需要手动序列化
                    if hasattr(comp, 'data'):
                        try:
                            json_str = json.dumps(comp.data)
                            urls.extend(extract_urls_from_json(json_str))
                        except:
                            pass
                    # 兼容可能存在的 content 属性
                    elif hasattr(comp, 'content'):
                        urls.extend(extract_urls_from_json(comp.content))
                
                # 增加对 Xml 卡片的支持
                # 检查 comp 类名是否为 Xml，或者尝试直接判断属性
                elif type(comp).__name__ == "Xml":
                    if hasattr(comp, 'content'):
                        urls.extend(extract_urls_from_xml(comp.content))
        
        if not urls:
            return

        # 去重
        urls = list(set(urls))

        # 并行解析
        tasks = [manager.parse_url(url, config=self.config) for url in urls]
        results = await asyncio.gather(*tasks)

        # 过滤并打印错误日志
        valid_results = []
        for r in results:
            if r.error_code == 0:
                valid_results.append(r)
            else:
                logger.error(f"解析失败 [{r.platform}]: {r.error} (URL: {r.url})")

        if not valid_results:
            return

        # 发送逻辑
        for res in valid_results:
            # 尝试渲染卡片
            card_path = await self.renderer.render(res)
            
            if card_path:
                try:
                    # 单独发送图片卡片
                    img_chain = MessageChain()
                    img_chain.chain.append(Image.fromFileSystem(str(card_path)))
                    await event.send(img_chain)

                    # 使用合并转发发送：原链接、描述、大小
                    nodes = []
                    
                    # Node 1: 原链接
                    nodes.append(Node(uin=event.message_obj.self_id, name="原链接", content=[Plain(clean_url(res.url))]))

                    # Node 2: 描述 (标题 + 详情)
                    desc_content = f"{res.title}"
                    if res.desc and res.desc != res.title:
                        desc_content += f"\n\n{res.desc}"
                    nodes.append(Node(uin=event.message_obj.self_id, name="描述", content=[Plain(desc_content)]))
                    
                    # Node 3: 其他图片 (如果是多图)
                    if res.images and len(res.images) > 0:
                        # 过滤无效 URL
                        valid_images = [img for img in res.images if img]
                        logger.info(f"Processing {len(valid_images)} images for merge forward")
                        
                        # 分批处理，每 20 张一个节点 (QQ 合并转发单节点限制较大，但为了稳健还是分批)
                        # 增加重试机制和错误处理
                        batch_size = 20
                        for i in range(0, len(valid_images), batch_size):
                            batch = valid_images[i:i+batch_size]
                            node_content = []
                            for img_url in batch:
                                try:
                                    # 确保是字符串
                                    if isinstance(img_url, str):
                                        node_content.append(Image.fromURL(img_url))
                                except Exception as e:
                                    logger.warning(f"Failed to create Image node for {img_url}: {e}")
                            
                            if node_content:
                                nodes.append(Node(
                                    uin=event.message_obj.self_id,
                                    name=f"图集 {i//batch_size + 1}",
                                    content=node_content
                                ))
                                logger.debug(f"Added image node with {len(node_content)} images")

                    # 发送合并转发
                    forward_chain = MessageChain()
                    forward_chain.chain.append(Nodes(nodes))
                    await event.send(forward_chain)
                    
                    # 视频单独发送，不包含在合并转发中
                    if res.media_url:
                        try:
                            video_chain = MessageChain()
                            video_chain.chain.append(Video.fromURL(res.media_url))
                            await event.send(video_chain)
                        except Exception as e:
                            logger.warning(f"Failed to send video: {e}")
                    
                    continue
                except Exception as e:
                    logger.error(f"发送渲染卡片合并转发失败，回退到普通模式: {e}")

            # 常规发送逻辑 (非小黑盒，或小黑盒合并转发失败)
            chain = MessageChain()
            
            # 标题与描述
            msg = ""
            if res.author:
                msg += f"作者: {res.author}\n"
            msg += f"标题: {res.title}\n"
            
            is_desc_long = False
            if res.desc and res.desc.strip() and res.platform not in ["kuaishou", "douyin"]:
                clean_desc = res.desc.strip()
                if clean_desc == "-": # B站空描述通常是一个横杠，忽略
                    pass
                elif res.platform == "twitter":
                    msg += f"描述: {clean_desc}\n"
                elif len(clean_desc) > 100:
                    is_desc_long = True
                    # 描述过长，不在此处显示，后续单独发送合并转发
                else:
                    msg += f"描述:{clean_desc}\n"
            
            # 数据统计 (B站等)
            if res.platform == "bilibili" and (res.view_count > 0 or res.like_count > 0):
                stats = f"播放:{self._format_count(res.view_count)} | 弹幕:{self._format_count(res.danmaku_count)} | "
                stats += f"点赞:{self._format_count(res.like_count)} | 硬币:{self._format_count(res.coin_count)} | "
                stats += f"收藏:{self._format_count(res.favorite_count)} | 分享:{self._format_count(res.share_count)}"
                msg += stats + "\n"
            
            chain.chain.append(Plain(msg))

            # 封面或图片
            if res.images:
                # 图集处理
                img_count = len(res.images)

                # Twitter specific: >3 images merge forward
                if res.platform == "twitter" and img_count > 3:
                    try:
                        nodes = []
                        # Node 1: Text
                        nodes.append(Node(uin=event.message_obj.self_id, name="推文内容", content=[Plain(msg)]))
                        
                        # Node 2: Images
                        img_node_content = []
                        for img_url in res.images:
                            img_node_content.append(Image.fromURL(img_url))
                        nodes.append(Node(uin=event.message_obj.self_id, name="推文图片", content=img_node_content))
                        
                        forward_chain = MessageChain()
                        forward_chain.chain.append(Nodes(nodes))
                        await event.send(forward_chain)
                        continue
                    except Exception as e:
                        logger.error(f"Twitter forward message failed: {e}")

                if img_count > 9:
                    # 拆分发送
                    for i in range(0, img_count, 9):
                        batch = res.images[i:i+9]
                        batch_chain = MessageChain()
                        if i == 0:
                            batch_chain.chain.append(Plain(f"【{res.platform.upper()}】{res.title} (共 {img_count} 张)\n"))
                        for img_url in batch:
                            batch_chain.chain.append(Image.fromURL(img_url))
                        await event.send(batch_chain)
                    continue # 已单独发送，跳过后续 chain 添加
                else:
                    for img_url in res.images:
                        chain.chain.append(Image.fromURL(img_url))
            elif res.cover:
                chain.chain.append(Image.fromURL(res.cover))

            # 视频链接 (仅文本)
            if res.media_url:
                info_text = f"\n视频原链接: {clean_url(res.url)}"
                if res.size > 0:
                    info_text += f"\n视频大小: {self._format_size(res.size)}"
                chain.chain.append(Plain(info_text))

            # 发送图文信息
            try:
                await event.send(chain)
            except Exception as e:
                logger.error(f"发送消息失败: {e}")

            # 如果描述过长，单独发送合并转发消息
            if is_desc_long:
                try:
                    nodes = [Node(uin=event.message_obj.self_id, name="视频描述", content=[Plain(res.desc.strip())])]
                    forward_chain = MessageChain()
                    forward_chain.chain.append(Nodes(nodes))
                    await event.send(forward_chain)
                except Exception as e:
                    logger.error(f"发送长描述合并转发失败: {e}")

            # 单独发送视频文件
            if res.media_url:
                try:
                    video_chain = MessageChain()
                    video_chain.chain.append(Video.fromURL(res.media_url))
                    await event.send(video_chain)
                except Exception as e:
                    logger.warning(f"Failed to create Video component: {e}")
