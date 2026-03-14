import re
import aiohttp
import json
import random
from urllib.parse import urlparse, parse_qs, unquote
from .base import BaseParser, MediaResult
from ..utils import HttpUtils, FileUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class DouyinParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "douyin"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["douyin.com", "iesdouyin.com", "v.douyin.com"])

    async def parse(self, url: str) -> MediaResult:
        # 1. 获取重定向后的真实链接
        real_url = url
        # 随机生成 User-Agent
        ua_mobile = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
        headers = {
            "User-Agent": ua_mobile,
            "Referer": "https://www.douyin.com/"
        }

        if "v.douyin.com" in url:
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(url, headers=headers, allow_redirects=True) as resp:
                        real_url = str(resp.url)
                except Exception as e:
                    return MediaResult(platform=self.platform_name, url=url, error=f"Redirect failed: {e}", error_code=500)

        # 2. 尝试从页面 HTML 提取数据 (SSR 渲染数据)
        try:
            content = await HttpUtils.fetch(real_url, headers=headers)
            if content:
                # 尝试提取 RENDER_DATA
                render_data_match = re.search(r'<script id="RENDER_DATA" type="application/json">(.+?)</script>', content)
                if render_data_match:
                    try:
                        render_data = json.loads(unquote(render_data_match.group(1)))
                        # 遍历寻找 aweme 详情
                        video_info = self._find_aweme_info(render_data)
                        if video_info:
                            return await self._parse_aweme_info(video_info, url)
                    except Exception as e:
                        logger.debug(f"Douyin RENDER_DATA parsing failed: {e}")

                # 尝试提取 _SSR_RENDER_DATA (新版)
                ssr_data_match = re.search(r'<script id="SSR_RENDER_DATA" type="application/json">(.+?)</script>', content) # 或者是 _SSR_RENDER_DATA
                if not ssr_data_match:
                     ssr_data_match = re.search(r'window\._SSR_RENDER_DATA\s*=\s*(\{.+?\});', content)
                
                if ssr_data_match:
                    try:
                        ssr_data = json.loads(ssr_data_match.group(1))
                        video_info = self._find_aweme_info(ssr_data)
                        if video_info:
                            return await self._parse_aweme_info(video_info, url)
                    except Exception as e:
                         logger.debug(f"Douyin SSR_RENDER_DATA parsing failed: {e}")

                # 尝试提取 _ROUTER_DATA (新版 2024)
                # window._ROUTER_DATA = { ... }
                match = re.search(r'window\._ROUTER_DATA\s*=\s*', content)
                if match:
                    start_idx = match.end()
                    end_idx = content.find('</script>', start_idx)
                    if end_idx != -1:
                        json_str = content[start_idx:end_idx].strip()
                        if json_str.endswith(';'):
                            json_str = json_str[:-1]
                        try:
                            router_data = json.loads(json_str)
                            video_info = self._find_aweme_info(router_data)
                            if video_info:
                                return await self._parse_aweme_info(video_info, url)
                        except Exception as e:
                            logger.debug(f"Douyin _ROUTER_DATA parsing failed: {e}")

        except Exception as e:
            logger.warning(f"Douyin HTML parsing failed: {e}")

        # 3. 提取 ID 用于 API
        item_id = ""
        match = re.search(r"(?:video|note|modal_id)/(\d+)", real_url)
        if match:
            item_id = match.group(1)
        
        if not item_id:
            parsed = urlparse(real_url)
            params = parse_qs(parsed.query)
            if "modal_id" in params:
                item_id = params["modal_id"][0]

        if not item_id:
            # 如果没有 ID 且页面解析失败
            return MediaResult(platform=self.platform_name, url=url, error="Could not extract Douyin ID", error_code=400)

        # 4. 尝试 API (API 1: tikhub.io)
        try:
            api_url_1 = f"https://api.tikhub.io/tiktok/download?url={real_url}"
            data = await HttpUtils.fetch(api_url_1, headers={"User-Agent": "Mozilla/5.0"})
            if data and (data.get("data") or "title" in data):
                return await self._parse_from_tikhub_data(data, url)
        except Exception as e:
            logger.warning(f"Douyin TikHub API failed: {e}")

        # API 2: douyin.wtf (可能失效，作为备选)
        try:
            api_url_2 = f"https://api.douyin.wtf/douyin_video_data?video_id={item_id}"
            data = await HttpUtils.fetch(api_url_2)
            if data and data.get("code") == 200 and data.get("data"):
                return await self._parse_from_tikhub_data(data, url)
        except Exception as e:
            logger.warning(f"Douyin WTF API failed: {e}")
            
        # API 3: 官方 Web API (兜底，通常需要签名)
        try:
            api_url_3 = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={item_id}"
            data = await HttpUtils.fetch(api_url_3)
            if data and data.get("item_list"):
                return await self._parse_from_official_data(data, url)
        except Exception as e:
            logger.warning(f"Douyin Official API failed: {e}")

        # API 4: lovelu.top
        try:
            api_url_4 = f"https://api.lovelu.top/api/video/douyin?url={real_url}"
            data = await HttpUtils.fetch(api_url_4)
            if data and data.get("code") == 200 and data.get("data"):
                d = data["data"]
                return MediaResult(
                    platform=self.platform_name,
                    title=d.get("title", "抖音视频"),
                    author=d.get("author", ""),
                    desc=d.get("title", ""),
                    cover=d.get("cover", ""),
                    url=url,
                    media_url=d.get("play", ""),
                    size=0 
                )
        except Exception as e:
            logger.warning(f"Douyin LoveLu API failed: {e}")

        return MediaResult(platform=self.platform_name, url=url, error="All Douyin APIs failed", error_code=500)

    def _find_aweme_info(self, data):
        """递归查找 aweme_detail 或 video 结构"""
        if isinstance(data, dict):
            if "aweme_detail" in data: # 常见结构
                return data["aweme_detail"]
            if "aweme_list" in data and isinstance(data["aweme_list"], list) and len(data["aweme_list"]) > 0:
                return data["aweme_list"][0]
            if "item_list" in data and isinstance(data["item_list"], list) and len(data["item_list"]) > 0:
                 # Check if the first item looks like an aweme
                 item = data["item_list"][0]
                 if isinstance(item, dict) and ("video" in item or "aweme_id" in item):
                     return item
            if "video" in data and "play_addr" in data["video"]: # 直接是 video 对象
                return data
            
            for v in data.values():
                res = self._find_aweme_info(v)
                if res: return res
        elif isinstance(data, list):
            for item in data:
                res = self._find_aweme_info(item)
                if res: return res
        return None

    async def _parse_aweme_info(self, item: dict, url: str) -> MediaResult:
        # 处理从 HTML 提取的数据
        # 有时 item 就是整个 aweme 结构
        
        desc = item.get("desc", "抖音视频")
        author_info = item.get("author", {})
        author = author_info.get("nickname", "")
        author_avatar = ""
        if author_info.get("avatar_larger"):
             author_avatar = author_info.get("avatar_larger", {}).get("url_list", [""])[-1]
        elif author_info.get("avatar_medium"):
             author_avatar = author_info.get("avatar_medium", {}).get("url_list", [""])[-1]
        elif author_info.get("avatar_thumb"):
             author_avatar = author_info.get("avatar_thumb", {}).get("url_list", [""])[-1]
        
        # 封面
        cover = ""
        if item.get("video", {}).get("cover"):
             cover = item.get("video", {}).get("cover", {}).get("url_list", [""])[-1]
        
        # 视频地址
        media_url = ""
        video_info = item.get("video", {})
        if video_info.get("play_addr"):
            url_list = video_info.get("play_addr", {}).get("url_list", [])
            if url_list:
                media_url = url_list[-1] # 通常最后一个是高清或者 CDN 较好的
                # 尝试替换 playwm -> play (去水印)
                media_url = media_url.replace("playwm", "play")

        # 图集
        images = []
        if item.get("images"):
             images = [img.get("url_list", [""])[-1] for img in item.get("images", []) if img.get("url_list")]
        
        # 如果是图集，清空 media_url (通常图集的 video 字段是背景音乐或空)
        if images:
            media_url = ""
        
        # 获取大小
        size = 0
        if media_url:
             size = await FileUtils.get_file_size(media_url)

        return MediaResult(
            platform=self.platform_name,
            title=desc,
            author=author,
            author_avatar=author_avatar,
            desc=desc,
            cover=cover,
            url=url,
            media_url=media_url,
            images=images,
            duration=item.get("duration", 0) // 1000,
            size=size,
            view_count=item.get("statistics", {}).get("play_count", 0),
            like_count=item.get("statistics", {}).get("digg_count", 0),
            comment_count=item.get("statistics", {}).get("comment_count", 0),
            share_count=item.get("statistics", {}).get("share_count", 0),
            favorite_count=item.get("statistics", {}).get("collect_count", 0)
        )

    async def _parse_from_tikhub_data(self, data: dict, url: str) -> MediaResult:
        video_data = data.get("data", {})
        if not video_data:
             if "title" in data or "video" in data:
                 video_data = data
             else:
                return MediaResult(platform=self.platform_name, url=url, error="Douyin API response empty", error_code=500)

        # 提取数据
        title = video_data.get("title") or video_data.get("desc") or "抖音视频"
        author = video_data.get("author", {}).get("nickname", "")
        cover = video_data.get("cover", "")
        if not cover:
             cover = video_data.get("cover_data", {}).get("cover", {}).get("url_list", [""])[0]

        res = MediaResult(
            platform=self.platform_name,
            title=title,
            author=author,
            desc=title,
            cover=cover,
            url=url
        )

        # 提取图集
        if video_data.get("images"):
            res.images = video_data.get("images")
        # 提取视频
        elif video_data.get("play"): # 有些接口返回 play
             res.media_url = video_data.get("play")
             res.size = await FileUtils.get_file_size(res.media_url)
        elif video_data.get("video"): # 有些返回 video 对象
            # 优先使用高清无水印链接
            video_url = video_data.get("video", {}).get("play_addr", {}).get("url_list", [""])[0]
            if video_url:
                res.media_url = video_url
                res.size = await FileUtils.get_file_size(video_url)
        
        return res

    async def _parse_from_official_data(self, data: dict, url: str) -> MediaResult:
        item = data["item_list"][0]
        return await self._parse_aweme_info(item, url)
