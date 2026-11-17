> 2.0.1 修复更新：下列 7 项已在本次发布中处理。本文保留原审查证据；当前实现与验证结果以 release_notes/RELEASE_NOTES_v2.0.1.md 为准。

# KEF Controller 审查与 pykefcontrol 1.0.1 迁移

审查日期：2026-11-16。目录：`C:/Users/alan/My Drive/alan/kef/kef_controller`。

What this repo does: 这是单用户 Windows 托盘音箱控制器，接收锁屏、显示器和系统电源事件，控制局域网 KEF 音箱。审查按一台电脑、一个主要目标音箱、UI 与后台线程并发运行的负载判断，不按多用户服务器评价。

结论：有明确的正确性问题，也有重复状态和无效配置带来的维护负担。预热 socket、两种 generation（目标版本与动作版本）、有界回退、消息泵外执行网络请求有实际用途，应保留。没有证据支持为了减少文件数重写整个程序或替换 UI 框架。

原有未跟踪文件 `DESIGN_REVIEW.zh-CN.md` 保持原样；本报告单独记录此次检查。下列审查发现尚未修改；已经完成的依赖迁移列在后面。

## 必须修复

1. **连续网络失败被当成已确认待机** — `kef_app/controller/actions/device_common.py:218`。
   - 作用：请求待机后读取状态，确认音箱实际待机。
   - 问题：两次读取异常直接返回 `True`，原因是 `device_unreachable`。将读取替换为连续“network down”错误后，实际复现返回 `(True, ...)`；Wi-Fi 掉线也会被解释为成功待机。
   - 最小修复：网络失败返回未确认，把“已发送”和“已观测待机”区分开；只有明确状态/输入源为 standby 才确认，并修正对应测试。
   - 不修后果：界面、日志和后续电源判断可能声称待机成功，音箱实际仍在播放。

2. **旧音量任务污染新目标，切源重试也没有目标边界** — `kef_app/controller/actions/device_controls.py:602`、`:530`。
   - 作用：用户调整音量或输入源后，后台发送请求并更新显示。
   - 问题：`set_volume()` 没有记录目标 generation，写入后直接更新当前缓存。实测在旧音箱的 volume setter 中切换到 B，调用仍返回 True，B 的缓存音量变成旧任务的 65；切源重试会重新调用 `get_speaker()`，也没有验证是否仍是原目标。
   - 最小修复：任务开始时固定目标及 generation，获取动作锁后再校验，每次重试/提交缓存时检查；过期结果丢弃。已有 `_set_speaker_runtime_state(target_generation=...)` 可直接复用。
   - 不修后果：新音箱显示旧任务的音量，切源重试还可能操作用户刚选的新音箱。

3. **快速待机固定走未签名 HTTP，无法覆盖要求 HTTPS 写入的固件** — `kef_app/devices/transport/standby_request.py:16`、`kef_app/controller/standby/prewarmed_socket.py`。
   - 作用：Windows 即将睡眠或关机时，利用预热连接和原始请求尽快发送待机。
   - 问题：这条路径固定使用 80 端口和原始 HTTP POST，完全绕过 pykefcontrol；普通按钮即使已迁移至 HTTPS，也不会改变这条路径。这是源码确认的协议缺口，尚未用真实固件证明具体哪台音箱拒绝它。
   - 最小修复：先确认目标固件是否接受 HTTP；需要安全写入时，在常规运行阶段准备正确的安全传输，再复用现有 deadline、取消与回退逻辑。不能在睡眠回调里临时做证书加载、协议探测和用户注册。
   - 不修后果：只接受 HTTPS 签名写入的设备可能手动控制正常，自动睡眠/关机待机却失败；socket 写入成功不能证明设备接受请求。

4. **托盘保留旧目标状态，并接受过期目标事件** — `kef_app/ui/tray_application.py:180`。
   - 作用：根据音箱状态显示托盘图标、菜单文字和提示。
   - 问题：`_on_identity_changed()` 只刷新图标，未清空 `_speaker_on_hint`；`_on_speaker_state_changed()` 忽略传入的 `_target_generation`。A 为开机后切到离线 B，旧的 True 提示仍能让 B 显示为在线；队列里的 A 事件也能覆盖 B。
   - 最小修复：像已有 `WebStateMixin` 一样检查目标版本，在目标变化时清空提示；最终统一使用控制器的目标快照。
   - 不修后果：网页与托盘互相矛盾，用户看到的可能是上一台音箱的状态。

## 应当修复

5. **“网络不可达，未发送”被定义为动作成功** — `kef_app/controller/power_state.py:64`、`kef_app/ui/web_state.py:175`。
   - 作用：把动作结果转换为事件和通知。
   - 问题：成功判定使用 `startswith("sent_")`，实际调用 `power_action_outcome_is_success("sent_skipped_host_unreachable")` 返回 True，UI 随后发送 success 通知并清除上次失败。现有 confirmed 判断没有把它确认成待机，这点应保留。
   - 最小修复：先给这个明确的 skipped 结果返回 False，或在现有集中映射处区分 skipped/sent/confirmed；无需立刻引入一组新服务类。
   - 不修后果：网络故障被显示成绿色成功，降低诊断可信度。

6. **同一次状态读取有两条 UI 提交通道** — `kef_app/controller/actions/device_controls.py:295`、`kef_app/ui/web_state.py:73`。
   - 作用：常规定时读取电源、输入源和音量，然后更新网页。
   - 问题：状态变化时控制器已经发出 `speaker_state_changed`；同一次读取完成后，网页又发 `_polled_state`，两路最终都会调用 `_apply_speaker_state()` 和 `publish_state()`。网页、托盘和控制器还各自维护开关状态，问题 2、4 正是同步规则不完整的具体例子。
   - 最小修复：保留控制器状态事件作为数值更新入口，读取完成路径只记录成功时间/错误；继续保留音量拖动等界面临时状态。身份刷新是否重复发网络请求需另做计数，不能仅凭存在多个定时器断言。
   - 不修后果：同一次变化重复序列化和发送 UI 状态，状态规则需要维护多份；本次没有测得明显 CPU 或内存瓶颈。

## 可以简化

7. **backend_name 是可保存的选项，却无法切换后端** — `kef_app/config/user_settings.py:15`、`kef_app/controller/core.py:48`。
   - 作用：记录当前音箱后端标识。
   - 问题：配置加载器接受任意字符串，日志和身份会使用该字符串，但控制器始终创建 `W2Backend`。例如保存另一个名称，只会改变记录，无法改变实际实现。
   - 最小修复：把它固定为现有后端常量；加载老配置时接受并忽略该项，删除持久化的虚假选择。无需添加后端工厂或插件系统。
   - 不修后果：配置与实际行为不一致，并让维护者误以为存在多后端能力。

## 过度设计的判断

- 控制器使用多层 Mixin，却仍共享一个 self、状态和多把锁。它提高了阅读成本，但“改成几个 Service”本身不是收益。本次不建议仅为文件数或类数重构；先修上面的目标隔离和状态归属。
- AppConfig/UserSettings 两层动态属性转发与配置字段映射有维护成本，但现有调用大量依赖平铺访问；直接删除会破坏调用者。下次修改配置模型时统一一种内部访问方式，老 JSON 兼容留在存储边界即可。
- Qt 托盘、独立 WebView 进程和本地 HTTP 的确较重，同时提供界面故障隔离。没有测量内存、包体和启动时间之前，不把整套 UI 替换当成本次优化任务。
- 快速待机预热、保活协议解析、目标取消、动作取消与两种回退均有实测场景；不能为了“精简”删去。

## 已完成的 1.0.1 迁移

上游 1.0.0 是破坏兼容的重写，已删除 `KefConnector`，要求 Python 3.12+；1.0.1 修正了不可读固件/硬件字段，建议使用 `DeviceInfo.firmware`。

- requirements 与当前项目 `.venv` 均升级到 `pykefcontrol==1.0.1`。
- 在原有音箱后端边界接入 `KefClient`。保留控制器现有调用契约，适配电源、输入源、音量、新事件队列和 Wi-Fi 信息，避免为升级重写整个控制器。
- 移除旧的全局 `kef_connector.requests` 替换、requests 会话缓存和 requests 直接依赖。保留线程局部超时范围；缓存客户端在超时范围改变时会替换，旧客户端在不再被使用时释放连接。
- 事件队列新建后主动读取初始状态；恢复订阅使用公开 reset 接口，删除旧私有轮询字段操作。
- 身份读取优先使用 `DeviceInfo.firmware`，处理空名称/MAC，并补齐新版 LS60W 的型号归一化。
- PyInstaller spec 收集 pykefcontrol 版本元数据，更新中英文 README 与旧审查自检脚本。

重要限制：上游自动 HTTPS 探测超时为 3 秒；HTTPS 签名写请求强制至少 15 秒，并可能包含注册/认证重试。传给 KefClient 的 socket_timeout 不是整个操作的总 deadline。保留上游处理固件认证延迟的行为；紧急原始发送仍维持原来的独立预算，协议缺口见问题 3。

参考：[1.0.0 发布说明](https://github.com/N0ciple/pykefcontrol/releases/tag/1.0.0)、[1.0.1 发布说明](https://github.com/N0ciple/pykefcontrol/releases/tag/1.0.1)。GitHub releases 列表的网页缓存仍显示 0.9.3，本次通过 GitHub release API、PyPI 和已安装的 1.0.1 源码交叉核实。

## 验证与范围

已运行：Ruff 全项目检查、308 项 unittest（无跳过）、compileall、pip check、git diff --check、`tests/review_repro.py`。新增测试使用真实 1.0.1 客户端并在 HTTP 发送边界模拟设备，验证 HTTPS 签名/HTTP 回退、写入负载与实际超时、三个支持型号的身份、事件初始状态/变化/重订阅、嵌套和线程隔离超时以及连接释放；没有向真实音箱发指令。

已深入检查：依赖和构建配置、入口、音箱后端、控制动作、目标切换与恢复、身份识别与扫描、事件桥、网页状态、托盘状态、本地 API 认证与路径限制、JSON 原子写入及相关回归测试。启动注册、完整 Win32 消息泵、原始 socket HTTP 解析和全部前端文件未逐行复审，主要依据现有测试及相关调用点检查。

Verdict: 先修网络失败被确认待机与目标隔离，再处理固件传输兼容，最后收敛重复状态；没有必要启动整体重写。

Lean: 已删除旧 requests 适配和会话缓存，运行依赖减少 1 个（requests）；保留仅服务本次迁移的现有边界适配，不添加框架。其他删减数量未经实际实施，不承诺行数。

Not checked: 真实 KEF 音箱、新旧固件互操作、Windows 锁屏/睡眠/合盖/关机、打包后 EXE 与安装器。本次没有构建或替换已安装应用，也未提交或推送 Git；已有业务问题仍按上述编号列出。
