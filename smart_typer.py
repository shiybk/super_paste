# -*- coding: utf-8 -*-
"""
smart_typer.py —— 智能模拟录入工具（GUI 版，支持中文）

功能：针对"只能录入、不能粘贴"的网页，把你要复制的内容通过模拟真人敲键
的方式，逐字符录入到已经手动打开的网页输入框里。

支持内容：
  - 中文 / 英文 / 数字 / 符号混合
  - 普通字符走键盘扫描码；中文等非 ASCII 字符通过系统级 Unicode 键盘输入
    （SendInput + KEYEVENTF_UNICODE），它被系统识别为"键盘敲入"，不触发
    网页的 paste 事件，因此能绕过"禁止粘贴"，也不依赖中文输入法。
  - 换行 -> 回车键；制表符 -> Tab 键。

技术栈：
  - tkinter  : 图形界面（Python 内置）
  - pynput   : 虚拟键盘控制器（普通字符 + Unicode 字符）
  - pyautogui: 仅用于读取鼠标位置，实现"移到左上角紧急中止"
  - threading: 后台线程录入，避免界面卡死
  - random   : 随机间隔，模拟真人节奏

使用建议：
  - 现在所有字符统一走 Unicode 直投，**无需切换系统输入法**，中文/英文/
    全角符号混排都不会被输入法误判为拼音；
  - 若出现"程序显示成功但网页没收到字"，通常是脚本与浏览器权限级别不一致
    （如浏览器以管理员运行、脚本普通权限），请用相同权限运行。

安全机制：
  - 鼠标快速移到屏幕左上角，可立即中止录入
  - 界面提供"停止录入"按钮（后台线程随时可中断）
"""

import ctypes
import random
import threading
import time
from ctypes import wintypes

import pyautogui
import pyperclip
import tkinter as tk
from tkinter import messagebox, ttk

from pynput.keyboard import Controller, Key

# 虚拟键盘控制器（pynput，仅用于 Tab/回车/清空等物理键）
kb = Controller()


# ---------------------------------------------------------------------------
# 统一 Unicode 直投（核心修复）
#
# 之前的方案：ASCII 走物理扫描码、中文走 Unicode。两个问题：
#   1) 物理字母按键在中文输入法状态下会被 IME 当成拼音，打出汉字；
#   2) 扫描码通道与 Unicode 通道混用，大小写交叉时异步乱序。
# 现在改为：所有可打印字符（英文/数字/符号/中文/全角符号）统一用
# SendInput + KEYEVENTF_UNICODE 直接投递字符。
#   - 全程不产生物理按键事件 → IME 收不到，字母不会被当拼音；
#   - 单一通道 → 大小写交叉时序一致，不再乱序。
# ---------------------------------------------------------------------------
_INPUT_KEYBOARD = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT_UNION(ctypes.Union):
    # 必须包含全部三个成员，联合体大小才与系统 INPUT 一致（取最大 MOUSEINPUT）
    _fields_ = [("ki", _KEYBDINPUT),
                ("mi", _MOUSEINPUT),
                ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD),
                ("u", _INPUT_UNION)]


_sendinput = ctypes.windll.user32.SendInput


def send_unicode_char(ch: str) -> None:
    """用 KEYEVENTF_UNICODE 投递一个字符（BMP 外字符自动拆代理对）"""
    code = ord(ch)
    if code > 0xFFFF:
        n = code - 0x10000
        units = [0xD800 + (n >> 10), 0xDC00 + (n & 0x3FF)]
    else:
        units = [code]
    for unit in units:
        for flags in (0, _KEYEVENTF_KEYUP):
            inp = _INPUT(
                type=_INPUT_KEYBOARD,
                u=_INPUT_UNION(ki=_KEYBDINPUT(
                    wVk=0, wScan=unit,
                    dwFlags=_KEYEVENTF_UNICODE | flags,
                    time=0, dwExtraInfo=0)))
            _sendinput(1, ctypes.byref(inp), ctypes.sizeof(inp))

# 左上角紧急中止的判定区域（像素）
FAILSAFE_ZONE = 6


class SmartTyperApp:
    """模拟录入工具的主界面"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("智能模拟录入工具（支持中文）")
        self.root.geometry("640x580")
        self.root.resizable(False, False)

        # 后台录入线程控制
        self.typing_thread: threading.Thread | None = None
        self.stop_event = threading.Event()   # 置位 = 请求停止
        self.typing = False                   # 是否正在录入

        # 录入参数
        self.base = 30
        self.jitter = 40
        self.line_pause = 250
        self.countdown = 3

        self._build_ui()

    # ------------------------------------------------------------------
    # 界面搭建（模块化：每个区域一个方法）
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        tip = ("用法：把内容粘贴到下方 → 调整参数 → 点[开始录入] → "
               "在倒计时内点击网页输入框 → 程序自动逐字敲入（支持中文）")
        ttk.Label(self.root, text=tip, foreground="#555",
                  wraplength=600).pack(pady=(10, 2))

        # 内容编辑区
        frame_text = ttk.LabelFrame(self.root, text=" 要录入的内容（中英文均可） ")
        frame_text.pack(fill="both", expand=True, padx=12, pady=6)
        self.text = tk.Text(frame_text, height=14, font=("Consolas", 11),
                            wrap="char", undo=True)
        scroll = ttk.Scrollbar(frame_text, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        scroll.pack(side="right", fill="y")

        # 参数区
        frame_opt = ttk.LabelFrame(self.root, text=" 录入参数 ")
        frame_opt.pack(fill="x", padx=12, pady=6)

        # 第一行：速度与抖动
        row1 = ttk.Frame(frame_opt)
        row1.pack(fill="x", padx=8, pady=4)
        ttk.Label(row1, text="基础速度(毫秒/字符):").pack(side="left")
        self.var_base = tk.StringVar(value="30")
        ttk.Entry(row1, textvariable=self.var_base, width=7).pack(side="left", padx=(4, 16))
        ttk.Label(row1, text="随机抖动(毫秒, 0-100):").pack(side="left")
        self.var_jitter = tk.StringVar(value="40")
        ttk.Entry(row1, textvariable=self.var_jitter, width=7).pack(side="left", padx=(4, 0))

        # 第二行：行停顿、倒计时、清空选项
        row2 = ttk.Frame(frame_opt)
        row2.pack(fill="x", padx=8, pady=4)
        ttk.Label(row2, text="每行结束停顿(毫秒):").pack(side="left")
        self.var_line_pause = tk.StringVar(value="250")
        ttk.Entry(row2, textvariable=self.var_line_pause, width=7).pack(side="left", padx=(4, 16))
        ttk.Label(row2, text="倒计时(秒):").pack(side="left")
        self.var_countdown = tk.StringVar(value="3")
        ttk.Entry(row2, textvariable=self.var_countdown, width=5).pack(side="left", padx=(4, 16))
        self.var_clear = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="录入前清空目标框",
                        variable=self.var_clear).pack(side="left")

        # 按钮区
        frame_btn = ttk.Frame(self.root)
        frame_btn.pack(fill="x", padx=12, pady=8)
        ttk.Button(frame_btn, text="开始录入",
                   command=self.start_typing).pack(side="left", padx=(0, 6))
        ttk.Button(frame_btn, text="复制当前内容",
                   command=self.copy_content).pack(side="left", padx=6)
        ttk.Button(frame_btn, text="停止录入",
                   command=self.stop_typing).pack(side="left", padx=6)
        ttk.Button(frame_btn, text="清空内容",
                   command=self.clear_content).pack(side="right")

        # 状态栏
        self.status = tk.StringVar(value="就绪（建议先把系统输入法切为英文）")
        ttk.Label(self.root, textvariable=self.status,
                  relief="sunken", anchor="w").pack(fill="x", padx=12, pady=(0, 8))

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    def copy_content(self) -> None:
        """把编辑区内容复制到剪贴板，方便先备份"""
        content = self.text.get("1.0", "end-1c")
        pyperclip.copy(content)
        self._set_status(f"已复制 {len(content)} 个字符到剪贴板")

    def clear_content(self) -> None:
        """清空编辑区"""
        self.text.delete("1.0", "end")
        self._set_status("已清空编辑区")

    def _set_status(self, msg: str) -> None:
        """更新状态栏文本"""
        self.status.set(msg)
        self.root.update_idletasks()

    # ------------------------------------------------------------------
    # 录入流程
    # ------------------------------------------------------------------
    def start_typing(self) -> None:
        """点击[开始录入]：先倒计时，倒计时结束后进入后台录入线程"""
        if self.typing:
            self._set_status("正在录入中，请勿重复点击")
            return

        content = self.text.get("1.0", "end-1c")
        if not content.strip():
            messagebox.showwarning("提示", "内容为空，请先粘贴要录入的内容")
            return

        # 校验参数
        try:
            self.base = max(0, int(self.var_base.get()))
            self.jitter = min(100, max(0, int(self.var_jitter.get())))
            self.line_pause = max(0, int(self.var_line_pause.get()))
            self.countdown = max(1, min(30, int(self.var_countdown.get())))
        except ValueError:
            messagebox.showerror("参数错误", "请输入有效的整数参数")
            return

        self.stop_event.clear()
        self._countdown(self.countdown)

    def _countdown(self, remain: int) -> None:
        """递归倒计时，结束后启动录入"""
        if remain > 0:
            self._set_status(f"倒计时 {remain} 秒，请点击网页输入框定位光标…")
            self.root.after(1000, self._countdown, remain - 1)
        else:
            self._set_status("开始录入…（鼠标移到屏幕左上角可紧急中止）")
            self._launch_typing()

    def _launch_typing(self) -> None:
        """最小化界面，启动后台录入线程"""
        self.typing = True
        self.root.iconify()  # 最小化窗口，避免遮挡网页输入框

        self.typing_thread = threading.Thread(
            target=self._type_worker, daemon=True)
        self.typing_thread.start()

    # ------------------------------------------------------------------
    # 键盘动作（封装为方法，便于测试与维护）
    # ------------------------------------------------------------------
    def _tap_char(self, ch: str) -> None:
        """录入单个字符。

        通道策略：
          - 制表符 -> 物理 Tab 键；
          - 其余可打印字符（英文/数字/符号/中文/全角符号）统一走
            KEYEVENTF_UNICODE 直投（send_unicode_char）。
            这样全程不产生物理字母按键，中文输入法 IME 收不到、
            不会把字母当拼音；同时所有字符同一通道，大小写交叉不乱序。
        """
        if ch == "\t":
            kb.tap(Key.tab)
            return

        if ch.isprintable():
            send_unicode_char(ch)
        # 其他不可见控制字符直接忽略

    def _tap_enter(self) -> None:
        """按回车键"""
        kb.tap(Key.enter)

    def _clear_target(self) -> None:
        """清空目标输入框：Ctrl+A 全选后删除"""
        kb.press(Key.ctrl)
        kb.tap("a")
        kb.release(Key.ctrl)
        kb.tap(Key.delete)

    def _failcheck(self) -> bool:
        """左上角紧急中止检测：鼠标进入左上角区域则请求停止"""
        try:
            x, y = pyautogui.position()
            if x < FAILSAFE_ZONE and y < FAILSAFE_ZONE:
                self.stop_event.set()
                return True
        except Exception:
            # 取不到鼠标位置时不应影响录入
            pass
        return False

    # ------------------------------------------------------------------
    # 后台录入线程
    # ------------------------------------------------------------------
    def _type_worker(self) -> None:
        """逐字符模拟录入（含真人节奏抖动）"""
        content = self.text.get("1.0", "end-1c")
        lines = content.split("\n")

        try:
            # 可选：录入前清空目标框
            if self.var_clear.get():
                if self._failcheck():
                    self._done_typing("已紧急中止")
                    return
                self._clear_target()
                time.sleep(0.15)

            for idx, line in enumerate(lines):
                if self.stop_event.is_set():
                    self._done_typing("已手动停止")
                    return

                # 逐字符录入
                for ch in line:
                    if self._failcheck():
                        self._done_typing("已紧急中止")
                        return
                    if self.stop_event.is_set():
                        self._done_typing("已手动停止")
                        return
                    self._tap_char(ch)
                    # 真人节奏：基础速度 + 随机抖动
                    time.sleep((self.base + random.uniform(0, self.jitter)) / 1000.0)

                # 行间：非最后一行则按回车 + 停顿
                if idx < len(lines) - 1:
                    self._tap_enter()
                    time.sleep(self.line_pause / 1000.0)

            self._done_typing("录入完成")
        except Exception as e:
            self._done_typing(f"录入出错：{e}")

    def _done_typing(self, msg: str) -> None:
        """录入结束：恢复窗口、更新状态（切回主线程更新 UI）"""
        self.typing = False
        self.root.after(0, self._finish_ui, msg)

    def _finish_ui(self, msg: str) -> None:
        self.root.deiconify()
        self.root.lift()
        self._set_status(msg)

    # ------------------------------------------------------------------
    # 停止
    # ------------------------------------------------------------------
    def stop_typing(self) -> None:
        """点击[停止录入]：置位停止事件"""
        if self.typing:
            self.stop_event.set()
            self._set_status("正在停止…")
        else:
            self._set_status("当前没有正在进行的录入")


def main() -> None:
    root = tk.Tk()
    SmartTyperApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
