import aiohttp
import asyncio
import re
import json
from typing import Optional, Any
import logging

logger = logging.getLogger("astrbot_plugin_parser")

class HttpUtils:
    @staticmethod
    async def fetch(
        url: str, 
        method: str = "GET", 
        headers: Optional[dict] = None, 
        params: Optional[dict] = None, 
        json_data: Optional[dict] = None,
        timeout: int = 10,
        retries: int = 3
    ) -> Optional[Any]:
        """异步请求工具，带重试逻辑"""
        default_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        if headers:
            default_headers.update(headers)
        
        async with aiohttp.ClientSession() as session:
            for i in range(retries):
                try:
                    async with session.request(
                        method, 
                        url, 
                        headers=default_headers, 
                        params=params, 
                        json=json_data, 
                        timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as response:
                        if response.status == 200:
                            if "application/json" in response.headers.get("Content-Type", ""):
                                return await response.json()
                            return await response.text()
                        else:
                            logger.warning(f"Request failed: {url}, status: {response.status}, retry {i+1}")
                except Exception as e:
                    logger.error(f"Request error: {url}, error: {str(e)}, retry {i+1}")
                
                if i < retries - 1:
                    await asyncio.sleep(1)
            return None

def extract_urls(text: str) -> list[str]:
    """从文本中提取所有 URL"""
    if not text:
        return []
    url_pattern = r'https?://[^\s<>"]+'
    return re.findall(url_pattern, text)

def extract_urls_from_json(json_str: str) -> list[str]:
    """从 JSON 字符串中提取特定字段的 URL (针对小程序卡片)"""
    urls = []
    # 针对 drdon1234 提到的 key
    # 以及其他平台常见的 key
    keys = ["qqdocurl", "jumpUrl", "url", "jump_url", "link", "share_url", "target_url"]
    # 可能包含 URL 的文本字段
    text_keys = ["desc", "description", "title", "summary", "content"]
    
    try:
        data = json.loads(json_str)
        def search(d):
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, str):
                        if k in keys and v.startswith("http"):
                            urls.append(v)
                        elif k in text_keys:
                            # 尝试从文本字段提取 URL (如快手分享的描述中可能包含链接)
                            urls.extend(extract_urls(v))
                    else:
                        search(v)
            elif isinstance(d, list):
                for item in d:
                    search(item)
        search(data)
    except:
        # 如果不是标准 JSON，尝试正则匹配所有 URL
        urls.extend(extract_urls(json_str))
    return list(set(urls))

def extract_urls_from_xml(xml_str: str) -> list[str]:
    """从 XML 字符串中提取 URL (针对 XML 卡片)"""
    if not xml_str:
        return []
    # 简单粗暴：正则提取所有 http/https 链接
    # XML 卡片中的 URL 通常在属性值中，如 url="http..." 或 >http...<
    # 有时会被转义 (如 &amp;)，需要处理
    
    # 1. 提取所有类似 URL 的字符串
    raw_urls = extract_urls(xml_str)
    
    # 2. 处理转义字符
    urls = []
    for u in raw_urls:
        u = u.replace("&amp;", "&")
        u = u.replace("&lt;", "<")
        u = u.replace("&gt;", ">")
        urls.append(u)
        
    return list(set(urls))

def clean_url(url: str) -> str:
    """清洗 URL，去掉多余参数"""
    # 简单处理，具体平台可自行覆盖
    return url.split('?')[0] if '?' in url else url

class FileUtils:
    @staticmethod
    async def get_file_size(url: str, headers: Optional[dict] = None) -> int:
        """获取远程文件大小（字节）"""
        if not url:
            return 0
            
        default_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        if headers:
            default_headers.update(headers)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, headers=default_headers, allow_redirects=True, timeout=5) as resp:
                    if resp.status == 200:
                        content_length = resp.headers.get("Content-Length")
                        if content_length and content_length.isdigit():
                            return int(content_length)
        except Exception as e:
            logger.warning(f"Failed to get file size for {url}: {e}")
        return 0
