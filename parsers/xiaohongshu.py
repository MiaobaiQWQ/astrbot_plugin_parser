import re
import aiohttp
import json
from .base import BaseParser, MediaResult
from ..utils import HttpUtils, FileUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class XiaohongshuParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "xiaohongshu"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["xiaohongshu.com", "xhslink.com"])

    async def parse(self, url: str) -> MediaResult:
        real_url = url
        if "xhslink.com" in url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, allow_redirects=True) as resp:
                    real_url = str(resp.url)
        
        # 提取 note_id
        match = re.search(r"/explore/([a-zA-Z0-9]+)", real_url)
        if not match:
            match = re.search(r"/discovery/item/([a-zA-Z0-9]+)", real_url)
        
        if not match:
            return MediaResult(platform=self.platform_name, url=url, error="Could not extract Note ID", error_code=400)
        
        note_id = match.group(1)
        
        # 小红书解析通常需要特定的 headers 或通过网页解析
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Cookie": "" # 敏感信息应从配置获取
        }
        
        content = await HttpUtils.fetch(real_url, headers=headers)
        if not content:
            return MediaResult(platform=self.platform_name, url=url, error="Failed to fetch page", error_code=500)
        
        # 从页面提取数据 (window.__INITIAL_STATE__)
        match = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});', content)
        if not match:
            return MediaResult(platform=self.platform_name, url=url, error="Initial state not found", error_code=500)
            
        try:
            state = json.loads(match.group(1))
            # 这里的结构可能很复杂，且经常变动，需根据实际情况调整
            # 假设 noteDetailMap 结构存在
            note_detail_map = state.get("note", {}).get("noteDetailMap", {})
            # 有时 note_id 不在 map key 中，或者 key 是 note_id
            target_note = note_detail_map.get(note_id, {})
            if not target_note:
                # 尝试获取第一个 value
                if note_detail_map:
                    target_note = list(note_detail_map.values())[0]

            note_data = target_note.get("note", {})
            if not note_data:
                # 尝试直接从 state.note.note 获取 (单页模式)
                note_data = state.get("note", {}).get("note", {})

            if not note_data:
                return MediaResult(platform=self.platform_name, url=url, error="Note data empty", error_code=404)
            
            user_info = note_data.get("user", {})
            
            res = MediaResult(
                platform=self.platform_name,
                title=note_data.get("title", ""),
                desc=note_data.get("desc", ""),
                author=user_info.get("nickname", ""),
                author_avatar=user_info.get("avatar", ""),
                url=url,
                cover=note_data.get("imageList", [{}])[0].get("url", ""),
                view_count=0, # XHS often hides view count in API
                like_count=note_data.get("liked_count", 0),
                comment_count=note_data.get("comment_count", 0),
                share_count=note_data.get("share_count", 0),
                favorite_count=note_data.get("collected_count", 0)
            )
            
            if note_data.get("type") == "video":
                # 视频逻辑
                video_dict = note_data.get("video", {})
                media_url = video_dict.get("media", {}).get("stream", {}).get("h264", [{}])[0].get("masterUrl", "")
                if media_url:
                    res.media_url = media_url
                    res.size = await FileUtils.get_file_size(media_url)
            else:
                # 图集
                res.images = [img.get("url", "") for img in note_data.get("imageList", [])]
                
            return res
        except Exception as e:
            return MediaResult(platform=self.platform_name, url=url, error=f"Parse error: {str(e)}", error_code=500)
