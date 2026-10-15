# -*- coding: utf-8 -*-
"""
test_smart_typer.py —— smart_typer 核心录入逻辑验证（全 Unicode 直投版）

验证：
  - 所有可打印字符（英文/数字/符号/中文/全角符号）统一经 send_unicode_char
    逐字符直投，序列与原文一致（含大小写、全角括号、乘号）；
  - 全程不产生物理字母按键（IME 收不到，字母不会被当拼音）；
  - Tab/回车/清空/停止/左上角中止仍正确。

运行: python test_smart_typer.py
"""
import threading
import types
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smart_typer
from pynput.keyboard import Key, KeyCode


def norm(key):
    if isinstance(key, KeyCode):
        return ("vk", key.vk)
    if isinstance(key, Key):
        return ("key", key.name)
    return ("raw", key)


class FakeKb:
    """记录物理键（Tab/回车/ctrl/del），不模拟字母"""
    def __init__(self):
        self.calls = []

    def tap(self, key):
        self.calls.append(("tap", norm(key)))

    def press(self, key):
        self.calls.append(("press", norm(key)))

    def release(self, key):
        self.calls.append(("release", norm(key)))


class FakeText:
    def __init__(self, content):
        self._c = content

    def get(self, *args):
        return self._c


class FakeApp(smart_typer.SmartTyperApp):
    def __init__(self, content, clear=True):
        self.text = FakeText(content)
        self.var_clear = types.SimpleNamespace(get=lambda: clear)
        self.stop_event = threading.Event()
        self.base = 0
        self.jitter = 0
        self.line_pause = 0
        self.done_msg = None

    def _done_typing(self, msg):
        self.done_msg = msg


def no_sleep(seconds):
    return None


def run_case(content, clear=True, stop_before=False, mouse=(500, 500)):
    app = FakeApp(content, clear)
    stub_kb = FakeKb()
    unicode_chars = []
    orig_kb = smart_typer.kb
    orig_su = smart_typer.send_unicode_char
    orig_pa = smart_typer.pyautogui
    orig_time = smart_typer.time
    smart_typer.kb = stub_kb
    smart_typer.send_unicode_char = lambda ch: unicode_chars.append(ch)
    smart_typer.pyautogui = types.SimpleNamespace(position=lambda: mouse)
    smart_typer.time = types.SimpleNamespace(
        sleep=no_sleep, uniform=lambda a, b: 0.0)
    if stop_before:
        app.stop_event.set()
    try:
        app._type_worker()
    finally:
        smart_typer.kb = orig_kb
        smart_typer.send_unicode_char = orig_su
        smart_typer.pyautogui = orig_pa
        smart_typer.time = orig_time
    return app, stub_kb, unicode_chars


def main():
    # 用例1：用户报错文本片段——含全角符号、大小写交叉、中文
    content = "dB（分贝）× 10\nlg"
    app, kb, chars = run_case(content, clear=False)
    assert app.done_msg == "录入完成"
    # Unicode 直投序列应与原文逐字一致（去掉 \n，因为换行走物理回车）
    assert "".join(chars) == "dB（分贝）× 10lg", f"序列错乱: {''.join(chars)}"
    # 全角括号、乘号也走 Unicode
    assert chars.count("（") == 1 and chars.count("）") == 1 and chars.count("×") == 1
    # 物理键只应有 1 次回车（两行之间）
    enters = [c for c in kb.calls if c == ("tap", ("key", "enter"))]
    assert len(enters) == 1, f"回车次数错: {kb.calls}"
    print("用例1 通过：全角符号/大小写/中文统一逐字直投，无拼音误判")

    # 用例2：完全没有物理字母按键（IME 不可能截获）
    app, kb, chars = run_case("aBcDxyz", clear=False)
    letter_phys = [c for c in kb.calls if c[0] in ("tap", "press", "release")
                   and isinstance(c[1], tuple) and c[1][0] == "vk"]
    assert letter_phys == [], f"不应有物理字母键: {kb.calls}"
    assert "".join(chars) == "aBcDxyz"
    print("用例2 通过：无物理字母按键，输入法无法把字母当拼音")

    # 用例3：清空 / Tab
    app, kb, chars = run_case("a\tb\nd", clear=True)
    assert ("press", ("key", "ctrl")) in kb.calls
    assert ("tap", ("raw", "a")) in kb.calls
    assert ("tap", ("key", "delete")) in kb.calls
    assert ("tap", ("key", "tab")) in kb.calls
    assert "".join(chars) == "abd"
    print("用例3 通过：清空/Tab 正常")

    # 用例4：停止即时生效（不得直投任何字符）
    app, kb, chars = run_case("abc", clear=True, stop_before=True)
    assert app.done_msg == "已手动停止"
    assert chars == [], f"用例4 不应录入: {chars}"
    print("用例4 通过：停止即时生效")

    # 用例5：左上角紧急中止
    app, kb, chars = run_case("abc", clear=False, mouse=(0, 0))
    assert app.done_msg == "已紧急中止"
    assert chars == []
    print("用例5 通过：左上角紧急中止")

    print("\n全部 5 个用例通过 ✅")


if __name__ == "__main__":
    main()
