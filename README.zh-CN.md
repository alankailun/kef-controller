# KEF Controller

## 项目说明

KEF Controller 是一个 Windows 托盘程序，用于控制支持 KEF W2 / KEF Connect 的音箱。

当前支持的型号：

- LS50 Wireless II
- LSX II
- LS60 Wireless

当前主要功能：

- 程序启动时唤醒音箱
- Windows 关机、注销或结束会话时让音箱待机
- Windows 锁屏时让音箱待机
- Windows 解锁时唤醒音箱
- Windows 睡眠时让音箱待机
- 音箱 IP 变化后，通过目标 MAC 恢复设备
- 从界面扫描局域网并选择目标音箱
- 以 Windows 托盘 + 主窗口方式运行，包含设置、日志和事件测试页面

## 项目结构

```text
kef_controller/
  main_gui.py                  GUI 入口：托盘程序 + 主窗口
  main_background.py           Headless 入口：只运行后台消息循环
  KEF Controller.spec          PyInstaller 打包配置
  requirements.txt
  installer/
    KEF_Controller.iss         Inno Setup 安装脚本
    assets/                    安装器和程序图标资源
  kef_app/
    config/                    AppConfig、SystemConfig 和用户设置
    storage/                   JSON 配置和音箱状态存储
    devices/                   音箱后端、型号处理和网络发现
    controller/                电源控制、唤醒/待机动作、身份恢复
    platform/
      windows/                 Win32 事件、会话/电源 API、自启动注册
        startup/               Registry Run 和 Task Scheduler 辅助逻辑
    runtime/                   启动引导、日志、后台消息循环
    ui/                        PySide6/QFluent 托盘程序、主窗口、设置、日志
      logs/                    UI 日志历史和 handler
      settings/                设置卡片、保存逻辑、自启动同步
  tests/                       config、自启动、事件逻辑相关单元测试
```

## 运行时文件

程序会把用户数据放在当前 Windows 用户目录下：

```text
%LocalAppData%\KEF Controller\
  config.json
  state.json
  logs\
    kef_controller.log
```

也就是说，程序安装位置和运行状态是分开的。用户设置、目标音箱身份、恢复后的 IP 状态和日志都不会写到 `.exe` 旁边。

## 自启动和安装路径

程序支持三种 Windows 自启动状态：

- Registry Run
- Task Scheduler
- Off

设置界面会显示当前实际生效的自启动方式，也可以清理或修复过期启动项。Task Scheduler 的修复或删除有时会需要管理员确认。

对于打包后的安装版，程序启动路径有一个固定的稳定位置：

```text
%LocalAppData%\Programs\KEF Controller\KEF Controller.exe
```

安装器仍然允许用户选择主安装目录。与此同时，安装器会把同一个 `.exe` 同步一份到上面的 LocalAppData 稳定路径。开始菜单快捷方式、可选桌面快捷方式、安装完成后的启动，以及 Windows 自启动项，都会指向这个稳定路径。这样无论用户把主安装目录选在 `C:`、`F:`、OneDrive 或其他位置，快捷方式和自启动都不依赖那个可变路径。

程序运行时，如果检测到自己是打包后的 `.exe`，也会在注册自启动前尝试把当前 `.exe` 同步到稳定路径。如果同步失败，会写日志并退回使用当前 `.exe`。

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

## 使用 PyInstaller 打包 EXE

推荐使用仓库里的 `.spec` 文件打包：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller "KEF Controller.spec"
```

主要输出文件：

```text
dist\KEF Controller.exe
```

当前 `.spec` 文件使用 `main_gui.py`，生成无控制台的单文件窗口程序，并包含 QFluentWidgets 资源和安装器图标。

也可以直接用命令打包：

```bat
.venv\Scripts\pyinstaller --noconsole --onefile --name "KEF Controller" --collect-all qfluentwidgets main_gui.py
```

## 使用 Inno Setup 生成安装包

先安装 Inno Setup。编译器通常在这里：

```text
C:\Program Files (x86)\Inno Setup 6\ISCC.exe
```

先确认 PyInstaller 输出已经存在：

```text
dist\KEF Controller.exe
```

编译安装包：

```bat
cd /d "path\to\kef_controller"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KEF_Controller.iss
```

预期输出安装包：

```text
output\KEF_Controller_Setup.exe
```

安装和卸载前，安装器会先关闭正在运行的 `KEF Controller.exe`，方便干净替换文件。

## 测试

运行单元测试：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python -m unittest discover -s tests
```

## 推荐发布流程

1. 激活 `.venv`。
2. 运行单元测试。
3. 用 `.spec` 文件生成 `dist\KEF Controller.exe`。
4. 手动测试生成的 `.exe`。
5. 编译 `installer\KEF_Controller.iss`。
6. 在 Windows 机器上测试安装包、快捷方式、自启动方式、关机/锁屏/睡眠行为和日志。

## 补充说明

- `main_gui.py` 是正常打包使用的入口。
- `main_background.py` 仍然保留，用于直接运行 Headless 后台逻辑。
- 当前不使用 `RegisterApplicationRestart`。程序自己处理 Windows 关机和会话事件，并且在 Restart Manager 的 `CLOSEAPP` 请求下快速退出，避免 Windows 把它记成 application hang。
- 新生成的未签名程序如果被 SmartScreen 或杀毒软件提示，测试阶段比较常见。
