import re
import aiohttp
from .base import BaseParser, MediaResult
from ..utils import HttpUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class TiktokParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "tiktok"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["tiktok.com", "v.tiktok.com"])

    async def parse(self, url: str) -> MediaResult:
        real_url = url
        if "v.tiktok.com" in url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, allow_redirects=True) as resp:
                    real_url = str(resp.url)
        
        # 提取 item_id
        match = re.search(r"video/(\d+)", real_url)
        if not match:
            return MediaResult(platform=self.platform_name, url=url, error="Could not extract TikTok video ID", error_code=400)
        
        video_id = match.group(1)
        # TikTok 的 API 比较复杂，通常需要 headers 和第三方服务。
        # 这里展示通过 oEmbed 获取基本信息，完整解析需更复杂的 logic。
        oembed_url = f"https://www.tiktok.com/oembed?url={real_url}"
        data = await HttpUtils.fetch(oembed_url)
        
        if not data:
            return MediaResult(platform=self.platform_name, url=url, error="TikTok API error", error_code=500)

        return MediaResult(
            platform=self.platform_name,
            title=data.get("title", "TikTok 视频"),
            desc=data.get("author_name", ""),
            cover=data.get("thumbnail_url", ""),
            url=url,
            media_url=real_url # 实际直链需调用第三方 API 或更深层次解析
        )
