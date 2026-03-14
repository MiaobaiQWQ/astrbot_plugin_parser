import hashlib
import json
import inspect
try:
    import aioredis
except ImportError:
    aioredis = None
from typing import List, Optional
from .base import BaseParser, MediaResult
from .bilibili import BilibiliParser
from .douyin import DouyinParser
from .xiaohongshu import XiaohongshuParser
from .acfun import AcfunParser
from .tiktok import TiktokParser
from .instagram import InstagramParser
from .youtube import YoutubeParser
from .twitter import TwitterParser
from .kuaishou import KuaishouParser
from .weibo import WeiboParser

class ParserManager:
    def __init__(self, redis_url: str = "redis://localhost", expire: int = 300):
        self.parsers: List[BaseParser] = [
            BilibiliParser(),
            DouyinParser(),
            XiaohongshuParser(),
            AcfunParser(),
            TiktokParser(),
            InstagramParser(),
            YoutubeParser(),
            KuaishouParser(),
            TwitterParser(),
            WeiboParser(),
        ]
        self.redis_url = redis_url
        self.expire = expire
        self._redis = None

    async def _get_redis(self):
        if not aioredis:
            return None
        if not self._redis:
            try:
                self._redis = await aioredis.from_url(self.redis_url)
            except Exception:
                return None
        return self._redis

    def _get_url_hash(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    def get_parser(self, url: str) -> Optional[BaseParser]:
        for parser in self.parsers:
            if parser.match(url):
                return parser
        return None

    async def parse_url(self, url: str, config: dict = None) -> MediaResult:
        # 尝试从缓存读取
        redis = await self._get_redis()
        url_hash = self._get_url_hash(url)
        if redis:
            cached = await redis.get(f"parser_cache:{url_hash}")
            if cached:
                return MediaResult(**json.loads(cached))

        parser = self.get_parser(url)
        if not parser:
            return MediaResult(platform="unknown", url=url, error="No matching parser found", error_code=404)
        
        try:
            # Check signature
            sig = inspect.signature(parser.parse)
            if "config" in sig.parameters:
                res = await parser.parse(url, config=config)
            else:
                res = await parser.parse(url)
            # 写入缓存
            if redis and res.error_code == 0:
                await redis.set(f"parser_cache:{url_hash}", res.model_dump_json(), ex=self.expire)
            return res
        except Exception as e:
            return MediaResult(platform=parser.platform_name, url=url, error=str(e), error_code=500)

manager = ParserManager()
