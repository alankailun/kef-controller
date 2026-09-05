# KEF Controller

## 项目说明

KEF Controller 是一个 Windows 托盘应用，用于控制支持 KEF W2 / KEF
Connect 的音箱。它主要解决 Windows 电脑上的日常自动化问题：该唤醒时唤醒
音箱，该待机时让音箱待机，稳定记住所选音箱，并把电源事件、网络恢复和运行
状态写进本地日志。

当前支持的型号：

- LS50 Wireless II
- LSX II
- LS60 Wireless

这个项目面向 Windows。它使用 Win32 的会话、电源、关机、显示器、合盖、网卡
变化、自启动和托盘相关 API。

## 功能总览

### Home 页面

- 显示当前选中的音箱身份、型号、IP、电源状态和可用状态。
- 手动唤醒音箱。
- 手动让音箱待机。
- 切换音箱输入源。
- 调整音量，并用后台防抖提交，避免拖动滑块时频繁打请求。
- 刷新音箱状态，同时不阻塞界面。

### 托盘应用

- 常驻 Windows 系统托盘。
- 在托盘提示和菜单里显示当前音箱/状态。
- 打开主窗口。
- 从托盘菜单唤醒音箱。
- 从托盘菜单让音箱待机。
- 干净退出程序。

### 音箱选择

- 扫描局域网里的受支持 KEF 音箱。
- 扫描过程中一旦发现候选音箱，就立即显示到选择窗口。
- 关闭音箱选择窗口时，会协作式取消当前扫描。
- 优先探测上次已知 IP 和默认路由所在网段，减少在 VPN、Tailscale、虚拟网卡
  网段上的无用扫描。
- 支持手动输入 IP，并可选填 MAC 来验证音箱身份。
- 能验证手动目标是否是受支持 KEF 音箱。
- 当前网页表单要求填写 IP；仅 MAC 的目标验证仍保留在控制器接口中。

### 音箱电源行为

设置页面提供这些自动化开关：

- Wake Speaker When the App Starts：程序启动时唤醒音箱。
- Put Speaker in Standby When the Screen Turns Off：屏幕关闭时让音箱待机。
- Wake Speaker When Windows Unlocks：Windows 解锁时唤醒音箱。
- Put Speaker in Standby When Windows Locks：Windows 锁屏时让音箱待机。
- Put Speaker in Standby When Windows Sleeps：Windows 睡眠时让音箱待机。
- Put Speaker in Standby When Windows Shuts Down：Windows 关机/注销时让音箱待机。

屏幕关闭触发器监听的是 Windows 的 console display power-setting 通知。它不是
Windows 11 独占功能，但在 Windows 11 Modern Standby 机器上尤其有用，因为
屏幕关闭事件往往比传统 suspend 通知更早到达。

### Windows 电源和会话事件

- 监听 Windows 锁屏/解锁。
- 监听睡眠/恢复。
- 监听关机、注销、会话结束。
- 监听合盖事件。
- 监听屏幕亮/暗/关闭事件。
- 恢复后重启音箱事件轮询。
- 使用 generation 和 deadline 防止过期的唤醒/待机任务继续发送。
- 把时间敏感的网络动作派发到消息泵之外，避免 Windows 事件回调被慢网络 I/O
  卡住。

### 快速待机路径

- 运行时维护预热 socket。
- 在锁屏、屏幕关闭、合盖、睡眠、关机这些窗口里优先走预热待机快路。
- 预热快路不可用时，回退到有界的 fire-and-forget HTTP 待机发送。
- Windows 事件处理器里不做无界阻塞网络调用。
- 待机日志中的外层 action 和底层 socket send 使用同一个 reason，例如
  `DISPLAY_OFF`、`WTS_SESSION_LOCK`、`PBT_APMSUSPEND`，方便按触发器排查。

### 唤醒行为

- 程序启动时可自动唤醒音箱。
- 默认在恢复/睡眠后等待 Windows 解锁，再唤醒音箱。
- 解锁/恢复后会等待一小段时间，让本地网络先恢复。
- 唤醒失败时按有界延迟重试。
- 唤醒成功后切换到配置的默认输入源。

### IP 恢复和发现

- 把选中的音箱身份保存到 `state.json`。
- 使用当前 IP 前会先尝试验证。
- 如果音箱 IP 变化，可以根据保存的 KEF 身份/MAC 做恢复。
- 当本地路由明确不可用时，跳过昂贵的自动发现扫描。
- 手动扫描和自动恢复分离：扫描发现候选音箱不会直接改当前目标，只有用户选中
  后才保存。

### 日志和诊断

- 在 UI 里查看应用日志。
- 从 UI 重新加载日志。
- 从 UI 打开日志文件夹。
- 记录结构化的电源、会话、网卡、发现、唤醒、待机、自启动日志。
- 在音箱支持时记录 Wi-Fi 诊断信息。
- 运行状态和日志都放在当前 Windows 用户目录下。

### 事件测试

Event Tests 页面可以模拟这些事件：

- Startup
- Shutdown
- Lock
- Unlock
- Display Off
- Sleep

测试会尊重当前设置。如果某个行为已关闭，测试会跳过，并在 UI 里说明原因。

### Windows 自启动

- 支持 Registry Run 自启动。
- 支持 Task Scheduler 自启动。
- 支持关闭自启动。
- 设置页面显示当前实际生效的自启动状态。
- 可以在可行时修复过期自启动项。
- 快捷方式和自启动项使用稳定的安装启动路径。

Registry Run 是更简单的当前用户自启动方式。Task Scheduler 通常在登录后启动得
更快、更早；如果你希望 KEF Controller 尽快启动并尽快接管音箱的唤醒/待机
处理，Task Scheduler 更合适。

## 运行时文件

程序会把用户数据放在当前 Windows 用户目录下：

```text
%LocalAppData%\KEF Controller\
  config.json
  state.json
  logs\
    kef_controller.log
```

程序安装位置和运行状态是分开的。用户设置、目标音箱身份、恢复后的 IP 状态和
日志都不会写到 `.exe` 旁边。

## 安装和启动路径

安装版使用一个稳定的启动路径：

```text
%LocalAppData%\Programs\KEF Controller\KEF Controller.exe
```

Inno Setup 安装器仍然允许用户选择主安装目录。同时，安装器会把同一个 `.exe`
同步一份到上面的 LocalAppData 稳定路径。开始菜单快捷方式、可选桌面快捷方式、
安装完成后的启动，以及 Windows 自启动项，都会指向这个稳定路径。这样无论用户
把主安装目录放在 `C:`、`F:`、OneDrive 或其他位置，快捷方式和自启动都不依赖
那个可变路径。

程序运行时，如果检测到自己是打包后的 `.exe`，也会在注册自启动前尝试把当前
`.exe` 同步到稳定路径。如果同步失败，会写日志并退回使用当前 `.exe`。

## 项目结构

```text
kef_controller/
  main_gui.py                  GUI 入口：托盘应用 + 主窗口
  main_background.py           Headless 入口：只运行后台消息循环
  KEF Controller.spec          PyInstaller 打包配置
  requirements.txt
  installer/
    KEF_Controller.iss         Inno Setup 安装脚本
    assets/                    安装器和程序图标资源
  release_notes/               按版本整理的 release notes
  kef_app/
    config/                    AppConfig、SystemConfig 和用户设置
    storage/                   JSON 配置和音箱状态存储
    devices/                   KEF 后端、型号处理和网络发现
    controller/                唤醒/待机动作、事件处理、IP 恢复
    platform/
      windows/                 Win32 会话/电源 API 和自启动注册
        startup/               Registry Run 和 Task Scheduler 辅助逻辑
    runtime/                   启动引导、日志、后台消息循环
    ui/                        PySide6 托盘应用和本地 Web UI
      logs/                    UI 日志历史和 handlers
      settings/                设置卡片、保存逻辑、自启动同步
  tests/                       config、UI、自启动、事件逻辑相关单元测试
```

## 从源码运行

可以使用 Windows `cmd.exe` 或 PowerShell。

创建或更新虚拟环境，然后安装依赖：

```bat
cd /d "path\to\kef_controller"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

GUI 模式：

```bat
python main_gui.py
```

Headless 模式：

```bat
python main_background.py
```

## 一键打包（推荐）

在项目目录打开 PowerShell，运行：

```powershell
./build.ps1
```

脚本自动查找 Inno Setup，每次清空并复用 `build/`、`dist/`，生成并覆盖
`installer/output/KEF_Controller_Setup.exe`。不另建带版本号、`final` 或
`rebuild` 后缀的目录和安装包。版本号保存在程序属性及更新说明中。
日志固定放在 `build/pyinstaller.log` 和 `build/installer.log`。

若未找到编译器，可运行 `./build.ps1 -IsccPath 'F:\Inno Setup 6\ISCC.exe'`。
后续打包约定见 [AGENTS.md](AGENTS.md)。下面保留分步打包命令。

## 使用 PyInstaller 打包 EXE

推荐使用仓库里的 `.spec` 文件打包：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller "KEF Controller.spec"
```

主要输出文件：

```text
dist\KEF Controller\KEF Controller.exe
```

`.spec` 文件使用 `main_gui.py`，生成无控制台的 onedir 窗口程序，包含本地
HTML/CSS/JavaScript UI 和应用图标，保留 Modern Windows Qt style plugin 与软件
OpenGL fallback，并裁掉未使用的 Qt/PySide6 模块和插件以减小体积。

## 使用 Inno Setup 生成安装包

先安装 Inno Setup。编译器通常在这里：

```text
C:\Program Files (x86)\Inno Setup 6\ISCC.exe
```

先确认 PyInstaller 输出已经存在：

```text
dist\KEF Controller\KEF Controller.exe
```

编译安装包：

```bat
cd /d "path\to\kef_controller"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KEF_Controller.iss
```

预期输出安装包：

```text
installer\output\KEF_Controller_Setup.exe
```

安装和卸载前，安装器会先关闭正在运行的 `KEF Controller.exe`，方便干净替换文件。

## 测试

运行单元测试：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python -m unittest discover -s tests
```

运行字节码编译检查：

```bat
python -m compileall -q kef_app tests
```

## Release Notes

按版本整理的 release notes 放在：

```text
release_notes/
```

当前安装器版本定义在：

```text
installer\KEF_Controller.iss
```

## 推荐发布流程

1. 同步更新 `installer/KEF_Controller.iss` 和 `installer/version_info.txt` 里的版本号。
2. 更新 `release_notes/` 下对应版本的 release note。
3. 运行单元测试和 `compileall`。
4. 运行 `./build.ps1`，在上述固定路径生成程序和安装包。
5. 手动测试生成的 `.exe`。
6. 在 Windows 机器上测试安装包、快捷方式、自启动、关机/锁屏/睡眠/屏幕关闭行为和日志。
7. 提交、打 tag、推送。

## 补充说明

- `main_gui.py` 是正常打包使用的入口。
- `main_background.py` 仍然保留，用于直接运行 Headless 后台逻辑。
- 当前不使用 `RegisterApplicationRestart`。程序自己处理 Windows 关机和会话事件，
  并且在 Restart Manager 的 `CLOSEAPP` 请求下快速退出，避免 Windows 把它记成
  application hang。
- 新生成的未签名程序如果被 SmartScreen 或杀毒软件提示，测试阶段比较常见。
- 设置页面部分图标来自 Microsoft Fluent UI System Icons 的 SVG，原图标以 MIT
  license 分发。
