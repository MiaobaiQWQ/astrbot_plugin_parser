from abc import ABC, abstractmethod
from typing import List, Optional, Union
from pydantic import BaseModel, Field

class MediaResult(BaseModel):
    """统一返回格式"""
    platform: str
    title: str = ""
    author: str = ""  # 作者
    author_avatar: Optional[str] = None  # 作者头像
    desc: str = ""
    cover: str = ""
    url: str = ""  # 原链接
    media_url: Optional[str] = None  # 视频直链
    images: List[str] = Field(default_factory=list)  # 图集链接列表
    duration: int = 0  # 时长（秒）
    size: int = 0  # 文件大小（字节）
    
    # 统计数据
    view_count: int = 0      # 播放量
    like_count: int = 0      # 点赞
    coin_count: int = 0      # 硬币
    favorite_count: int = 0  # 收藏
    share_count: int = 0     # 分享
    comment_count: int = 0   # 评论
    danmaku_count: int = 0   # 弹幕

    error: Optional[str] = None
    error_code: int = 0  # 0 为成功

class BaseParser(ABC):
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台名称"""
        pass

    @abstractmethod
    def match(self, url: str) -> bool:
        """检查 URL 是否匹配该平台"""
        pass

    @abstractmethod
    async def parse(self, url: str, config: dict = None) -> MediaResult:
        """解析 URL 并返回统一格式"""
        pass
