# 智能模拟录入工具（Super Paste）

针对“只能录入、不能粘贴”的网页输入框，把剪贴板/编辑区里的内容通过**模拟真人敲键**逐字符录入，支持中文、英文、数字、符号混排。

项目包含两个独立工具，可分别运行：

| 工具 | 文件 | 方案 | 适用场景 |
| --- | --- | --- | --- |
| 智能模拟录入工具 | `smart_typer.py` | 系统级 Unicode 键盘直投（SendInput + KEYEVENTF_UNICODE） | 通用网页输入框，需要光标焦点在前台 |
| CDP 独立录入工具 | `cdp_typer.py` | Chrome DevTools Protocol（`Input.insertText`） | 需要后台录入、不受窗口切换/外设干扰 |

## 特性

- **中文原生支持**：所有可打印字符统一走 Unicode 直投，不经过物理按键队列，中文输入法 IME 无法截获，无需切换输入法，大小写交叉不乱序。
- **真人节奏**：随机间隔抖动模拟人类敲键速度。
- **安全机制**：鼠标快速移到屏幕左上角可立即中止录入，界面提供“停止录入”按钮，后台线程随时可中断。
- **CDP 后台录入**：不依赖前台焦点，窗口最小化或你同时操作鼠标键盘都不影响录入。

## 安装

需要 Python 3.10+（Windows）。

```bash
pip install -r requirements.txt
```

> CDP 工具首次使用还需安装浏览器驱动：`playwright install chromium`（仅连接本地已运行浏览器时也可跳过，见下方说明）。

## 使用

### 工具一：智能模拟录入工具（smart_typer.py）

```bash
python smart_typer.py
```

1. 把要录入的内容粘贴到编辑区；
2. 调整速度/抖动/倒计时等参数；
3. 点击「开始录入」，在倒计时内点击网页输入框定位光标；
4. 程序自动逐字敲入（鼠标移到屏幕左上角可紧急中止）。

提示：若程序显示成功但网页没收到字，通常是脚本与浏览器的权限级别不一致（如浏览器以管理员身份运行），请用相同权限运行。

### 工具二：CDP 独立录入工具（cdp_typer.py）

```bash
python cdp_typer.py
```

前置条件（只需做一次）：先完全关闭 Chrome/Edge，再用调试端口启动：

```
chrome.exe  --remote-debugging-port=9222   # Chrome
msedge.exe --remote-debugging-port=9222    # Edge
```

使用流程：

1. 在目标网页点击要填写的输入框（使其成为页面 activeElement）；
2. 本程序点「连接浏览器」→ 在下拉框选中目标标签页；
3. 点「检查焦点」确认当前焦点是输入框；
4. 粘贴内容 → 点「开始录入」，文字即写入，全程不受窗口/外设影响。

## 运行测试

```bash
python test_smart_typer.py   # 模拟录入逻辑（无需真实浏览器）
python test_cdp_typer.py     # CDP 执行器逻辑（无需真实浏览器）
```

## 打包为 exe（可选）

```bash
pip install pyinstaller
pyinstaller smart_typer.spec
```

产物位于 `dist/smart_typer.exe`。

## 依赖说明

- `smart_typer.py`：tkinter（内置）、pynput、pyautogui（仅读取鼠标位置）、pyperclip
- `cdp_typer.py`：tkinter（内置）、playwright
