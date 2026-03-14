import re
import json
from .base import BaseParser, MediaResult
from ..utils import HttpUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class AcfunParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "acfun"

    def match(self, url: str) -> bool:
        return "acfun.cn" in url

    async def parse(self, url: str) -> MediaResult:
        # 提取 ac 号
        match = re.search(r"ac(\d+)", url)
        if not match:
            return MediaResult(platform=self.platform_name, url=url, error="Could not extract AC ID", error_code=400)
        
        ac_id = match.group(1)
        # AcFun Web API
        api_url = f"https://www.acfun.cn/v/ac{ac_id}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Referer": "https://www.acfun.cn/"
        }
        
        content = await HttpUtils.fetch(api_url, headers=headers)
        if not content:
            return MediaResult(platform=self.platform_name, url=url, error="Failed to fetch AcFun page", error_code=500)
            
        # 尝试提取视频信息
        # AcFun 页面中有 window.pageInfo 或 window.videoInfo
        try:
            title_match = re.search(r'<title>(.*?)</title>', content)
            title = title_match.group(1).replace("- AcFun 弹幕视频网 - 认真你就输了 (・ω・)ノ- ( ゜- ゜)つロ", "").strip() if title_match else "AcFun 视频"
            
            # 封面图
            cover_match = re.search(r'"coverUrl":"(.*?)"', content)
            cover = cover_match.group(1).replace("\\u002F", "/") if cover_match else ""
            
            return MediaResult(
                platform=self.platform_name,
                title=title,
                cover=cover,
                url=url,
                media_url=url # 实际直链需要更复杂的 m3u8 解析，这里先返回原链或占位
            )
        except Exception as e:
            return MediaResult(platform=self.platform_name, url=url, error=f"AcFun parse error: {str(e)}", error_code=500)
