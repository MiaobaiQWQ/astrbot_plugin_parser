import re
import aiohttp
import json
import random
import time
from .base import BaseParser, MediaResult
from ..utils import HttpUtils, FileUtils
import logging
from urllib.parse import quote, urlparse, parse_qs

logger = logging.getLogger("astrbot_plugin_parser")

class KuaishouParser(BaseParser):
    @property
    def platform_name(self) -> str:
        return "kuaishou"

    def match(self, url: str) -> bool:
        return any(x in url for x in ["kuaishou.com", "v.kuaishou.com", "chenzhongtech.com", "kspkg.com"])

    async def parse(self, url: str) -> MediaResult:
        real_url = url
        # 随机生成 did
        did = "".join(random.choices("0123456789abcdef", k=32))
        
        # 使用移动端 UA 以获取 H5 页面，这通常更容易解析且包含 INIT_STATE
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
            "Referer": "https://www.kuaishou.com/",
            "Cookie": f"did=web_{did}; clientid=3; client_key=65890b29"
        }
        
        # 1. 解析短链并获取最终 URL
        if "v.kuaishou.com" in url or "chenzhongtech.com" in url or "kspkg.com" in url:
            async with aiohttp.ClientSession() as session:
                try:
                    # 必须携带 header 访问短链，否则可能无法正确重定向
                    async with session.get(url, headers=headers, allow_redirects=True, timeout=10) as resp:
                        real_url = str(resp.url)
                except Exception as e:
                    logger.warning(f"Kuaishou redirect failed: {str(e)}")
        
        # 2. 提取 Photo ID
        photo_id = ""
        try:
            parsed = urlparse(real_url)
            params = parse_qs(parsed.query)
            if "photoId" in params:
                photo_id = params["photoId"][0]
        except:
            pass

        if not photo_id:
            # 尝试从 URL 路径提取
            match = re.search(r"short-video/([a-zA-Z0-9]+)", real_url)
            if match:
                photo_id = match.group(1)
            else:
                match = re.search(r"photoId=([a-zA-Z0-9]+)", real_url)
                if match:
                    photo_id = match.group(1)
        
        # 尝试从页面内容中提取更多信息
        content = ""
        try:
            content = await HttpUtils.fetch(real_url, headers=headers)
        except Exception:
            pass

        if not photo_id and content:
            match = re.search(r'"photoId":"([a-zA-Z0-9]+)"', content)
            if match:
                photo_id = match.group(1)
        
        # 3. 尝试从 H5 页面数据直接解析 (INIT_STATE) - 移动端优先
        if content:
            try:
                # 查找 window.INIT_STATE
                start_pattern = "window.INIT_STATE = "
                start_index = content.find(start_pattern)
                if start_index != -1:
                    start_index += len(start_pattern)
                    end_index = content.find("</script>", start_index)
                    if end_index != -1:
                        json_str = content[start_index:end_index].strip()
                        if json_str.endswith(";"):
                            json_str = json_str[:-1]
                        
                        data = json.loads(json_str)
                        video_info = self._find_photo_in_json(data)
                        
                        if video_info:
                            return await self._create_result(video_info, url)
            except Exception as e:
                logger.debug(f"Kuaishou INIT_STATE parsing failed: {e}")

        # 4. 尝试从 PC 页面数据直接解析 (window.pageData)
        if content:
            # 尝试提取 window.pageData
            try:
                page_data_match = re.search(r"window\.pageData\s*=\s*(\{.*?\});", content)
                if page_data_match:
                    page_data = json.loads(page_data_match.group(1))
                    video_info = page_data.get("video", {}) or page_data.get("videoInfo", {})
                    
                    if video_info:
                        return await self._create_result(video_info, url)
            except Exception as e:
                logger.debug(f"Kuaishou pageData parsing failed: {e}")

        if not photo_id:
            return MediaResult(platform=self.platform_name, url=url, error="无法提取 Photo ID", error_code=400)

        # 5. 使用 GraphQL API (作为主要手段)
        # GraphQL 需要 PC 端 UA
        pc_headers = headers.copy()
        pc_headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        return await self._parse_with_graphql(photo_id, url, pc_headers)

    async def _create_result(self, video_info: dict, url: str) -> MediaResult:
        title = video_info.get("caption", "") or video_info.get("desc", "") or "快手作品"
        author = video_info.get("userName", "")
        author_avatar = video_info.get("headUrl", "") or video_info.get("userHead", "") or video_info.get("userAvatarUrl", "")
        
        cover = ""
        if video_info.get("coverUrls"):
            cover = video_info.get("coverUrls", [{}])[0].get("url", "")
        elif video_info.get("poster"):
            cover = video_info.get("poster", "")
        elif video_info.get("coverUrl"):
            cover = video_info.get("coverUrl", "")

        media_url = ""
        if video_info.get("mainMvUrls"):
             media_url = video_info.get("mainMvUrls", [{}])[0].get("url", "")
        
        if not media_url:
             media_url = video_info.get("photoUrl", "") or video_info.get("srcNoMark", "")

        images = []
        if video_info.get("imgUrls"):
            raw_imgs = video_info.get("imgUrls", [])
            for img in raw_imgs:
                if isinstance(img, dict) and "url" in img:
                    images.append(img["url"])
                elif isinstance(img, str):
                    images.append(img)
        
        if not images and video_info.get("atlas"):
            atlas = video_info.get("atlas")
            if isinstance(atlas, dict):
                cdns = atlas.get("cdn", [])
                file_list = atlas.get("list", [])
                if cdns and file_list:
                    cdn = cdns[0]
                    if not cdn.startswith("http"):
                        cdn = "https://" + cdn.lstrip("/")
                    for f in file_list:
                        images.append(f"{cdn}/{f}" if not f.startswith("http") else f)
        
        # 增加对 manifest 字段的解析 (有时图片在 manifest 中)
        if not images and video_info.get("manifest"):
             try:
                 manifest = json.loads(video_info.get("manifest"))
                 if "adapter" in manifest and "image_list" in manifest["adapter"]:
                     for img in manifest["adapter"]["image_list"]:
                         if "url" in img:
                             # 快手 manifest 中的 url 有时没有协议头，且可能是 webp
                             img_url = img["url"]
                             if not img_url.startswith("http"):
                                 img_url = "https://" + img_url.lstrip("/")
                             # 尝试获取 .kpg .jpg 等格式，而不是 .webp (虽然 webp 也行，但兼容性考虑)
                             images.append(img_url)
             except:
                 pass
        
        # 兜底：如果还是没有图片，但有 coverUrls，尝试将 coverUrls 作为图片列表 (不理想但总比没有好)
        if not images and video_info.get("coverUrls"):
            for img in video_info.get("coverUrls", []):
                if isinstance(img, dict) and "url" in img:
                     images.append(img["url"])
        
        # 兜底2：ext_params 中的 atlas
        if not images and video_info.get("ext_params"):
            try:
                ext_params = video_info.get("ext_params")
                if isinstance(ext_params, str):
                    # 有时候 ext_params 是 json 字符串
                    # 但通常它比较乱，暂不深度解析，除非必要
                    pass
            except:
                pass

        # 如果是图集，且 media_url 是图片，则置空 media_url
        if images and media_url:
            if media_url in images:
                media_url = ""
            elif any(media_url.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]):
                media_url = ""
        
        size = 0
        if media_url:
            size = await FileUtils.get_file_size(media_url)

        return MediaResult(
            platform=self.platform_name,
            title=title,
            author=author,
            author_avatar=author_avatar,
            desc=title,
            cover=cover,
            url=url,
            media_url=media_url,
            images=images,
            size=size,
            view_count=video_info.get("viewCount", 0) or video_info.get("realViewCount", 0),
            like_count=video_info.get("likeCount", 0) or video_info.get("realLikeCount", 0) or video_info.get("likedCount", 0),
            comment_count=video_info.get("commentCount", 0) or video_info.get("realCommentCount", 0),
            share_count=video_info.get("shareCount", 0)
        )

    def _find_photo_in_json(self, data):
        if isinstance(data, dict):
            if "photo" in data and isinstance(data["photo"], dict):
                photo = data["photo"]
                if "mainMvUrls" in photo or "photoUrl" in photo or "imgUrls" in photo or "atlas" in photo:
                    return photo
            
            for v in data.values():
                res = self._find_photo_in_json(v)
                if res: return res
                
        elif isinstance(data, list):
            for item in data:
                res = self._find_photo_in_json(item)
                if res: return res
        return None

    async def _parse_with_graphql(self, photo_id: str, url: str, headers: dict) -> MediaResult:
        try:
            graphql_url = "https://www.kuaishou.com/graphql"
            graphql_query = {
                "operationName": "visionVideoDetail",
                "variables": {
                    "photoId": photo_id,
                    "page": "detail"
                },
                "query": "query visionVideoDetail($photoId: String, $type: String, $page: String, $webPageArea: String) { visionVideoDetail(photoId: $photoId, type: $type, page: $page, webPageArea: $webPageArea) { status photo { id duration caption coverUrl photoUrl likedCount realLikeCount shareCount viewCount realViewCount commentCount realCommentCount timestamp userName userEid userAvatarUrl manifest manifestH265 imgUrls { url } atlas { cdn list } ext_params } } }"
            }
            
            # 更新 Cookie 中的时间戳
            if "Cookie" in headers:
                headers["Cookie"] += f"; didv={int(time.time() * 1000)}"

            data = await HttpUtils.fetch(graphql_url, method="POST", json_data=graphql_query, headers=headers)
            
            if data and "data" in data and "visionVideoDetail" in data["data"]:
                detail = data["data"]["visionVideoDetail"]
                
                # 检查状态
                if detail is None or detail.get("status") != 1:
                    # 尝试备用 API 或报错
                    logger.warning(f"Kuaishou GraphQL status error: {detail}")
                else:
                    photo = detail.get("photo", {})
                    return await self._create_result(photo, url)
        except Exception as e:
            logger.warning(f"Kuaishou GraphQL failed: {e}")

        # 5. 最后尝试使用 TikHub (作为备选)
        try:
            # 使用原始 url 而不是 real_url，有些短链 TikHub 可能处理得更好，或者反之
            # 这里还是用 real_url
            api_url = f"https://api.tikhub.io/tiktok/download?url={url}" 
            # TikHub 有时需要原始短链
            
            # ... (保留原有的 TikHub 逻辑作为 fallback，但要注意它的不稳定性)
            # 简化 TikHub 逻辑
            pass 
        except:
            pass

        return MediaResult(platform=self.platform_name, url=url, error="快手 API 解析失败", error_code=500)

