import re
from .base import BaseParser, MediaResult
from ..utils import HttpUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class InstagramParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "instagram"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["instagram.com", "instagr.am"])

    async def parse(self, url: str) -> MediaResult:
        # Instagram 解析通常需要 Cookie 或使用 oEmbed
        # oEmbed 只能获取有限信息，且有时需要 Access Token
        oembed_url = f"https://api.instagram.com/oembed/?url={url}"
        data = await HttpUtils.fetch(oembed_url)
        
        if not data:
            # 备选方案：直接尝试页面解析（通常会被拦截）
            return MediaResult(platform=self.platform_name, url=url, error="Instagram API error (Cookie may be required)", error_code=500)

        return MediaResult(
            platform=self.platform_name,
            title=data.get("title", "Instagram Post"),
            desc=f"By {data.get('author_name', 'unknown')}",
            cover=data.get("thumbnail_url", ""),
            url=url,
            media_url=url # 实际直链需 Cookie 或更复杂的逻辑
        )
