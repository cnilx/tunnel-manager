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
