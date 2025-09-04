#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
异步目录爬虫（最终版）
存储根目录不再带 /NVIDIA/vGPU/NVIDIA 前缀
"""
import asyncio, aiohttp, aiofiles, pathlib
from typing import List, Set
from urllib.parse import urlparse, urljoin, unquote
from playwright.async_api import async_playwright
from tqdm.asyncio import tqdm_asyncio
from tqdm import tqdm
import sys

# ---------- Windows 长路径 ----------
if sys.platform == "win32":
    import ctypes, os
    os.system('')
    ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)

class BatchDownload:
    def __init__(self,
                 url: str,
                 depth: int = 10,
                 store_dir: str = None,
                 ext: Set[str] = None,
                 download_html: bool = False):
        self.url = url.rstrip("/")
        self.depth = depth
        # 存储目录直接用用户给的短名（默认用域名）
        self.store_dir = pathlib.Path(store_dir or urlparse(url).netloc)
        self.ext = {e.lower() for e in (ext or set())}
        self.download_html = download_html
        self._file_links: List[str] = []

    # ------------ 公共 API ------------
    async def fetch(self) -> List[str]:
        """遍历并返回文件下载链接（带目录进度条）"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(user_agent="Mozilla/5.0")
            # 第一层：拿版本号目录
            top_links = await self._collect(page, self.url)
            dirs = [h for h in top_links if self._depth(urlparse(h).path) == 0]
            pbar_dir = tqdm(total=len(dirs), desc="ScanDirs", unit="dir")
            for d in dirs:
                await self._gather(page, d, 0)
                pbar_dir.update(1)
            pbar_dir.close()
            await browser.close()
        self._file_links = list(set(self._file_links))
        return self._file_links

    async def download(self, max_workers: int = 3, chunk_size: int = 8192):
        if not self._file_links:
            raise RuntimeError("请先调用 fetch()")
        await self._download_all(max_workers, chunk_size)

    # ------------ 内部实现 ------------
    def _depth(self, abs_path: str) -> int:
        """以初始化 url 路径为深度 0"""
        prefix = urlparse(self.url).path
        if abs_path.startswith(prefix):
            abs_path = abs_path[len(prefix):].lstrip("/")
        return abs_path.count("/")

    def _allowed(self, url: str) -> bool:
        suf = pathlib.Path(url).suffix.lower()
        if not self.ext:
            return True
        return suf in self.ext

    async def _collect(self, page, base_url: str):
        await page.goto(base_url, wait_until="networkidle", timeout=30_000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1_000)
        hrefs = await page.eval_on_selector_all("a[href]", "els => els.map(e=>e.href)")
        return {urljoin(base_url, h) for h in hrefs}

    async def _gather(self, page, base_url: str, cur_depth: int):
        if cur_depth > self.depth:
            return
        links = await self._collect(page, base_url)
        for h in links:
            d = self._depth(urlparse(h).path)
            # 文件链接（深度 1）
            if d == cur_depth + 1 and self._allowed(h):
                if not self.download_html and pathlib.Path(h).suffix.lower() in {".html", ".htm"}:
                    continue
                self._file_links.append(h)
            # 子目录继续递归
            elif d == cur_depth + 1 and h.endswith("/"):
                await self._gather(page, h, cur_depth + 1)

    # ---------- 下载相关 ----------
    async def _download_all(self, max_workers: int, chunk_size: int):
        # 1. 连接池可以稍微大一点，或者干脆用默认 100
        conn = aiohttp.TCPConnector(limit=30)
        timeout = aiohttp.ClientTimeout(total=None, connect=30)
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            # 2. 用 semaphore 限制“同时跑多少个任务”
            sem = asyncio.Semaphore(max_workers)

            async def _bounded_dl(url):
                async with sem:
                    # 关键：只取深度之后的相对路径，不再带 /NVIDIA/vGPU/NVIDIA
                    rel_path = unquote(urlparse(url).path).lstrip("/")
                    prefix_path = urlparse(self.url).path.lstrip("/")
                    if rel_path.startswith(prefix_path):
                        rel_path = rel_path[len(prefix_path):].lstrip("/")
                    local = self.store_dir / rel_path
                    await self._dl_one(session, url, local, chunk_size)

            tasks = [_bounded_dl(u) for u in self._file_links]
            await tqdm_asyncio.gather(*tasks, desc="Files")

    async def _dl_one(self, session: aiohttp.ClientSession, url: str,
                      local: pathlib.Path, chunk: int):
        """带 10 次重试、大小校验、断点续传"""
        RETRY, BACKOFF = 10, 1
        # 1. 探测远程大小
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                resp.raise_for_status()
                remote_size = int(resp.headers.get("content-length", 0))
        except Exception as e:
            return

        # 2. 本地大小一致 → 跳过
        if local.exists() and local.stat().st_size == remote_size:
            return

        # 3. 断点续传准备
        headers = {}
        start_byte = 0
        if local.exists():
            start_byte = local.stat().st_size
            headers["Range"] = f"bytes={start_byte}-"
            mode = "ab"
        else:
            mode = "wb"

        # 4. 重试循环
        for attempt in range(1, RETRY + 1):
            try:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=None, connect=30)) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0)) + start_byte
                    # 进度条：文件名即描述
                    pbar = tqdm(total=total, unit='B', unit_scale=True,
                                desc=local.name, leave=False)
                    pbar.update(start_byte)

                    safe_make_parent(local)
                    async with aiofiles.open(local, mode) as f:
                        async for data in resp.content.iter_chunked(chunk):
                            await f.write(data)
                            pbar.update(len(data))
                    pbar.close()
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                if attempt < RETRY:
                    await asyncio.sleep(BACKOFF)
                else:
                    if local.exists():
                        local.unlink(missing_ok=True)


# ---------- 工具 ----------
def safe_make_parent(path: pathlib.Path):
    parent = path.parent
    if parent.is_file():
        parent.unlink(missing_ok=True)
    parent.mkdir(parents=True, exist_ok=True)
