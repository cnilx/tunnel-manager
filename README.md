# SSH 隧道管理器

带 GUI 界面的 SSH 隧道管理工具，支持批量管理、状态监控和自动重启。

## 功能特点

- 图形化界面，操作简单
- 批量管理多个 SSH 隧道
- 实时状态监控
- 自动重启死掉的进程
- 系统托盘运行
- 操作日志记录

## 快速开始

### 使用发布版（推荐）

1. 进入 `release/` 目录
2. 双击 `SSHTunnelManager.exe` 启动
3. 无需安装 Python，开箱即用

### 从源码运行

```bash
# 创建虚拟环境（如果不存在）
python -m venv .venv

# 激活虚拟环境
.venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 运行
python source/tunnel_gui.py
```

## 项目结构

```
.
├── README.md              # 项目说明
├── requirements.txt       # Python 依赖
├── build.bat              # 打包脚本
├── source/                # 源代码
│   ├── tunnel.py          # 核心管理模块
│   ├── tunnel_gui.py      # GUI 界面
│   └── tunnels.yaml       # 配置文件示例
└── release/               # 发布版本
    ├── SSHTunnelManager.exe
    └── tunnels.yaml
```

## 配置说明

### tunnels.yaml 字段

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | 是 | - | 隧道名称（唯一标识） |
| `ssh_host` | 是 | - | SSH 服务器地址，格式 `user@host` |
| `ssh_port` | 否 | `22` | SSH 端口 |
| `local_bind` | 否 | `127.0.0.1` | 本地绑定地址（见下方说明） |
| `local_port` | 是 | - | 本地监听端口 |
| `remote_host` | 否 | `127.0.0.1` | 远程目标主机 |
| `remote_port` | 是 | - | 远程目标端口 |
| `auto_start` | 否 | `false` | 程序启动时自动建立隧道 |

### 局域网共享（local_bind）

默认情况下隧道只绑定本机 `127.0.0.1`，仅本机可访问。

若需要让**同一局域网内的其他人**也能访问，将 `local_bind` 设为 `0.0.0.0`：

```yaml
tunnels:
- name: mysql
  ssh_host: user@server
  ssh_port: 22
  local_bind: 0.0.0.0   # 局域网内所有人可访问
  local_port: 3306
  remote_host: 127.0.0.1
  remote_port: 3306
  auto_start: false
  pid: null
```

> 也可在 GUI 的添加/编辑隧道对话框中通过下拉框选择绑定地址，无需手动编辑 YAML。

## 开发

### 打包可执行文件

```bash
# 双击运行
build.bat
```

生成的可执行文件位于 `release/SSHTunnelManager.exe`

## 依赖

- PyYAML >= 6.0 - YAML 配置文件解析
- psutil >= 5.9.0 - 进程管理
- Pillow >= 10.0.0 - 系统托盘图标
- pyinstaller >= 6.0.0 - 打包工具（仅构建时需要）
