# Windows 1.0.26 实现分析

## 结论

ScreenHop 不是 LAN KVM。运行时进程没有 TCP/UDP 连接；Windows 服务通过 BlueZ 对等的
Windows Bluetooth GATT/HID API 与 `HUAWEI Mouse WYN` 通信。鼠标本身保存多主机布局、
负责切换目标主机，并承载键盘协同数据。桌面程序的职责是检测光标撞到屏幕边缘、将坐标
归一化为 0–1000、向鼠标发出 CrossToHost，以及维护布局和设置。

## Windows 分层（观测）

- `MouseCrossingUI.exe`：.NET Framework 4.7.2 WPF UI，包含首页、设备列表、布局画布、
  DPI、快捷键、通用设置和关于等 View/ViewModel。
- `RpcClient.dll`：向 UI 导出 37 个 RPC 封装，如 `RpcEnableCross`、`RpcSetLayoutInfo`、
  `RpcKeyboardSynerg`、`RpcGetHostList`。
- `MouseCrossingService.exe`：Win32 核心；导入低层鼠标钩子、光标定位、显示器枚举、
  Bluetooth GATT、HID、SetupAPI 和 Windows RPC。
- `MouseCrossingUtil.exe`：Windows 服务入口，启动/守护核心服务。
- 本机日志显示 `CrossDetector -> CrossMain::CrossCallback -> Device::CrossToHost` 链路。

REA Evidence：桌面清单 `ev_060dc2c85068bfd699a6935416ed88e2dcddd55f2ffb713fd935056169e51bab`；
UI `ev_181fe6...` / native boundary `ev_b9c95...`；Dialog `ev_22e3997...`；
Service imports `ev_6927103f...`；RpcClient exports `ev_98e39...`。

## BLE 服务与帧（观测）

- 穿越服务：`0000fe40-cc7a-482a-984a-7f2ed5b3e512`
- RX notify：`0000fe42-cc7a-482a-984a-7f2ed5b3e512`
- TX write：`0000fe41-cc7a-482a-984a-7f2ed5b3e512`
- E600/E601/E602 是 HiLink JSON 通道；将其作为穿越通道只会收到 batteryInfo。
- 帧头通常为 `9F OPCODE KIND LENGTH PAYLOAD...`，KIND 1/2/3 分别为请求/事件/确认。
- 启用：`9F 01 01 02 01 01`；设备信息：`9F 03 01 01 01`。
- 布局：`9F 07 01 08 00 P0 01 P1 02 P2 03 P3`。
- 穿越：`9F 06 01 0A TARGET 08 X_BE32 Y_BE32`，其中 TARGET 是目标主机槽位。
  Windows 从槽位 2 的右边缘穿越到槽位 1 时实际发送 TARGET=1。
- 边缘方向仍为：1 上、2 下、3 左、4 右，但只用于布局选路，不直接写入 TARGET。

## Linux 映射

Windows 的 `SetWindowsHookEx/SetCursorPos` 在 GNOME Wayland 下由 Shell 扩展替代；扩展在
合成器内部读取指针位置并经会话 D-Bus 发出边缘事件。主程序用 BlueZ 系统 D-Bus 找到
FE40/FE41/FE42，再写入相同协议帧。这样不依赖 X11，也不需要读取 `/dev/input`。

## 实机验证

Ubuntu 真机已确认 GNOME Wayland、BlueZ FE40/FE41/FE42 通信、双主机列表、四方向布局
同步以及 Windows ↔ Linux 双向穿越。拖拽布局后，Linux 会写入鼠标保存的四槽位布局，
并同步更新本地边缘检测方向。代码与安装包包含 `screenhopctl` 逐帧诊断入口。

