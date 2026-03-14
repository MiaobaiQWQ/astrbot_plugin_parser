import re
from .base import BaseParser, MediaResult
from ..utils import HttpUtils
import logging
import asyncio
import aiohttp

logger = logging.getLogger("astrbot_plugin_parser")

class YoutubeParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "youtube"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["youtube.com", "youtu.be"])

    async def parse(self, url: str) -> MediaResult:
        try:
            import yt_dlp
        except ImportError:
            return MediaResult(platform=self.platform_name, url=url, error="yt-dlp not installed", error_code=500)
        
        try:
            loop = asyncio.get_event_loop()
            # Run yt-dlp in a separate thread to avoid blocking the event loop
            def extract_info():
                ydl_opts = {
                    'format': 'best[ext=mp4]/best',
                    'quiet': True,
                    'no_warnings': True,
                    'noplaylist': True,
                    # cookiefile if needed
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=False)
            
            info = await loop.run_in_executor(None, extract_info)
            
            if not info:
                 return MediaResult(platform=self.platform_name, url=url, error="yt-dlp extraction failed", error_code=500)

            title = info.get('title', 'YouTube Video')
            author = info.get('uploader', 'unknown')
            channel_id = info.get('channel_id')
            media_url = info.get('url', '')
            cover = info.get('thumbnail', '')
            duration = info.get('duration', 0)
            view_count = info.get('view_count', 0)
            like_count = info.get('like_count', 0)
            
            author_avatar = ""
            if channel_id:
                author_avatar = await self._fetch_author_avatar(channel_id)

            return MediaResult(
                platform=self.platform_name,
                title=title,
                author=author,
                author_avatar=author_avatar,
                desc=title,
                cover=cover,
                url=url,
                media_url=media_url,
                duration=duration,
                view_count=view_count,
                like_count=like_count
            )

        except Exception as e:
             logger.error(f"YouTube parsing failed: {e}")
             return MediaResult(platform=self.platform_name, url=url, error=f"YouTube parsing failed: {e}", error_code=500)

    async def _fetch_author_avatar(self, channel_id: str) -> str:
        """Fetch channel avatar using YouTube internal API"""
        url = "https://www.youtube.com/youtubei/v1/browse?prettyPrint=false"
        payload = {
            "context": {
                "client": {
                    "hl": "en",
                    "gl": "US",
                    "clientName": "WEB",
                    "clientVersion": "2.20230920.00.00", # Example version
                }
            },
            "browseId": channel_id,
        }
        
        try:
            data = await HttpUtils.fetch(url, method="POST", json_data=payload)
            if data:
                # Traverse JSON to find avatar
                # metadata -> channelMetadataRenderer -> avatar -> thumbnails -> [0] -> url
                metadata = data.get("metadata", {}).get("channelMetadataRenderer", {})
                thumbnails = metadata.get("avatar", {}).get("thumbnails", [])
                if thumbnails:
                    return thumbnails[0].get("url", "")
        except Exception as e:
            logger.warning(f"Failed to fetch YouTube channel avatar: {e}")
        return ""
