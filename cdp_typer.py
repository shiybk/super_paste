# -*- coding: utf-8 -*-
"""
cdp_typer.py —— CDP 独立录入工具（GUI，支持中文）

核心思路：通过 Chrome DevTools Protocol（CDP）直接连接到你已用调试端口启动的
浏览器，使用 Input.insertText 把文本写入网页输入框。

与"模拟键盘"方案的本质区别：
  - 不经过系统键盘队列、不依赖前台焦点；
  - 窗口被切换、最小化、甚至你同时移动鼠标/敲击键盘，都不会打断录入；
  - 中/英文均原生支持。

前置条件（只做一次）：
  先完全关闭 Chrome/Edge，再用带调试端口的命令启动（端口此处用 9222）：
    Chrome : chrome.exe --remote-debugging-port=9222
    Edge   : msedge.exe --remote-debugging-port=9222
  启动后正常登录、打开目标网页即可（保留你的登录态）。

使用流程：
  1. 在目标网页里点击要填写的输入框（让该框成为页面 activeElement）；
  2. 本程序点[连接浏览器] → 在标签页下拉框选中目标网页；
  3. 点[检查焦点]确认当前焦点是输入框；
  4. 粘贴内容 → 点[开始录入]，文字即写入，全程不受窗口/外设影响。

技术栈：tkinter + playwright（connect_over_cdp）。
"""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from playwright.sync_api import sync_playwright


# 读取页面当前焦点元素（activeElement）的信息
JS_GET_ACTIVE = """
() => {
  const el = document.activeElement;
  if (!el) return { editable: false, tag: 'BODY' };
  const editable = el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
                   || el.isContentEditable;
  return {
    tag: el.tagName || '',
    type: el.type || '',
    name: el.name || '',
    id: el.id || '',
    placeholder: el.placeholder || '',
    isContentEditable: !!el.isContentEditable,
    editable: editable
  };
}
"""

# 让当前焦点元素保持/获取焦点
JS_FOCUS_ACTIVE = """
() => {
  const el = document.activeElement;
  if (el && el.focus) el.focus();
}
"""


class CdpExecutor:
    """专用工作线程：所有 Playwright 操作都在此线程串行执行（Playwright 非线程安全）"""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        """循环取任务执行，回传结果/异常"""
        while True:
            fn, args, holder, event = self._queue.get()
            try:
                holder["value"] = fn(*args)
            except Exception as e:  # noqa: BLE001
                holder["error"] = e
            finally:
                event.set()

    def call(self, fn, *args, timeout: int = 60):
        """提交任务到工作线程并阻塞等待结果"""
        holder: dict = {}
        event = threading.Event()
        self._queue.put((fn, args, holder, event))
        if not event.wait(timeout):
            raise TimeoutError("CDP 操作超时")
        if "error" in holder:
            raise holder["error"]
        return holder["value"]


class CdpTyperApp:
    """CDP 独立录入工具主界面"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CDP 独立录入工具（不受窗口切换/外设影响）")
        self.root.geometry("640x680")
        self.root.resizable(False, False)

        # Playwright 对象（仅在 CDP 工作线程中访问）
        self._pw = None
        self._browser = None
        self.pages: list = []
        self.executor = CdpExecutor()

        self._build_ui()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self):
        # 连接区
        conn = ttk.LabelFrame(self.root, text=" 1. 连接浏览器（调试模式） ")
        conn.pack(fill="x", padx=12, pady=8)

        help_txt = ("先用命令启动浏览器：chrome.exe --remote-debugging-port=9222"
                    "（Edge 用 msedge.exe），需先完全关闭浏览器")
        ttk.Label(conn, text=help_txt, foreground="#a05000",
                  wraplength=600).pack(anchor="w", padx=8, pady=(6, 2))

        row = ttk.Frame(conn)
        row.pack(fill="x", padx=8, pady=4)
        ttk.Label(row, text="调试端口:").pack(side="left")
        self.var_port = tk.StringVar(value="9222")
        ttk.Entry(row, textvariable=self.var_port, width=8).pack(side="left", padx=(4, 10))
        ttk.Button(row, text="连接浏览器",
                   command=self.connect).pack(side="left")

        # 标签页选择
        tab = ttk.LabelFrame(self.root, text=" 2. 选择目标标签页并检查焦点 ")
        tab.pack(fill="x", padx=12, pady=8)

        r2 = ttk.Frame(tab)
        r2.pack(fill="x", padx=8, pady=4)
        self.var_page = tk.StringVar()
        self.combo = ttk.Combobox(r2, textvariable=self.var_page,
                                  state="readonly", width=52)
        self.combo.pack(side="left", fill="x", expand=True)
        ttk.Button(r2, text="刷新", width=6,
                   command=self.refresh_pages).pack(side="left", padx=(6, 0))

        r3 = ttk.Frame(tab)
        r3.pack(fill="x", padx=8, pady=4)
        ttk.Button(r3, text="检查焦点",
                   command=self.check_focus).pack(side="left")
        self.var_focus = tk.StringVar(value="尚未检查")
        ttk.Label(r3, textvariable=self.var_focus,
                  foreground="#0060a0").pack(side="left", padx=(10, 0))

        # 内容区
        content = ttk.LabelFrame(self.root, text=" 3. 要录入的内容（中英文均可） ")
        content.pack(fill="both", expand=True, padx=12, pady=8)
        self.text = tk.Text(content, height=11, font=("Consolas", 11),
                            wrap="char", undo=True)
        sc = ttk.Scrollbar(content, command=self.text.yview)
        self.text.configure(yscrollcommand=sc.set)
        self.text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        sc.pack(side="right", fill="y")

        # 操作区
        op = ttk.Frame(self.root)
        op.pack(fill="x", padx=12, pady=4)
        ttk.Button(op, text="开始录入",
                   command=self.start_type).pack(side="left")
        ttk.Button(op, text="清空内容",
                   command=lambda: self.text.delete("1.0", "end")).pack(side="left", padx=8)

        # 状态栏
        self.status = tk.StringVar(value="未连接")
        ttk.Label(self.root, textvariable=self.status,
                  relief="sunken", anchor="w").pack(fill="x", padx=12, pady=(4, 8))

    def _set_status(self, msg):
        self.status.set(msg)
        self.root.update_idletasks()

    # ------------------------------------------------------------------
    # 通用：在临时线程中提交任务到 CDP 工作线程，结果回到主线程更新 UI
    # ------------------------------------------------------------------
    def _run_cdp(self, busy_msg, work, on_success, *args):
        self._set_status(busy_msg)

        def wrapper():
            try:
                result = self.executor.call(work, *args)
                self.root.after(0, on_success, result)
            except Exception as e:  # noqa: BLE001
                self.root.after(0, self._on_error, str(e))

        threading.Thread(target=wrapper, daemon=True).start()

    def _on_error(self, err: str):
        self._set_status(f"失败：{err}")
        messagebox.showerror("错误", err)

    # ------------------------------------------------------------------
    # CDP 工作方法（在工作线程中执行）
    # ------------------------------------------------------------------
    def _do_connect(self, port: int):
        """连接到调试端口浏览器，收集所有页面，返回 [(标题, 网址)]"""
        if self._pw is None:
            self._pw = sync_playwright().start()
        endpoint = f"http://127.0.0.1:{port}"
        self._browser = self._pw.chromium.connect_over_cdp(endpoint)
        self._collect_pages()
        return self._page_labels()

    def _collect_pages(self):
        """收集所有 context 的所有 page"""
        self.pages = []
        for ctx in self._browser.contexts:
            for pg in ctx.pages:
                self.pages.append(pg)

    def _page_labels(self):
        labels = []
        for pg in self.pages:
            try:
                title = pg.title() or "(无标题)"
            except Exception:  # noqa: BLE001
                title = "(无法读取)"
            labels.append(f"{title}  —  {pg.url}")
        return labels

    def _do_refresh(self):
        self._collect_pages()
        return self._page_labels()

    def _selected_page(self):
        idx = self.combo.current()
        if idx < 0 or idx >= len(self.pages):
            raise RuntimeError("请先在下拉框选择目标标签页")
        return self.pages[idx]

    def _do_check_focus(self, idx: int):
        """返回选中标签页的 activeElement 信息"""
        pg = self.pages[idx]
        return pg.evaluate(JS_GET_ACTIVE)

    def _do_type(self, idx: int, content: str):
        """聚焦后通过 Input.insertText 写入内容"""
        pg = self.pages[idx]
        info = pg.evaluate(JS_GET_ACTIVE)
        if not info.get("editable"):
            raise RuntimeError(
                "当前页面焦点不是可编辑输入框（请先在网页里点击目标输入框，再录入）")
        pg.evaluate(JS_FOCUS_ACTIVE)
        cdp = pg.context.new_cdp_session(pg)
        cdp.send("Input.insertText", {"text": content})
        return True

    # ------------------------------------------------------------------
    # 按钮回调
    # ------------------------------------------------------------------
    def connect(self):
        try:
            port = int(self.var_port.get())
        except ValueError:
            messagebox.showerror("错误", "端口请输入数字")
            return
        self._run_cdp("正在连接浏览器…", self._do_connect,
                      self._on_pages, port)

    def refresh_pages(self):
        if self._browser is None:
            messagebox.showwarning("提示", "请先连接浏览器")
            return
        self._run_cdp("正在刷新标签页…", self._do_refresh, self._on_pages)

    def _on_pages(self, labels):
        self.combo["values"] = labels
        if labels:
            self.combo.current(0)
            self._set_status(f"已连接，共 {len(labels)} 个标签页")
        else:
            self._set_status("已连接，但未发现任何标签页")

    def check_focus(self):
        idx = self.combo.current()
        if idx < 0:
            messagebox.showwarning("提示", "请先选择目标标签页")
            return
        self._run_cdp("正在检查焦点…", self._do_check_focus,
                      self._on_focus, idx)

    def _on_focus(self, info):
        if info.get("editable"):
            detail = info.get("tag", "")
            if info.get("type"):
                detail += f"[type={info['type']}]"
            if info.get("placeholder"):
                detail += f" 提示:{info['placeholder']}"
            self.var_focus.set(f"焦点在输入框：{detail}")
            self._set_status("焦点正常，可以开始录入")
        else:
            self.var_focus.set("焦点不在输入框，请先在网页点击输入框")
            self._set_status("焦点异常")

    def start_type(self):
        idx = self.combo.current()
        if idx < 0:
            messagebox.showwarning("提示", "请先连接并选择目标标签页")
            return
        content = self.text.get("1.0", "end-1c")
        if not content:
            messagebox.showwarning("提示", "内容为空")
            return
        self._run_cdp("正在录入（不影响你做其他操作）…", self._do_type,
                      self._on_typed, idx, content)

    def _on_typed(self, _ok):
        self._set_status("录入完成")


def main():
    root = tk.Tk()
    CdpTyperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
