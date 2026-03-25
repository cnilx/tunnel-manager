# SSH 隧道管理器 1.0.4

带 GUI 界面的 SSH 隧道管理工具，支持本地转发与反向转发，批量管理、标签/类型筛选、端口校验、状态监控和自动重启。

## 功能特点

- 图形化界面，操作简单
- 批量管理多个 SSH 隧道
- **本地转发（-L）**：将远端服务映射到本机端口
- **反向转发（-R）**：将本机服务暴露到远端服务器
- 标签/类型筛选和批量启动/停止
- 启动前端口冲突与端口占用校验
- 实时状态监控
- 自动重启死掉的进程
- 系统托盘运行
- 默认关闭到托盘
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

首次启动时如果本地没有配置文件，程序会自动创建空的 `tunnels.yaml`。

## 项目结构

```
.
├── README.md              # 项目说明
├── requirements.txt       # Python 依赖
├── build.bat              # 打包脚本
├── source/                # 源代码
│   ├── tunnel.py          # 核心管理模块
│   ├── tunnel_gui.py      # GUI 界面
│   └── tunnels.yaml       # 本地运行时自动生成的配置文件（不提交）
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
| `tunnel_type` | 否 | `local` | 隧道类型：`local`（本地转发 -L）或 `remote`（反向转发 -R） |
| `tags` | 否 | `[]` | 标签列表，用于 GUI 中的快速筛选，例如 `["生产", "MySQL"]` |
| `auto_start` | 否 | `false` | 程序启动时自动建立隧道；程序完整退出前会记住当前仍在运行的隧道，并在下次启动时自动恢复 |

> `auto_start` 在 GUI 模式下既可手工配置，也会在程序完整退出前被更新为当前仍在运行的隧道集合，用于下次启动自动恢复。

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
  tags:
    - 数据库
    - 局域网共享
  auto_start: false
  pid: null
```

> 也可在 GUI 的添加/编辑隧道对话框中直接填写标签，并通过顶部“标签筛选”下拉框快速查看同一类隧道，无需手动编辑 YAML。

### 反向转发（tunnel_type: remote）

反向转发将**本机服务暴露到远端服务器**，让服务器上的端口映射回本机。

SSH 命令格式：`ssh -R 远端监听地址:远端监听端口:本机服务地址:本机服务端口 ssh_host`

```yaml
tunnels:
- name: expose-dev-server
  tunnel_type: remote
  ssh_host: user@my-server.com
  ssh_port: 22
  local_bind: 127.0.0.1   # 本机服务地址（不能填 0.0.0.0）
  local_port: 3000         # 本机服务端口
  remote_host: 0.0.0.0    # 远端绑定地址（0.0.0.0 = 所有网卡，127.0.0.1 = 仅服务器本机）
  remote_port: 8080        # 远端服务器监听端口
  auto_start: false
  tags: []
```

访问方式：`远端服务器IP:8080` → 流量转发到 → `本机127.0.0.1:3000`

> **注意：若要通过公网 IP 访问反向隧道端口**，需在服务器 `/etc/ssh/sshd_config` 中添加：
> ```
> GatewayPorts clientspecified
> ```
> 然后重启 sshd（`systemctl restart ssh`），并在云服务商安全组中开放对应端口。

> 列表中反向转发隧道名称前会显示 `[R]` 标识，本地转发显示 `[L]`，可通过顶部「类型筛选」快速过滤。

### 配置文件位置

- 源码运行时默认使用 `source/tunnels.yaml`
- 发布版运行时默认使用 `release/tunnels.yaml`
- 如果文件不存在，程序会自动创建一个空配置：

```yaml
tunnels: []
```

### 端口校验规则

- 添加/编辑隧道时，会拦截与现有隧道配置冲突的本地监听端口
- 启动隧道时，会额外检查本机端口是否已被其他进程占用
- 端口冲突或占用时，GUI 会弹窗提示具体原因

### GUI 交互说明

- 点击窗口右上角关闭按钮时，程序默认最小化到系统托盘，隧道继续运行
- 如需完整退出，请使用托盘菜单中的“退出”
- 表格支持多选，`Delete` 键可批量删除选中的隧道
- 右键菜单提供“浏览器中打开”，会使用默认浏览器访问本地端口地址
- 表格顶部“启动所有隧道 / 停止所有隧道”会按当前标签筛选结果生效

## SSH 公钥配置（免密登录）

使用 SSH 隧道配置公钥认证，避免输入密码。

### 第一步：生成密钥对

```bash
# Windows（PowerShell 或 Git Bash）
ssh-keygen -t ed25519 -C "your_email@example.com"
```

按提示操作：
- 密钥保存路径：直接回车使用默认路径 `~/.ssh/id_ed25519`
- 密码短语：建议留空（直接回车），方便自动化连接

生成后会得到两个文件：
- `~/.ssh/id_ed25519` — 私钥（**不要泄露**）
- `~/.ssh/id_ed25519.pub` — 公钥（上传到服务器）

### 第二步：将公钥上传到服务器

**手动追加（适用于 PowerShell）**

```powershell
# 查看公钥内容
Get-Content ~/.ssh/id_ed25519.pub

# 将公钥追加到服务器的 authorized_keys
$pubkey = Get-Content ~/.ssh/id_ed25519.pub
ssh user@your-server "mkdir -p ~/.ssh && echo '$pubkey' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

### 第三步：验证免密登录

```bash
ssh -p 22 user@your-server
# 若不再提示输入密码，则配置成功
```

配置成功后，SSH 隧道管理器即可无需手动干预地自动建立和恢复隧道连接。

---

## 开发

### 打包可执行文件

```bash
# Windows 下双击运行
build.bat
```

生成的可执行文件位于 `release/SSHTunnelManager.exe`

### 版本发布

- 当前版本号定义在 `source/tunnel_gui.py` 的 `APP_VERSION`
- 发布新版本时，先更新 `APP_VERSION` 与 `CHANGELOG.md`
- 然后手动执行 `build.bat` 生成新的 `release/SSHTunnelManager.exe`

## 依赖

- PyYAML >= 6.0 - YAML 配置文件解析
- psutil >= 5.9.0 - 进程管理
- Pillow >= 10.0.0 - 系统托盘图标
- pyinstaller >= 6.0.0 - 打包工具（仅构建时需要）
