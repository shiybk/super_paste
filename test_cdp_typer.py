# -*- coding: utf-8 -*-
"""
test_cdp_typer.py —— cdp_typer 核心逻辑的独立验证（无需真实浏览器）

验证：
  - CdpExecutor 工作线程的结果回传与异常回传；
  - _do_type 在焦点可编辑时正确调用 Input.insertText；
  - _do_type 在焦点不可编辑时正确抛出错误。

运行: python test_cdp_typer.py
"""
import sys
import os
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp_typer


class FakeCdp:
    """记录 CDP 命令"""
    def __init__(self):
        self.sent = []

    def send(self, method, params):
        self.sent.append((method, params))


class FakePage:
    """模拟 Playwright Page：按 JS 返回不同结果"""
    def __init__(self, editable=True):
        self.cdp = FakeCdp()
        self._editable = editable
        self.context = types.SimpleNamespace(
            new_cdp_session=lambda pg: self.cdp)

    def evaluate(self, js, *args):
        if cdp_typer.JS_GET_ACTIVE in js:
            return {
                "tag": "TEXTAREA" if self._editable else "BODY",
                "type": "",
                "editable": self._editable,
            }
        return None


def make_app():
    """绕过 GUI 构造，仅创建实例并挂一个执行器"""
    app = cdp_typer.CdpTyperApp.__new__(cdp_typer.CdpTyperApp)
    app.executor = cdp_typer.CdpExecutor()
    return app


def main():
    # 用例1：执行器回传正常结果
    ex = cdp_typer.CdpExecutor()
    assert ex.call(lambda x: x + 1, 41) == 42, "用例1 结果回传错误"
    print("用例1 通过：执行器正常回传结果")

    # 用例2：执行器回传异常
    def boom():
        raise ValueError("x")
    try:
        ex.call(boom)
        raise AssertionError("用例2 应抛异常")
    except ValueError:
        print("用例2 通过：执行器正确回传异常")

    # 用例3：焦点可编辑 -> 正确发送 insertText（含中文）
    app = make_app()
    page = FakePage(editable=True)
    app.pages = [page]
    text = "中文Hello\n第二行"
    ok = app.executor.call(app._do_type, 0, text)
    assert ok is True, "用例3 应返回 True"
    assert len(page.cdp.sent) == 1, f"用例3 命令数错误: {len(page.cdp.sent)}"
    method, params = page.cdp.sent[0]
    assert method == "Input.insertText", f"用例3 方法错误: {method}"
    assert params["text"] == text, f"用例3 文本错误: {params['text']}"
    print("用例3 通过：可编辑焦点下正确发送 insertText（含换行与中文）")

    # 用例4：焦点不可编辑 -> 抛出错误，不发送 insertText
    app = make_app()
    page = FakePage(editable=False)
    app.pages = [page]
    try:
        app.executor.call(app._do_type, 0, "abc")
        raise AssertionError("用例4 应抛错")
    except RuntimeError as e:
        assert "不是可编辑输入框" in str(e)
        assert len(page.cdp.sent) == 0, "用例4 不应发送任何命令"
        print("用例4 通过：非可编辑焦点时正确报错且不写入")

    print("\n全部 4 个 CDP 逻辑用例通过 ✅")


if __name__ == "__main__":
    main()
