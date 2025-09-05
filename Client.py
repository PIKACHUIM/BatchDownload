#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, threading, subprocess, sys, os, datetime, time, pathlib
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from ttkbootstrap import *
import aiohttp
from batchdownload import BatchDownload          # 你的原有抓取类

# ---------------- 异步协程托管 ----------------
class AsyncRunner(threading.Thread):
    """把协程丢到后台线程运行，避免阻塞 GUI"""
    def __init__(self, coro):
        super().__init__(daemon=True)
        self.coro = coro

    def run(self):
        try:
            asyncio.run(self.coro)
        except Exception as e:
            print("AsyncRunner 错误:", e)

# ---------------- 工具函数 ----------------
def fmt_size(b: int) -> str:
    """字节 -> MB 字符串"""
    return f"{b / 1024 / 1024:.2f}"

def fmt_time(sec: float) -> str:
    """秒 -> 00:00:00"""
    h, s = divmod(int(sec), 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------- Treeview 行管理 ----------------
class RowManager:
    """负责 Treeview 增删改"""
    def __init__(self, tree: ttk.Treeview):
        self.tree = tree
        self._iid_map = {}          # url -> iid

    def add(self, url: str, name: str, size: int):
        iid = self.tree.insert(
            "", "end",
            values=("等待中", name, fmt_size(size), 0, "0 MB/s", "00:00:00")
        )
        self._iid_map[url] = iid
        return iid

    def update(self, url: str, **kw):
        iid = self._iid_map.get(url)
        if not iid:
            return
        old = self.tree.item(iid, "values")
        # 按顺序替换
        vals = list(old)
        if "state" in kw:
            vals[0] = kw["state"]
        if "prog" in kw:
            vals[3] = kw["prog"]
        if "speed" in kw:
            vals[4] = kw["speed"]
        if "eta" in kw:
            vals[5] = kw["eta"]
        self.tree.item(iid, values=vals)

    def set_done(self, url: str, cost: float):
        self.update(url, state="已完成", prog=100, eta=f"✔ {fmt_time(cost)}")

    def clear(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._iid_map.clear()


# ---------------- 主界面 ----------------
class App(ttk.Frame):
    def __init__(self, master=None):
        super().__init__(master, padding=10)
        master.title("批量网页文件下载器")
        master.geometry("1000x600")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.grid(sticky="nsew")

        # ---------- 变量 ----------
        self.url_var   = tk.StringVar()
        self.depth_var = tk.IntVar(value=10)
        self.store_var = tk.StringVar(value="downloads")
        self.white_var = tk.StringVar(value="")          # 白名单
        self.black_var = tk.StringVar(value="")          # 黑名单
        self.workers_var = tk.IntVar(value=5)
        self.running   = False
        self.crawler   = None
        self._build_ui()

        # Treeview 行管理器
        self.row_mgr = RowManager(self.tree)

    # ---------- UI ----------
    def _build_ui(self):
        # ---- 参数区 ----
        frm = ttk.LabelFrame(self, text="下载设置")
        frm.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="下载链接:").grid(row=0, column=0, sticky="e", padx=(0, 4))
        ttk.Entry(frm, textvariable=self.url_var).grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(frm, text="保存目录:").grid(row=1, column=0, sticky="e", padx=(0, 4))
        dir_frm = ttk.Frame(frm); dir_frm.grid(row=1, column=1, sticky="ew", pady=2)
        dir_frm.columnconfigure(0, weight=1)
        ttk.Entry(dir_frm, textvariable=self.store_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dir_frm, text="浏览", command=self._browse, width=6).grid(row=0, column=1, padx=(4, 0))

        # 白名单 & 深度
        r1 = ttk.Frame(frm); r1.grid(row=2, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(r1, text="下载文件:").pack(side="left", padx=(0, 4))
        ttk.Entry(r1, textvariable=self.white_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(r1, text="深度:").pack(side="left", padx=(0, 4))
        ttk.Spinbox(r1, from_=0, to=20, textvariable=self.depth_var, width=4).pack(side="left")

        # 黑名单 & 并发
        r2 = ttk.Frame(frm); r2.grid(row=3, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(r2, text="排除文件:").pack(side="left", padx=(0, 4))
        ttk.Entry(r2, textvariable=self.black_var).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(r2, text="并发:").pack(side="left", padx=(0, 4))
        ttk.Spinbox(r2, from_=1, to=20, textvariable=self.workers_var, width=4).pack(side="left")

        # ---- 控制按钮 ----
        btn_frm = ttk.Frame(self)
        btn_frm.grid(row=1, column=0, columnspan=2, pady=4)
        self.start_btn = ttk.Button(btn_frm, text="开始下载", command=self._start, bootstyle="success")
        self.start_btn.pack(side="left", padx=4)
        self.stop_btn  = ttk.Button(btn_frm, text="停止下载", command=self._stop,  state="disabled", bootstyle="danger")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(btn_frm, text="打开目录", command=self._open_dir, bootstyle="info-outline").pack(side="left", padx=4)
        ttk.Button(btn_frm, text="导出日志", command=self._export_log, bootstyle="dark-outline").pack(side="left", padx=4)

        # ---- Treeview ----
        self.tree = ttk.Treeview(
            self, columns=("state", "name", "size", "prog", "speed", "eta"),
            show="headings", selectmode="browse", height=8
        )
        self.tree.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scroll.grid(row=2, column=1, sticky="ns", pady=(4, 0))
        self.tree.configure(yscrollcommand=scroll.set)

        # ---- 进度条 ----
        self.pbar = ttk.Progressbar(self, orient="horizontal", mode="determinate")
        self.pbar.grid(row=4, column=0, sticky="ew", pady=(4, 0))

        cols = [("state", "状态", 80), ("name", "文件名", 300),
                ("size", "大小(MB)", 80), ("prog", "进度", 70),
                ("speed", "下载速度", 100), ("eta", "预计/已用时间", 120)]
        for key, text, w in cols:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=w, anchor="center")

        # ---- 日志框 ----
        self.log = tk.Text(self, state="disabled", height=6)
        self.log.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        log_scroll = ttk.Scrollbar(self, orient="vertical", command=self.log.yview)
        log_scroll.grid(row=3, column=1, sticky="ns", pady=(4, 0))
        self.log.configure(yscrollcommand=log_scroll.set)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.rowconfigure(3, weight=0)

    # ---------- 工具 ----------
    def _log(self, txt: str):
        self.log.configure(state="normal")
        self.log.insert("end", txt + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _browse(self):
        path = filedialog.askdirectory()
        if path:
            self.store_var.set(path)

    def _open_dir(self):
        path = Path(self.store_var.get())
        if path.is_dir():
            os.startfile(path) if sys.platform == "win32" else os.system(f'xdg-open "{path}"')
        else:
            messagebox.showwarning("提示", "目录不存在！")

    def _export_log(self):
        log_text = self.log.get("1.0", "end-1c")
        if not log_text.strip():
            messagebox.showinfo("提示", "暂无日志可导出")
            return
        f = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("全部文件", "*.*")],
            initialfile=f"batchdownload_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
        )
        if f:
            Path(f).write_text(log_text, encoding="utf-8")
            messagebox.showinfo("成功", f"已导出日志：\n{f}")

    def _set_running(self, flag: bool):
        self.running = flag
        st = "disabled" if flag else "normal"
        self.start_btn.config(state=st)
        self.stop_btn.config(state="normal" if flag else "disabled")

    # ---------- 业务逻辑 ----------
    async def _run_crawler(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("提示", "请先填写 URL")
            return
        store = Path(self.store_var.get())
        store.mkdir(parents=True, exist_ok=True)

        # 解析白/黑名单
        white_raw = self.white_var.get().strip()
        white = {e.strip().lower() for e in white_raw.split(",") if e.strip()} if white_raw else set()
        black_raw = self.black_var.get().strip()
        black = {e.strip().lower() for e in black_raw.split(",") if e.strip()} if black_raw else set()
        depth   = self.depth_var.get()
        workers = self.workers_var.get()

        self._log("开始扫描...")
        self._set_running(True)
        self.row_mgr.clear()

        crawler = BatchDownload(
            url=url, depth=depth, store_dir=str(store),
            white=white, black=black, download_html=False
        )
        self.crawler = crawler

        try:
            links = await crawler.fetch()          # 返回 List[Dict{url,name,size}]
            total = len(links)
            if total == 0:
                self._log("未找到文件"); return

            # 日志：待下载
            self._log(f"\n===== 待下载文件（{total}个） =====")
            for item in links:
                self._log(f"  {item['name']}  {fmt_size(item['size'])} MB")
            # 日志：被排除
            if crawler.excluded:
                self._log(f"\n===== 被排除文件/目录（{len(crawler.excluded)}个） =====")
                for x in crawler.excluded:
                    self._log(f"  排除: {x}")

            # 插入 Treeview
            for item in links:
                self.row_mgr.add(item["url"], item["name"], item["size"])

            self.pbar.configure(maximum=total, value=0)
            counter = {"done": 0}

            # 为 crawler 注入实时回调
            original_dl = crawler._dl_one

            async def wrapped_dl(session:aiohttp.ClientSession, url:str, save_path:pathlib.Path, total_bytes:int):
                t0 = time.time()
                self.after(0, lambda: self.row_mgr.update(url, state="下载中"))
                # 调用真实下载
                await original_dl(session, url, save_path, total_bytes)
                cost = time.time() - t0
                counter["done"] += 1
                self.after(0, lambda: self.pbar.configure(value=counter["done"]))
                self.after(0, lambda: self.row_mgr.set_done(url, cost))
                self.after(0, lambda: self._log(
                    f"[完成] {save_path.name}  {fmt_size(total_bytes)} MB  用时 {fmt_time(cost)}"
                ))

            crawler._dl_one = wrapped_dl
            await crawler.download(max_workers=workers)
            self._log("全部完成！")
        except Exception as e:
            self._log(f"错误: {e}")
            traceback.print_exc()
        finally:
            self._set_running(False)
            self.crawler = None

    # 开始/停止 与之前相同
    def _start(self):
        if self.running: return
        self._set_running(True)
        self.start_btn.config(text="安装中…", state="disabled", bootstyle="secondary")
        def install_then_run():
            try:
                self._log("开始安装 浏览器组件，请耐心等待...")
                subprocess.run(["playwright","install","chromium"], check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            except subprocess.CalledProcessError as e:
                self.after(0, lambda: messagebox.showerror("安装失败", e.stdout))
                self.after(0, self._set_running, False); return
            self.after(0, lambda: AsyncRunner(self._run_crawler()).start())
        threading.Thread(target=install_then_run, daemon=True).start()

    def _stop(self):
        if self.crawler:
            self.crawler.stop()
            self._log("已请求停止...")
            self.row_mgr.clear()
            self.pbar.configure(value=0)
        self._set_running(False)


# ---------------- main ----------------
if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except: pass
    root = tk.Tk()
    App(root)
    root.mainloop()