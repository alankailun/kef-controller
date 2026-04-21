# KEF Controller

## 项目说明

KEF Controller 是一个用于 Windows 的桌面工具，面向支持 KEF W2 / KEF Connect 的音箱。

当前支持的型号：

- LS50 Wireless II
- LSX II
- LS60 Wireless

当前这版主要功能：

- 程序启动时唤醒音箱
- Windows 关机时让音箱待机
- Windows 锁屏时让音箱待机
- Windows 解锁时唤醒音箱
- Windows 睡眠时让音箱待机
- 在音箱 IP 变化后恢复设备身份信息
- 以托盘 + GUI 的方式运行

## 项目结构

```text
kef_controller/
  main_gui.py
  main_headless.py
  README.md
  README.en.md
  README.zh-CN.md
  KEF Controller.spec
  requirements.txt
  installer/
    KEF_Controller.iss
  kef_headless_split/
    __init__.py
    backends.py
    controller.py
    discovery.py
    headless_runtime.py
    logging_setup.py
    models.py
    appdata/
      config.py
      config_store.py
      state_store.py
      system_config.py
      user_settings.py
    controller_support/
    platform_windows/
      windows_api.py
      windows_startup.py
      startup_support/
    ui/
      home_interface.py
      main_window.py
      test_interface.py
      tray_app.py
      logs/
      settings/
```

## 运行时文件

程序第一次启动后，会在 `%LocalAppData%\KEF Controller\` 下生成运行时文件：

- `config.json`
- `state.json`
- 日志文件

也就是说，打包后的 `.exe` 本身可以保持干净。用户配置、运行状态和日志都会写到当前 Windows 用户目录下，而不是写到程序目录旁边。

## 从源码运行

建议使用 Windows 的 `cmd.exe`。

GUI 模式：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python main_gui.py
```

Headless 模式：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
python main_headless.py
```

## 使用 PyInstaller 打包 EXE

直接在 `cmd.exe` 里执行：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller --noconsole --onefile --name "KEF Controller" --collect-all qfluentwidgets main_gui.py
```

这个命令的作用：

- `--noconsole`：生成窗口程序，不弹黑色控制台
- `--onefile`：打包成单个 `.exe`
- `--name "KEF Controller"`：指定输出文件名
- `--collect-all qfluentwidgets`：把 QFluentWidgets 运行资源一起打包
- `main_gui.py`：使用 GUI 入口打包

主要输出文件：

```text
dist\KEF Controller.exe
```

## 使用现有 `.spec` 文件打包

仓库里已经有：

- [KEF Controller.spec](KEF%20Controller.spec)

可以直接这样打包：

```bat
cd /d "path\to\kef_controller"
.venv\Scripts\activate
.venv\Scripts\pyinstaller "KEF Controller.spec"
```

如果你希望打包过程更稳定、配置固定，建议优先使用 `.spec` 文件。

## 使用 Inno Setup 生成安装包

先安装 Inno Setup。编译器通常在这里：

```text
C:\Program Files (x86)\Inno Setup 6\ISCC.exe
```

先确认 PyInstaller 输出已经存在：

```text
dist\KEF Controller.exe
```

仓库里已经包含安装脚本：

- [installer/KEF_Controller.iss](installer/KEF_Controller.iss)

在 `cmd.exe` 中执行：

```bat
cd /d "path\to\kef_controller"
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\KEF_Controller.iss
```

预期输出安装包：

```text
output\KEF_Controller_Setup.exe
```

## 推荐打包流程

1. 激活 `.venv`
2. 用 PyInstaller 生成 `dist\KEF Controller.exe`
3. 先手动测试这个 `.exe`
4. 再编译 `installer\KEF_Controller.iss`
5. 如果可以，最好在一台干净的 Windows 机器上再测一次安装包

## 补充说明

- Windows 自启动现在支持两种方式：普通的 `Registry Run`，以及可选的 `Task Scheduler`
- 配置、状态和日志都写到 `%LocalAppData%\KEF Controller\`
- 新生成的未签名程序如果被 SmartScreen 或杀毒软件提示，测试阶段比较常见
- 如果你改了程序名或输出文件名，记得同时修改 PyInstaller 命令和 Inno Setup 脚本
