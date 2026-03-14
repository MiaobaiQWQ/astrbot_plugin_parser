import re
from .base import BaseParser, MediaResult
from ..utils import HttpUtils, FileUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class WeiboParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "weibo"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["weibo.com", "weibo.cn", "video.weibo.com"])

    async def parse(self, url: str) -> MediaResult:
        # 处理移动端链接和视频重定向
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1",
            "Referer": "https://m.weibo.cn/"
        }
        
        # 提取 mid
        mid = ""
        if "detail/" in url:
            mid = url.split("detail/")[1].split("?")[0]
        elif "status/" in url:
            mid = url.split("status/")[1].split("?")[0]
        elif "show?fid=" in url:
            mid = re.search(r"fid=([\d:]+)", url).group(1)
        
        if not mid and "weibo.com" in url:
            # 尝试通过页面内容查找 mid
            content = await HttpUtils.fetch(url, headers=headers)
            if content:
                match = re.search(r'"mid":\s*"(\d+)"', content)
                if match:
                    mid = match.group(1)

        if not mid:
            return MediaResult(platform=self.platform_name, url=url, error="Could not extract Weibo MID", error_code=400)

        # 调用微博移动端 API
        api_url = f"https://m.weibo.cn/statuses/show?id={mid}"
        data = await HttpUtils.fetch(api_url, headers=headers)
        
        if not data or data.get("ok") != 1:
            return MediaResult(platform=self.platform_name, url=url, error="Weibo API error", error_code=500)

        status = data["data"]
        text = re.sub(r'<[^>]+>', '', status.get("text", "")) # 移除 HTML 标签
        user = status.get("user", {})
        
        res = MediaResult(
            platform=self.platform_name,
            title=f"{user.get('screen_name', '微博用户')} 的微博",
            author=user.get("screen_name", ""),
            author_avatar=user.get("profile_image_url", "") or user.get("avatar_hd", ""),
            desc=text,
            url=url,
            cover=status.get("page_info", {}).get("page_pic", {}).get("url", "") or status.get("thumbnail_pic", "")
        )
        
        # 检查视频
        if status.get("page_info", {}).get("type") == "video":
            urls = status["page_info"].get("urls", {})
            # 优先取高清
            media_url = urls.get("mp4_720p_mp4") or urls.get("mp4_hd_url") or urls.get("mp4_ld_mp4")
            if media_url:
                res.media_url = media_url
                res.size = await FileUtils.get_file_size(media_url)
        
        # 检查图集
        pics = status.get("pics", [])
        if pics:
            res.images = [p.get("large", {}).get("url") or p.get("url") for p in pics]
            if not res.cover:
                res.cover = res.images[0]
                
        return res
