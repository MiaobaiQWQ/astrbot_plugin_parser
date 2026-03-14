import re
import aiohttp
from .base import BaseParser, MediaResult
from ..utils import HttpUtils, FileUtils
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class BilibiliParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "bilibili"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["bilibili.com", "b23.tv", "t.bilibili.com"])

    async def parse(self, url: str, config: dict = None) -> MediaResult:
        # 处理短链
        real_url = url
        if "b23.tv" in url:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, allow_redirects=True) as resp:
                    real_url = str(resp.url)
        
        # 提取 ID (BV/av/opus/dynamic)
        bvid = self._extract_bvid(real_url)
        avid = re.search(r"av(\d+)", real_url)
        opus_id = re.search(r"opus/(\d+)", real_url)
        dynamic_id = re.search(r"dynamic/(\d+)", real_url)
        
        if bvid or avid:
            # 视频解析
            param = f"bvid={bvid}" if bvid else f"aid={avid.group(1)}"
            api_url = f"https://api.bilibili.com/x/web-interface/view?{param}"
            data = await HttpUtils.fetch(api_url)
            
            if not data or data.get("code") != 0:
                return MediaResult(platform=self.platform_name, error="Bilibili Video API error", error_code=500)

            item = data["data"]
            stat = item.get("stat", {})
            owner = item.get("owner", {})
            
            # 获取视频流链接
            cid = item.get("cid", 0)
            media_url = f"https://www.bilibili.com/video/{item['bvid']}" # 默认网页链接
            size = 0  # Initialize size
            if cid:
                try:
                    # 尝试获取 1080P (qn=80)
                    qn = 80
                    headers = {}
                    if config and config.get("bilibili_cookie"):
                        headers["Cookie"] = config["bilibili_cookie"]
                    
                    play_api = f"https://api.bilibili.com/x/player/playurl?avid={item['aid']}&cid={cid}&qn={qn}&type=&otype=json&platform=html5&high_quality=1"
                    play_data = await HttpUtils.fetch(play_api, headers=headers)
                    if play_data and play_data.get("code") == 0:
                        durl = play_data["data"].get("durl", [])
                        if durl:
                            media_url = durl[0]["url"]
                            # 优先使用 API 返回的大小
                            if "size" in durl[0]:
                                size = durl[0]["size"]
                except Exception as e:
                    logger.warning(f"Bilibili playurl fetch failed: {e}")

            # 获取文件大小 (如果 API 没返回)
            if size == 0 and media_url:
                size = await FileUtils.get_file_size(media_url, headers={"Referer": "https://www.bilibili.com/"})
            
            return MediaResult(
                platform=self.platform_name,
                title=item["title"],
                author=owner.get("name", ""),
                author_avatar=owner.get("face", ""),
                desc=item["desc"],
                cover=item["pic"],
                url=url,
                media_url=media_url,
                duration=item["duration"],
                size=size,
                view_count=stat.get("view", 0),
                like_count=stat.get("like", 0),
                coin_count=stat.get("coin", 0),
                favorite_count=stat.get("favorite", 0),
                share_count=stat.get("share", 0),
                comment_count=stat.get("reply", 0),
                danmaku_count=stat.get("danmaku", 0)
            )
        elif opus_id or dynamic_id:
            # 动态/图文解析
            id_val = opus_id.group(1) if opus_id else dynamic_id.group(1)
            api_url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id={id_val}"
            data = await HttpUtils.fetch(api_url)
            
            if not data or data.get("code") != 0:
                return MediaResult(platform=self.platform_name, error="Bilibili Dynamic API error", error_code=500)
            
            module = data["data"]["item"]["modules"]["module_dynamic"]["major"]
            desc = data["data"]["item"]["modules"]["module_dynamic"]["desc"]["text"]
            stat = data["data"]["item"]["modules"]["module_stat"]["comment"]
            author = data["data"]["item"]["modules"]["module_author"]["name"]
            avatar = data["data"]["item"]["modules"]["module_author"]["face"]
            
            res = MediaResult(
                platform=self.platform_name,
                title=desc[:20],
                author=author,
                author_avatar=avatar,
                desc=desc,
                url=url,
                comment_count=stat.get("count", 0),
                like_count=data["data"]["item"]["modules"]["module_stat"]["like"]["count"],
                share_count=data["data"]["item"]["modules"]["module_stat"]["forward"]["count"]
            )
            
            if "opus" in module:
                res.images = [p["url"] for p in module["opus"]["pics"]]
                res.cover = res.images[0]
            elif "draw" in module:
                res.images = [p["src"] for p in module["draw"]["items"]]
                res.cover = res.images[0]
            
            return res

        return MediaResult(platform=self.platform_name, url=url, error="Unsupported Bilibili URL format", error_code=400)

    def _extract_bvid(self, url: str) -> str:
        match = re.search(r"BV[a-zA-Z0-9]+", url)
        return match.group(0) if match else ""
