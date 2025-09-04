#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import sys
from pathlib import Path
from batchdownload import BatchDownload


class AsyncRunner(threading.Thread):
    def __init__(self, coro):
        super().__init__(daemon=True)
        self.coro = coro

    def run(self):
        try:
            asyncio.run(self.coro)
        except Exception as e:
            print("AsyncRunner error:", e)


class App(ttk.Frame):
    def __init__(self, master=None):
        super().__init__(master, padding=10)
        master.title("批量网页文件下载器")
        master.geometry("720x480")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.grid(sticky="nsew")

        # ---------- 变量 ----------
        self.url_var = tk.StringVar()
        self.depth_var = tk.IntVar(value=1)
        self.store_var = tk.StringVar(value="downloads")
        self.ext_var = tk.StringVar(value=".zip,.run,.7z,.rar,.so,.exe,.pdf")
        self.workers_var = tk.IntVar(value=3)
        self.running = False
        self.crawler = None

        # 设置主题
        self._set_theme()
        self._build_ui()

    # ---------- 主题 ----------
    def _set_theme(self):
        style = ttk.Style()
        if sys.platform == "win32":
            style.theme_use("vista")
        else:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass

    # ---------- UI ----------
    def _build_ui(self):
        # 参数区
        frm = ttk.LabelFrame(self, text="")
        frm.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        frm.columnconfigure(1, weight=1)  # 让输入框占满

        # URL
        ttk.Label(frm, text="链接:").grid(row=0, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(frm, textvariable=self.url_var).grid(row=0, column=1, sticky="ew", pady=2)

        # 存储目录
        ttk.Label(frm, text="目录:").grid(row=1, column=0, sticky="e", padx=(0, 4))
        dir_frm = ttk.Frame(frm)
        dir_frm.grid(row=1, column=1, sticky="ew", pady=2)
        dir_frm.columnconfigure(0, weight=1)
        ttk.Entry(dir_frm, textvariable=self.store_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dir_frm, text="浏览", command=self._browse, width=6).grid(row=0, column=1, padx=(4, 0))

        # 扩展名 / 深度 / 并发 一行
        opt_frm = ttk.Frame(frm)
        opt_frm.grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(opt_frm, text="后缀:").pack(side="left", padx=(0, 4))
        ttk.Entry(opt_frm, textvariable=self.ext_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(opt_frm, text="深度:").pack(side="left", padx=(0, 4))
        ttk.Spinbox(opt_frm, from_=0, to=20, textvariable=self.depth_var, width=4).pack(side="left", padx=(0, 8))
        ttk.Label(opt_frm, text="并发:").pack(side="left", padx=(0, 4))
        ttk.Spinbox(opt_frm, from_=1, to=20, textvariable=self.workers_var, width=4).pack(side="left")

        # 控制按钮
        btn_frm = ttk.Frame(self)
        btn_frm.grid(row=1, column=0, columnspan=2, pady=4)
        self.start_btn = ttk.Button(btn_frm, text="开始", command=self._start)
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(btn_frm, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        # 进度条
        self.pbar = ttk.Progressbar(self, orient="horizontal", mode="determinate")
        self.pbar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)

        # 日志框
        self.log = tk.Text(self, state="disabled")
        self.log.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.log.yview)
        scroll.grid(row=3, column=1, sticky="ns", pady=(4, 0))
        self.log.configure(yscrollcommand=scroll.set)

        # 行列权重
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)  # 日志占剩余高度

    # ---------- 工具 ----------
    def _log(self, txt):
        self.log.configure(state="normal")
        self.log.insert("end", txt + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _browse(self):
        path = filedialog.askdirectory()
        if path:
            self.store_var.set(path)

    def _set_running(self, flag):
        self.running = flag
        state = "disabled" if flag else "normal"
        self.start_btn.config(state=state)
        self.stop_btn.config(state="normal" if flag else "disabled")

    # ---------- 业务逻辑 ----------
    async def _run_crawler(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先填写 URL")
            return
        store = Path(self.store_var.get())
        store.mkdir(parents=True, exist_ok=True)
        ext = {e.strip().lower() for e in self.ext_var.get().split(",") if e.strip()}
        depth = self.depth_var.get()
        workers = self.workers_var.get()

        self._log("开始扫描...")
        self._set_running(True)

        crawler = BatchDownload(
            url=url,
            depth=depth,
            store_dir=str(store),
            ext=ext,
            download_html=False
        )
        self.crawler = crawler

        try:
            links = await crawler.fetch()
            total = len(links)
            if total == 0:
                self._log("未找到文件")
                return
            self._log(f"共 {total} 个文件待下载")
            self.pbar.configure(maximum=total, value=0)

            counter = {"done": 0}

            def progress_hook():
                counter["done"] += 1
                self.pbar.configure(value=counter["done"])
                self.after(0, lambda: self._log(f"{counter['done']}/{total} 完成"))

            original_dl = crawler._dl_one

            async def patched_dl(*args, **kwargs):
                await original_dl(*args, **kwargs)
                self.after(0, progress_hook)

            crawler._dl_one = patched_dl
            await crawler.download(max_workers=workers)
            self._log("全部完成！")
        except Exception as e:
            self._log(f"错误: {e}")
        finally:
            self._set_running(False)
            self.crawler = None

    def _start(self):
        if self.running:
            return
        AsyncRunner(self._run_crawler()).start()

    def _stop(self):
        if self.crawler:
            self.crawler._file_links.clear()
            self._log("已请求停止...")
        self._set_running(False)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()