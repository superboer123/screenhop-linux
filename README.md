# ScreenHop Linux

[![Release](https://img.shields.io/github/v/release/superboer123/screenhop-linux)](https://github.com/superboer123/screenhop-linux/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Ubuntu%20%7C%20GNOME%20Wayland-orange)](#兼容性)

华为 `HUAWEI Mouse WYN` 键鼠穿越功能的 Linux 客户端。它通过鼠标内置的多主机能力和
Bluetooth LE GATT 协议，与 Windows/macOS 端互通，实现鼠标从屏幕边缘穿越到另一台设备。

> 本项目是独立的社区实现，与华为官方无隶属关系。

## 功能

- Linux、Windows 等已连接主机之间双向鼠标穿越；
- GNOME Wayland 原生边缘检测，并提供 XWayland 后端；
- 自动读取鼠标保存的主机列表和设备布局；
- 可视化设备布局，本机与对端均可拖到上、下、左、右；
- 按实际连接数量展示设备，以“蓝牙 1 / 蓝牙 2”等槽位区分；
- 穿越开关与鼠标硬件状态同步；
- 兼容键盘的“键盘跟随鼠标”设置入口；
- 登录桌面后自动启动，可在界面中随时开关；
- 关闭主窗口后继续后台穿越，并提供系统托盘图标用于显示或隐藏窗口；
- 锁屏、解锁和系统休眠恢复后自动重建边缘检测；
- `screenhopctl` 命令行诊断工具。

## 兼容性

当前验证环境：

| 项目 | 状态 |
| --- | --- |
| Ubuntu 26.04.1 LTS | 已验证 |
| GNOME Shell 50.1 Wayland | 已验证 |
| HUAWEI Mouse WYN | 已验证 |
| Windows ↔ Linux 双向穿越 | 已验证 |
| 可视化布局同步 | 已验证 |
| 兼容多设备键盘联动 | 已接入协议状态，仍需对应键盘实机验证 |

其他 Linux 桌面环境尚未逐一测试。

## 安装

从 [Releases](https://github.com/superboer123/screenhop-linux/releases/latest) 下载最新 `.deb`：

```bash
sudo apt install ./screenhop-linux_0.8.2_all.deb
gnome-extensions enable screenhop@open-source
```

建议注销并重新登录一次，使 GNOME Shell 扩展和自动启动项完全生效。随后：

1. 在 Linux 蓝牙设置中配对 `HUAWEI Mouse WYN`；
2. 将鼠标切换到 Linux 对应的蓝牙槽位；
3. 启动“键鼠穿越”，点击“连接鼠标”；
4. 确认穿越开关已开启；
5. 拖动设备卡片设置实际的上、下、左、右关系；
6. 将指针推向对应屏幕边缘即可切换设备。

## 开机自动启动

安装包提供 GNOME 自动启动入口。界面的“开机自动启动”开关会在
`~/.config/autostart/screenhop.desktop` 写入当前用户设置：

- 开启：登录桌面后自动运行穿越服务；
- 关闭：写入用户级覆盖项，停用系统自动启动入口。

## 命令行诊断

```bash
# 查找支持的鼠标
screenhopctl devices

# 读取设备信息
screenhopctl info --address AA:BB:CC:DD:EE:FF

# 开启穿越
screenhopctl enable

# 请求切换到目标槽位
screenhopctl cross --target 2 --x 995 --y 500
```

## 从源码构建

依赖：Python 3、GTK 4、libadwaita、PyGObject、python3-dbus、BlueZ、GNOME Shell。

```bash
git clone https://github.com/superboer123/screenhop-linux.git
cd screenhop-linux

python3 -m pytest tests/test_protocol.py
sh packaging/build-deb.sh
sudo apt install ./dist/screenhop-linux_0.8.2_all.deb
```

## 实现结构

```text
src/screenhop/
├── app.py       GTK4/libadwaita 界面与状态机
├── bluez.py     BlueZ D-Bus 与 FE40/FE41/FE42 GATT 通信
├── edge.py      GNOME Wayland / XWayland 屏幕边缘检测
├── protocol.py  9F 协议编解码、布局与穿越命令
└── cli.py       screenhopctl 诊断入口

extension/       GNOME Shell 边缘检测扩展
packaging/       Debian/Ubuntu 打包脚本
tests/           协议回归测试
```

Windows 端实现和 BLE 协议分析见 [`ANALYSIS.md`](ANALYSIS.md)。

## 故障排查

### 检测不到设备

- 确认鼠标已经在 Linux 蓝牙设置中配对；
- 将鼠标切换到 Linux 对应槽位；
- 运行 `screenhopctl devices` 检查 BlueZ 是否能发现 FE40 服务；
- 点击界面的“重新检测设备”。

### 只能单向穿越

- 检查两端设备布局方向是否一致；
- 重新拖动设备卡片并等待“设备布局已更新”；
- 确认 GNOME 边缘检测显示为已就绪。

### GNOME 扩展尚未生效

```bash
gnome-extensions enable screenhop@open-source
```

然后注销并重新登录。扩展生效前，程序会尝试使用 XWayland 边缘后端。

## 许可证

[MIT License](LICENSE)
