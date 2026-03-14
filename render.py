
import asyncio
import io
import uuid
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache, wraps
from io import BytesIO
from pathlib import Path
from typing import ClassVar, ParamSpec, TypeVar, Optional

import aiohttp
from PIL import Image, ImageDraw, ImageFont

# 尝试导入 apilmoji，如果不存在则提供回退方案
try:
    from apilmoji import Apilmoji, EmojiCDNSource
    from apilmoji.core import get_font_height
    HAS_APILMOJI = True
except ImportError:
    HAS_APILMOJI = False
    
    def get_font_height(font):
        return font.getbbox("Ay")[3]

    class Apilmoji:
        @staticmethod
        async def text(image, xy, lines, font, fill, line_height, source=None):
            draw = ImageDraw.Draw(image)
            y = xy[1]
            for line in lines:
                draw.text((xy[0], y), line, font=font, fill=fill)
                y += line_height

from .parsers.base import MediaResult
import logging

logger = logging.getLogger("astrbot_plugin_parser")

# 定义类型变量
P = ParamSpec("P")
T = TypeVar("T")

Color = tuple[int, int, int]
PILImage = Image.Image

try:
    Resampling = Image.Resampling
except AttributeError:
    Resampling = Image


def suppress_exception(func: Callable[P, T]) -> Callable[P, T | None]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.debug(f"函数 {func.__name__} 执行失败: {e}")
            return None
    return wrapper


def suppress_exception_async(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T | None]]:
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.debug(f"函数 {func.__name__} 执行失败: {e}")
            return None
    return wrapper


@dataclass(eq=False, frozen=True, slots=True)
class FontInfo:
    font: ImageFont.FreeTypeFont
    line_height: int
    cjk_width: int

    def __hash__(self) -> int:
        return hash((id(self.font), self.line_height, self.cjk_width))

    @lru_cache(maxsize=400)
    def get_char_width(self, char: str) -> int:
        return int(self.font.getlength(char))

    def get_char_width_fast(self, char: str) -> int:
        if "\u4e00" <= char <= "\u9fff":
            return self.cjk_width
        else:
            return self.get_char_width(char)

    def get_text_width(self, text: str) -> int:
        if not text:
            return 0
        total_width = 0
        for char in text:
            total_width += self.get_char_width_fast(char)
        return total_width


@dataclass(eq=False, frozen=True, slots=True)
class FontSet:
    _FONT_SIZES = (
        ("name", 28),
        ("title", 30),
        ("text", 24),
        ("extra", 24),
        ("indicator", 60),
    )
    name_font: FontInfo
    title_font: FontInfo
    text_font: FontInfo
    extra_font: FontInfo
    indicator_font: FontInfo

    @classmethod
    def new(cls, font_path: Path):
        font_infos: dict[str, FontInfo] = {}
        for name, size in cls._FONT_SIZES:
            try:
                font = ImageFont.truetype(str(font_path), size)
            except OSError:
                font = ImageFont.load_default()
            
            font_infos[f"{name}_font"] = FontInfo(
                font=font,
                line_height=get_font_height(font),
                cjk_width=size,
            )
        return FontSet(**font_infos)


@dataclass(eq=False, frozen=True, slots=True)
class SectionData:
    height: int


@dataclass(eq=False, frozen=True, slots=True)
class HeaderSectionData(SectionData):
    avatar: PILImage | None
    name_lines: list[str]
    time_lines: list[str]
    text_height: int


@dataclass(eq=False, frozen=True, slots=True)
class TitleSectionData(SectionData):
    lines: list[str]


@dataclass(eq=False, frozen=True, slots=True)
class CoverSectionData(SectionData):
    cover_img: PILImage


@dataclass(eq=False, frozen=True, slots=True)
class TextSectionData(SectionData):
    lines: list[str]


@dataclass(eq=False, frozen=True, slots=True)
class ExtraSectionData(SectionData):
    lines: list[str]


@dataclass(eq=False, frozen=True, slots=True)
class RepostSectionData(SectionData):
    scaled_image: PILImage


@dataclass(eq=False, frozen=True, slots=True)
class ImageGridSectionData(SectionData):
    images: list[PILImage]
    cols: int
    rows: int
    has_more: bool
    remaining_count: int


@dataclass(eq=False, frozen=True, slots=True)
class GraphicsSectionData(SectionData):
    text_lines: list[str]
    image: PILImage
    alt_text: str | None = None


@dataclass
class RenderContext:
    result: MediaResult
    card_width: int
    content_width: int
    image: PILImage
    draw: ImageDraw.ImageDraw
    not_repost: bool = True
    y_pos: int = 0


class Renderer:
    PADDING = 25
    AVATAR_SIZE = 80
    AVATAR_TEXT_GAP = 15
    MAX_COVER_WIDTH = 1000
    MAX_COVER_HEIGHT = 800
    DEFAULT_CARD_WIDTH = 800
    MIN_CARD_WIDTH = 400
    SECTION_SPACING = 15
    NAME_TIME_GAP = 5
    AVATAR_UPSCALE_FACTOR = 2
    MIN_COVER_WIDTH = 300
    MIN_COVER_HEIGHT = 200
    MAX_IMAGE_HEIGHT = 800
    IMAGE_3_GRID_SIZE = 300
    IMAGE_2_GRID_SIZE = 400
    IMAGE_GRID_SPACING = 4
    MAX_IMAGES_DISPLAY = 9
    IMAGE_GRID_COLS = 3
    REPOST_PADDING = 12
    REPOST_SCALE = 0.88

    BG_COLOR: ClassVar[Color] = (255, 255, 255)
    TEXT_COLOR: ClassVar[Color] = (51, 51, 51)
    HEADER_COLOR: ClassVar[Color] = (0, 122, 255)
    EXTRA_COLOR: ClassVar[Color] = (136, 136, 136)
    REPOST_BG_COLOR: ClassVar[Color] = (247, 247, 247)
    REPOST_BORDER_COLOR: ClassVar[Color] = (230, 230, 230)

    _EMOJIS = "emojis"
    _RESOURCES = "resources"
    _LOGOS = "logos"
    _BUTTON_FILENAME = "media_button.png"
    _FONT_FILENAME = "SourceHanSans-VF.ttf"

    RESOURCES_DIR: ClassVar[Path] = Path(__file__).parent / _RESOURCES
    LOGOS_DIR: ClassVar[Path] = RESOURCES_DIR / _LOGOS
    DEFAULT_FONT_PATH: ClassVar[Path] = RESOURCES_DIR / _FONT_FILENAME
    DEFAULT_VIDEO_BUTTON_PATH: ClassVar[Path] = RESOURCES_DIR / _BUTTON_FILENAME

    fontset: ClassVar[FontSet] = None
    video_button_image: ClassVar[PILImage] = None
    platform_logos: ClassVar[dict[str, PILImage]] = {}

    def __init__(self, cache_expire: int = 300):
        self.cache_expire = cache_expire
        if HAS_APILMOJI:
            self.EMOJI_SOURCE = EmojiCDNSource(
                base_url="https://cdn.jsdelivr.net/npm/emoji-datasource-apple/img/apple/64/",
                style="apple",
            )
        else:
            self.EMOJI_SOURCE = None
            
        # 确保资源已加载
        if not self.fontset:
            self.load_resources()
            
        # 初始化时清理缓存
        self._clean_cache()

    def _clean_cache(self):
        cache_dir = Path(__file__).parent / "cache"
        if not cache_dir.exists():
            return
            
        now = time.time()
        for p in cache_dir.glob("card_*.png"):
            try:
                if now - p.stat().st_mtime > self.cache_expire:
                    p.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete cache file {p}: {e}")

    @classmethod
    def load_resources(cls):
        cls._load_fonts()
        cls._load_video_button()
        cls._load_platform_logos()

    @classmethod
    def _load_fonts(cls):
        font_path = cls.DEFAULT_FONT_PATH
        cls.fontset = FontSet.new(font_path)

    @classmethod
    def _load_video_button(cls):
        if cls.DEFAULT_VIDEO_BUTTON_PATH.exists():
            with Image.open(cls.DEFAULT_VIDEO_BUTTON_PATH) as img:
                cls.video_button_image: PILImage = img.convert("RGBA")
            alpha = cls.video_button_image.split()[-1]
            alpha = alpha.point(lambda x: int(x * 0.3))
            cls.video_button_image.putalpha(alpha)
        else:
            # 如果文件缺失，创建一个简单的播放按钮
            cls.video_button_image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
            draw = ImageDraw.Draw(cls.video_button_image)
            draw.polygon([(40, 30), (40, 98), (98, 64)], fill=(200, 200, 200, 100))

    @classmethod
    def _load_platform_logos(cls) -> None:
        cls.platform_logos = {}
        if cls.LOGOS_DIR.exists():
            for p in cls.LOGOS_DIR.rglob("*.png"):
                try:
                    with Image.open(p) as img:
                        cls.platform_logos[p.stem] = img.convert("RGBA")
                except Exception:
                    continue

    async def _download_image(self, url: str) -> Optional[PILImage]:
        if not url: return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        img = Image.open(BytesIO(data))
                        img.load() # 确保数据已加载
                        return img
        except Exception as e:
            logger.warning(f"Failed to download image {url}: {e}")
            return None

    async def text(self, ctx: RenderContext, xy: tuple[int, int], lines: list[str], font: FontInfo, fill: Color) -> int:
        try:
            await Apilmoji.text(
                ctx.image,
                xy,
                lines,
                font.font,
                fill=fill,
                line_height=font.line_height,
                source=self.EMOJI_SOURCE,
            )
        except Exception as e:
            logger.warning(f"Apilmoji render failed: {e}, fallback to simple text")
            # 回退到普通文本渲染
            draw = ctx.draw
            y = xy[1]
            for line in lines:
                draw.text((xy[0], y), line, font=font.font, fill=fill)
                y += font.line_height
        return font.line_height * len(lines)

    async def render(self, result: MediaResult) -> Optional[Path]:
        """渲染卡片并落盘，返回路径"""
        cache_dir = Path(__file__).parent / "cache"
        cache_dir.mkdir(exist_ok=True)
        cache_file = cache_dir / f"card_{uuid.uuid4().hex}.png"

        try:
            img = await self._create_card_image(result)
            await asyncio.to_thread(img.save, cache_file, format="PNG")
            return cache_file
        except Exception as e:
            logger.error(f"Failed to render card for result={result}: {e}", exc_info=True)
            return None

    async def _create_card_image(self, result: MediaResult, not_repost: bool = True) -> PILImage:
        card_width = self.DEFAULT_CARD_WIDTH
        content_width = card_width - 2 * self.PADDING
        sections = await self._calculate_sections(result, content_width)
        card_height = sum(section.height for section in sections)
        card_height += self.PADDING * 2 + self.SECTION_SPACING * (len(sections) - 1)

        bg_color = self.BG_COLOR if not_repost else self.REPOST_BG_COLOR
        image = Image.new("RGB", (card_width, card_height), bg_color)

        ctx = RenderContext(
            result=result,
            card_width=card_width,
            content_width=content_width,
            image=image,
            draw=ImageDraw.Draw(image),
            not_repost=not_repost,
            y_pos=self.PADDING,
        )
        await self._draw_sections(ctx, sections)
        return image

    async def _calculate_sections(self, result: MediaResult, content_width: int) -> list[SectionData]:
        sections: list[SectionData] = []

        # 1. 头部
        header_section = await self._calculate_header_section(result, content_width)
        if header_section:
            sections.append(header_section)

        # 2. 标题
        if result.title:
            title_lines = self._wrap_text(result.title, content_width, self.fontset.title_font)
            title_height = len(title_lines) * self.fontset.title_font.line_height
            sections.append(TitleSectionData(height=title_height, lines=title_lines))

        # 3. 封面 / 图片
        cover_img = None
        if result.cover:
            img = await self._download_image(result.cover)
            if img:
                cover_img = self._resize_cover(img, content_width)
        
        if cover_img:
            sections.append(CoverSectionData(height=cover_img.height, cover_img=cover_img))
        elif result.images:
            img_grid_section = await self._calculate_image_grid_section(result, content_width)
            if img_grid_section:
                sections.append(img_grid_section)

        # 4. 正文（描述）
        if result.desc:
            text_lines = self._wrap_text(result.desc, content_width, self.fontset.text_font)
            text_height = len(text_lines) * self.fontset.text_font.line_height
            sections.append(TextSectionData(height=text_height, lines=text_lines))

        # 5. 额外信息（统计数据）
        extra_text = []
        if result.view_count: extra_text.append(f"播放: {self._format_count(result.view_count)}")
        if result.danmaku_count: extra_text.append(f"弹幕: {self._format_count(result.danmaku_count)}")
        if result.like_count: extra_text.append(f"点赞: {self._format_count(result.like_count)}")
        if result.coin_count: extra_text.append(f"硬币: {self._format_count(result.coin_count)}")
        if result.favorite_count: extra_text.append(f"收藏: {self._format_count(result.favorite_count)}")
        if result.share_count: extra_text.append(f"分享: {self._format_count(result.share_count)}")
        
        if extra_text:
            extra_lines = self._wrap_text(" | ".join(extra_text), content_width, self.fontset.extra_font)
            extra_height = len(extra_lines) * self.fontset.extra_font.line_height
            sections.append(ExtraSectionData(height=extra_height, lines=extra_lines))
        
        # 6. 视频大小
        if result.media_url and result.size > 0:
            size_text = f"大小: {self._format_size(result.size)}"
            size_lines = self._wrap_text(size_text, content_width, self.fontset.extra_font)
            size_height = len(size_lines) * self.fontset.extra_font.line_height
            sections.append(ExtraSectionData(height=size_height, lines=size_lines))

        return sections
    
    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size/1024:.2f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size/(1024*1024):.2f} MB"
        else:
            return f"{size/(1024*1024*1024):.2f} GB"
    
    def _format_count(self, count: int) -> str:
        if count >= 10000:
            return f"{count/10000:.1f}万"
        return str(count)

    async def _calculate_header_section(self, result: MediaResult, content_width: int) -> HeaderSectionData | None:
        if not result.author:
            return None

        # 默认占位符
        avatar_img = self._create_avatar_placeholder()
        
        # 尝试下载头像（如果可用）
        if result.author_avatar:
            img = await self._download_image(result.author_avatar)
            if img:
                avatar_img = self._process_avatar(img)

        text_area_width = content_width - (self.AVATAR_SIZE + self.AVATAR_TEXT_GAP)
        name_lines = self._wrap_text(result.author, text_area_width, self.fontset.name_font)
        
        # 时间 - MediaResult 没有时间字段，使用“刚刚”或留空
        time_lines = [] # ["刚刚"]

        text_height = len(name_lines) * self.fontset.name_font.line_height
        if time_lines:
            text_height += self.NAME_TIME_GAP + len(time_lines) * self.fontset.extra_font.line_height
        header_height = max(self.AVATAR_SIZE, text_height)

        return HeaderSectionData(
            height=header_height,
            avatar=avatar_img,
            name_lines=name_lines,
            time_lines=time_lines,
            text_height=text_height,
        )

    def _process_avatar(self, original_img: PILImage) -> PILImage:
        """加载并处理头像（圆形裁剪，带抗锯齿）"""
        if original_img.mode != "RGBA":
            avatar_img = original_img.convert("RGBA")
        else:
            avatar_img = original_img

        scale = self.AVATAR_UPSCALE_FACTOR
        temp_size = self.AVATAR_SIZE * scale
        avatar_img = avatar_img.resize((temp_size, temp_size), Resampling.LANCZOS)

        mask = Image.new("L", (temp_size, temp_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, temp_size - 1, temp_size - 1), fill=255)

        output_avatar = Image.new("RGBA", (temp_size, temp_size), (0, 0, 0, 0))
        output_avatar.paste(avatar_img, (0, 0))
        output_avatar.putalpha(mask)

        output_avatar = output_avatar.resize((self.AVATAR_SIZE, self.AVATAR_SIZE), Resampling.LANCZOS)
        return output_avatar

    def _resize_cover(self, original_img: PILImage, content_width: int) -> PILImage:
        if original_img.mode not in ("RGB", "RGBA"):
            cover_img = original_img.convert("RGB")
        else:
            cover_img = original_img

        target_width = content_width
        if cover_img.width != target_width:
            scale_ratio = target_width / cover_img.width
            new_width = target_width
            new_height = int(cover_img.height * scale_ratio)
            if new_height > self.MAX_COVER_HEIGHT:
                scale_ratio = self.MAX_COVER_HEIGHT / new_height
                new_height = self.MAX_COVER_HEIGHT
                new_width = int(new_width * scale_ratio)
            cover_img = cover_img.resize((new_width, new_height), Resampling.LANCZOS)
        else:
            cover_img = cover_img.copy()
        return cover_img

    async def _calculate_image_grid_section(self, result: MediaResult, content_width: int) -> ImageGridSectionData | None:
        if not result.images:
            return None
        
        # 下载图片（限制为 MAX_IMAGES_DISPLAY）
        img_urls = result.images[:self.MAX_IMAGES_DISPLAY]
        total_images = len(result.images)
        has_more = total_images > self.MAX_IMAGES_DISPLAY
        remaining_count = total_images - self.MAX_IMAGES_DISPLAY
        
        processed_images = []
        for url in img_urls:
            img = await self._download_image(url)
            if img:
                # 处理网格图片
                processed_images.append(self._process_grid_image(img, content_width, len(img_urls)))

        if not processed_images:
            return None

        image_count = len(processed_images)
        if image_count == 1:
            cols, rows = 1, 1
        elif image_count in (2, 4):
            cols, rows = 2, (image_count + 1) // 2
        else:
            cols = self.IMAGE_GRID_COLS
            rows = (image_count + cols - 1) // cols

        max_img_height = max(img.height for img in processed_images)
        if len(processed_images) == 1:
            grid_height = max_img_height
        else:
            grid_height = self.IMAGE_GRID_SPACING + rows * (max_img_height + self.IMAGE_GRID_SPACING)

        return ImageGridSectionData(
            height=grid_height,
            images=processed_images,
            cols=cols,
            rows=rows,
            has_more=has_more,
            remaining_count=remaining_count,
        )

    def _process_grid_image(self, img: PILImage, content_width: int, img_count: int) -> PILImage:
        if img_count >= 2:
            img = self._crop_to_square(img)
        
        if img_count == 1:
            max_width = content_width
            max_height = min(self.MAX_IMAGE_HEIGHT, content_width)
            if img.width > max_width or img.height > max_height:
                ratio = min(max_width / img.width, max_height / img.height)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Resampling.LANCZOS)
        else:
            if img_count in (2, 4):
                num_gaps = 3
                max_size = (content_width - self.IMAGE_GRID_SPACING * num_gaps) // 2
                max_size = min(max_size, self.IMAGE_2_GRID_SIZE)
            else:
                num_gaps = self.IMAGE_GRID_COLS + 1
                max_size = (content_width - self.IMAGE_GRID_SPACING * num_gaps) // self.IMAGE_GRID_COLS
                max_size = min(max_size, self.IMAGE_3_GRID_SIZE)
            
            if img.width > max_size or img.height > max_size:
                ratio = min(max_size / img.width, max_size / img.height)
                img = img.resize((int(img.width * ratio), int(img.height * ratio)), Resampling.LANCZOS)
        return img

    def _crop_to_square(self, img: PILImage) -> PILImage:
        width, height = img.size
        if width == height: return img
        if width > height:
            left = (width - height) // 2
            return img.crop((left, 0, left + height, height))
        else:
            top = (height - width) // 2
            return img.crop((0, top, width, top + width))

    def _create_avatar_placeholder(self) -> PILImage:
        placeholder = Image.new("RGBA", (self.AVATAR_SIZE, self.AVATAR_SIZE), (0, 0, 0, 0))
        draw = ImageDraw.Draw(placeholder)
        draw.ellipse((0, 0, self.AVATAR_SIZE - 1, self.AVATAR_SIZE - 1), fill=(230, 230, 230, 255))
        # 画一个简单的头
        draw.ellipse((20, 15, 60, 55), fill=(200, 200, 200, 255))
        # 画身体
        draw.ellipse((10, 55, 70, 115), fill=(200, 200, 200, 255))
        
        # 蒙版
        mask = Image.new("L", (self.AVATAR_SIZE, self.AVATAR_SIZE), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, self.AVATAR_SIZE - 1, self.AVATAR_SIZE - 1), fill=255)
        placeholder.putalpha(mask)
        return placeholder

    async def _draw_sections(self, ctx: RenderContext, sections: list[SectionData]) -> None:
        for section in sections:
            if isinstance(section, HeaderSectionData): await self._draw_header(ctx, section)
            elif isinstance(section, TitleSectionData): await self._draw_title(ctx, section.lines)
            elif isinstance(section, CoverSectionData): self._draw_cover(ctx, section.cover_img)
            elif isinstance(section, TextSectionData): await self._draw_text(ctx, section.lines)
            elif isinstance(section, ExtraSectionData): await self._draw_extra(ctx, section.lines)
            elif isinstance(section, ImageGridSectionData): self._draw_image_grid(ctx, section)

    async def _draw_header(self, ctx: RenderContext, section: HeaderSectionData) -> None:
        x_pos = self.PADDING
        avatar = section.avatar if section.avatar else self._create_avatar_placeholder()
        ctx.image.paste(avatar, (x_pos, ctx.y_pos), avatar)

        text_x = self.PADDING + self.AVATAR_SIZE + self.AVATAR_TEXT_GAP
        avatar_center = ctx.y_pos + self.AVATAR_SIZE // 2
        text_y = avatar_center - section.text_height // 2

        text_y += await self.text(ctx, (text_x, text_y), section.name_lines, self.fontset.name_font, self.HEADER_COLOR)
        
        if section.time_lines:
            text_y += self.NAME_TIME_GAP
            await self.text(ctx, (text_x, text_y), section.time_lines, self.fontset.extra_font, self.EXTRA_COLOR)

        if ctx.not_repost and ctx.result.platform in self.platform_logos:
            logo_img = self.platform_logos[ctx.result.platform]
            logo_x = ctx.image.width - self.PADDING - logo_img.width
            logo_y = ctx.y_pos + (self.AVATAR_SIZE - logo_img.height) // 2
            ctx.image.paste(logo_img, (logo_x, logo_y), logo_img)
            
        ctx.y_pos += section.height + self.SECTION_SPACING

    async def _draw_title(self, ctx: RenderContext, lines: list[str]) -> None:
        ctx.y_pos += await self.text(ctx, (self.PADDING, ctx.y_pos), lines, self.fontset.title_font, self.TEXT_COLOR)
        ctx.y_pos += self.SECTION_SPACING

    def _draw_cover(self, ctx: RenderContext, cover_img: PILImage) -> None:
        x_pos = self.PADDING
        ctx.image.paste(cover_img, (x_pos, ctx.y_pos))
        
        # 如果是视频（存在 result.media_url），绘制播放按钮
        if ctx.result.media_url:
            button_size = 128
            button_x = x_pos + (cover_img.width - button_size) // 2
            button_y = ctx.y_pos + (cover_img.height - button_size) // 2
            # 如果封面太小，跳过按钮
            if cover_img.width < button_size or cover_img.height < button_size:
                 pass
            else:
                ctx.image.paste(self.video_button_image, (button_x, button_y), self.video_button_image)

        ctx.y_pos += cover_img.height + self.SECTION_SPACING

    async def _draw_text(self, ctx: RenderContext, lines: list[str]) -> None:
        ctx.y_pos += await self.text(ctx, (self.PADDING, ctx.y_pos), lines, self.fontset.text_font, self.TEXT_COLOR)
        ctx.y_pos += self.SECTION_SPACING

    async def _draw_extra(self, ctx: RenderContext, lines: list[str]) -> None:
        ctx.y_pos += await self.text(ctx, (self.PADDING, ctx.y_pos), lines, self.fontset.extra_font, self.EXTRA_COLOR)

    def _draw_image_grid(self, ctx: RenderContext, section: ImageGridSectionData) -> None:
        images = section.images
        if not images: return
        
        cols = section.cols
        img_spacing = self.IMAGE_GRID_SPACING
        available_width = ctx.content_width
        
        if len(images) == 1:
            max_img_size = available_width
        else:
            num_gaps = cols + 1
            calculated_size = (available_width - img_spacing * num_gaps) // cols
            max_img_size = self.IMAGE_2_GRID_SIZE if cols == 2 else self.IMAGE_3_GRID_SIZE
            max_img_size = min(calculated_size, max_img_size)

        current_y = ctx.y_pos
        for row in range(section.rows):
            row_start = row * cols
            row_end = min(row_start + cols, len(images))
            row_images = images[row_start:row_end]
            if not row_images: break
            
            max_height = max(img.height for img in row_images)
            for i, img in enumerate(row_images):
                img_x = self.PADDING + img_spacing + i * (max_img_size + img_spacing)
                img_y = current_y + img_spacing
                y_offset = (max_height - img.height) // 2
                ctx.image.paste(img, (img_x, img_y + y_offset))
                
                # 更多图片指示器
                if section.has_more and row == section.rows - 1 and i == len(row_images) - 1:
                    self._draw_more_indicator(ctx.image, img_x, img_y, img.width, img.height, section.remaining_count)
            
            current_y += img_spacing + max_height
            
        ctx.y_pos = current_y + img_spacing + self.SECTION_SPACING

    def _draw_more_indicator(self, image: PILImage, x: int, y: int, w: int, h: int, count: int):
        draw = ImageDraw.Draw(image)
        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 100))
        image.paste(overlay, (x, y), overlay)
        
        text = f"+{count}"
        font = self.fontset.indicator_font.font
        # 简单的居中计算
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text((x + (w - text_w) // 2, y + (h - text_h) // 2), text, fill=(255, 255, 255), font=font)

    def _wrap_text(self, text: str | None, max_width: int, font_info: FontInfo) -> list[str]:
        if not text: return []
        lines = []
        for paragraph in text.splitlines():
            if not paragraph:
                lines.append("")
                continue
            
            current_line = ""
            current_width = 0
            for char in paragraph:
                char_width = font_info.get_char_width_fast(char)
                if current_width + char_width > max_width:
                    lines.append(current_line)
                    current_line = char
                    current_width = char_width
                else:
                    current_line += char
                    current_width += char_width
            if current_line:
                lines.append(current_line)
        return lines
