import re
import json
from .base import BaseParser, MediaResult
from ..utils import HttpUtils, FileUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class TwitterParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "twitter"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["twitter.com", "x.com"])

    async def parse(self, url: str) -> MediaResult:
        # 提取推文 ID
        match = re.search(r"status/(\d+)", url)
        if not match:
            return MediaResult(platform=self.platform_name, url=url, error="Could not extract Tweet ID", error_code=400)
        
        tweet_id = match.group(1)
        
        # 使用 vxtwitter API
        # 使用 api.vxtwitter.com 还是 api.fxtwitter.com? 通常 vxtwitter 更稳定
        api_url = f"https://api.vxtwitter.com/Twitter/status/{tweet_id}"
        
        data = await HttpUtils.fetch(api_url)
        if not data:
            return MediaResult(platform=self.platform_name, url=url, error="Twitter/X API error (vxtwitter)", error_code=500)

        # 构造详细描述
        user_name = data.get("user_name", "unknown")
        screen_name = data.get("user_screen_name", "unknown")
        text = data.get("text", "")
        likes = data.get("likes", 0)
        retweets = data.get("retweets", 0)
        
        res = MediaResult(
            platform=self.platform_name,
            title=text[:30], # 标题取前30字
            author=f"{user_name} (@{screen_name})",
            author_avatar=data.get("user_profile_image_url", ""),
            desc=text,
            url=url,
            like_count=likes,
            share_count=retweets,
            comment_count=data.get("replies", 0)
        )
        
        media_list = data.get("media_extended", [])
        images = []
        for m in media_list:
            m_type = m.get("type")
            m_url = m.get("url")
            if m_type == "image":
                images.append(m_url)
            elif m_type in ["video", "gif"]:
                res.media_url = m_url
                res.duration = m.get("duration_millis", 0) // 1000
                res.cover = m.get("thumbnail_url", "")
                res.size = await FileUtils.get_file_size(m_url)
        
        # 如果有图集且没有视频，则填充 images
        if images:
            res.images = images
            if not res.cover:
                res.cover = images[0]
        elif not res.cover and data.get("media_urls"):
            res.cover = data.get("media_urls")[0]
            
        return res
