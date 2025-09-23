#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
异步目录爬虫（可中断最终版）
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
                 download_html: bool = False,
                 # ===== 新增两个参数 =====
                 white: Set[str] = None,
                 black: Set[str] = None):
        # ----------- 原来已有的赋值 ----------
        self.url = url.rstrip("/")
        self.depth = depth
        self.store_dir = pathlib.Path(store_dir or urlparse(url).netloc)
        self.ext = {e.lower() for e in (ext or set())}
        self.download_html = download_html
        self._file_links: List[str] = []
        self._to_stop = asyncio.Event()
        self._running_tasks: Set[asyncio.Task] = set()
        # ----------- 新增两行 ----------
        self.white = {w.strip().lower() for w in (white or set())}
        self.black = {b.strip().lower() for b in (black or set())}
        self.excluded = []          # 给前端打印用

    # ------------ 公共 API ------------
    async def fetch(self) -> list[dict]:
        """
        1. 爬取所有文件链接
        2. 黑白名单过滤 -> dict 列表
        3. 返回格式 [{"url": str, "name": str, "size": int}, ...]
        4. self.excluded 记录被排除的文件名（供前端打印）
        """
        self.excluded = []  # 清空前端打印用
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(user_agent="Mozilla/5.0")

            # 1. 收集原始链接（存 dict，size 先给 0）
            top_links = await self._collect(page, self.url)
            dirs = [h for h in top_links if self._depth(urlparse(h).path) == 0]
            pbar_dir = tqdm(total=len(dirs), desc="ScanDirs", unit="dir")
            for d in dirs:
                await self._gather(page, d, 0)
                pbar_dir.update(1)
            pbar_dir.close()
            await browser.close()

        # 2. 去重
        raw_links = list({v["url"]: v for v in self._file_links}.values())

        # 3. 黑白名单过滤
        filtered = []
        for item in raw_links:
            name = item["name"].lower()
            if any(k in name for k in self.black):  # 黑名单优先
                self.excluded.append(item["name"])
                continue
            if self.white and not any(k in name for k in self.white):  # 白名单
                self.excluded.append(item["name"])
                continue
            filtered.append(item)

        # 4. 补全 size（HEAD 拿不到就保持 0）
        async with aiohttp.ClientSession() as sess:
            for it in filtered:
                if it["size"] == 0:
                    try:
                        async with sess.head(it["url"]) as resp:
                            it["size"] = int(resp.headers.get("content-length", 0))
                    except Exception:
                        it["size"] = 0

        # 5. 写回实例变量并返回
        self._file_links = filtered
        return self._file_links

    async def download(self, max_workers: int = 3, chunk_size: int = 8192):
        if not self._file_links:
            raise RuntimeError("请先调用 fetch()")
        self._main_task = asyncio.create_task(
            self._download_all(max_workers, chunk_size)
        )
        try:
            await self._main_task
        except asyncio.CancelledError:
            self._to_stop.set()
            await self._cancel_all()
            raise

    async def stop(self):
        """线程安全，可从 UI 直接调用"""
        self._to_stop.set()
        await self._cancel_all()

    # ------------ 内部实现 ------------
    def _depth(self, abs_path: str) -> int:
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
            if d == cur_depth + 1:
                # 改进的目录检测逻辑
                is_dir = False
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(h) as resp:
                            content_type = resp.headers.get('Content-Type', '')
                            is_dir = 'text/html' in content_type and not h.endswith(('.html', '.htm'))
                except:
                    is_dir = h.endswith('/')
                
                if is_dir:  # 是目录则递归
                    await self._gather(page, h, cur_depth + 1)
                elif self._allowed(h):  # 是文件且符合扩展名要求
                    if not self.download_html and pathlib.Path(h).suffix.lower() in {".html", ".htm"}:
                        continue
                    self._file_links.append({"url": h, "name": pathlib.Path(h).name, "size": 0})

    # ---------- 下载相关 ----------
    async def _download_all(self, max_workers: int, chunk_size: int):
        conn = aiohttp.TCPConnector(limit=30)
        timeout = aiohttp.ClientTimeout(total=None, connect=30)
        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            sem = asyncio.Semaphore(max_workers)

            async def _bounded_dl(item):
                async with sem:
                    if self._to_stop.is_set():
                        return
                    url = item["url"]
                    rel_path = unquote(urlparse(url).path).lstrip("/")
                    prefix_path = urlparse(self.url).path.lstrip("/")
                    if rel_path.startswith(prefix_path):
                        rel_path = rel_path[len(prefix_path):].lstrip("/")
                    local = self.store_dir / rel_path
                    await self._dl_one(session, url, local, chunk_size)

            tasks = [_bounded_dl(u) for u in self._file_links]
            self._running_tasks = {asyncio.create_task(t) for t in tasks}
            await tqdm_asyncio.gather(*self._running_tasks, desc="Files")

    async def _dl_one(self, session: aiohttp.ClientSession, url: str,
                      local: pathlib.Path, chunk: int):
        RETRY, BACKOFF = 10, 1
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if self._to_stop.is_set():
                    return
                resp.raise_for_status()
                remote_size = int(resp.headers.get("content-length", 0))
        except Exception:
            return

        if local.exists() and local.stat().st_size == remote_size:
            return

        headers = {}
        start_byte = 0
        if local.exists():
            start_byte = local.stat().st_size
            headers["Range"] = f"bytes={start_byte}-"
            mode = "ab"
        else:
            mode = "wb"

        for attempt in range(1, RETRY + 1):
            try:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=None, connect=30)) as resp:
                    if self._to_stop.is_set():
                        return
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0)) + start_byte
                    pbar = tqdm(total=total, unit='B', unit_scale=True,
                                desc=local.name, leave=False)
                    pbar.update(start_byte)

                    safe_make_parent(local)
                    async with aiofiles.open(local, mode) as f:
                        async for data in resp.content.iter_chunked(chunk):
                            if self._to_stop.is_set():
                                pbar.close()
                                return
                            await f.write(data)
                            pbar.update(len(data))
                    pbar.close()
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                if attempt < RETRY:
                    await asyncio.sleep(BACKOFF)
                else:
                    local.unlink(missing_ok=True)

    async def _cancel_all(self):
        """取消所有正在运行的任务"""
        for t in self._running_tasks:
            t.cancel()
        try:
            await asyncio.wait(self._running_tasks, timeout=0.1)
        except asyncio.TimeoutError:
            pass
        self._running_tasks.clear()
        self._file_links = []  # 清空文件链接列表


# ---------- 工具 ----------
def safe_make_parent(path: pathlib.Path):
    parent = path.parent
    if parent.is_file():
        parent.unlink(missing_ok=True)
    parent.mkdir(parents=True, exist_ok=True)