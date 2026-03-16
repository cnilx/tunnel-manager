#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SSH 隧道管理工具
支持启动、查看状态、关闭隧道
"""

import os
import sys
import signal
import subprocess
import time
import yaml
import json
import psutil
from pathlib import Path

# 获取程序所在目录
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后的可执行文件
    BASE_DIR = Path(sys.executable).parent
else:
    # 开发环境
    BASE_DIR = Path(__file__).parent

# 修复Windows控制台编码问题
if sys.platform == 'win32' and sys.stdout is not None:
    import io
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


class TunnelManager:
    def __init__(self, config_file=None, pid_file=None, quiet=False):
        # 使用程序所在目录的绝对路径
        self.config_file = str(BASE_DIR / (config_file or "tunnels.yaml"))
        self.pid_file = str(BASE_DIR / (pid_file or ".tunnels.pid"))
        self.quiet = quiet
        self.tunnels = []
        self.processes = []

    def load_config(self):
        """加载配置文件"""
        config_path = Path(self.config_file)
        if not config_path.exists():
            # 如果配置文件不存在，创建一个空配置
            self.tunnels = []
            self.save_config()
            return self.tunnels

        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if not config or 'tunnels' not in config:
            self.tunnels = []
        else:
            self.tunnels = config['tunnels'] if config['tunnels'] else []

        return self.tunnels

    def save_config(self):
        """保存配置到文件"""
        config = {'tunnels': self.tunnels}
        with open(self.config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def save_pid_info(self, tunnel_info):
        """保存隧道进程信息到文件"""
        pid_data = []
        if Path(self.pid_file).exists():
            try:
                with open(self.pid_file, 'r', encoding='utf-8') as f:
                    pid_data = json.load(f)
            except:
                pid_data = []

        pid_data.extend(tunnel_info)

        with open(self.pid_file, 'w', encoding='utf-8') as f:
            json.dump(pid_data, f, ensure_ascii=False, indent=2)

    def load_pid_info(self):
        """从文件加载隧道进程信息"""
        if not Path(self.pid_file).exists():
            return []

        try:
            with open(self.pid_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return []

    def clear_pid_info(self):
        """清除 PID 文件"""
        if Path(self.pid_file).exists():
            os.remove(self.pid_file)

    def build_ssh_command(self, tunnel):
        """构建 SSH 命令"""
        name = tunnel.get('name', '未命名隧道')
        ssh_host = tunnel.get('ssh_host')
        ssh_port = tunnel.get('ssh_port', 22)
        local_port = tunnel.get('local_port')
        remote_host = tunnel.get('remote_host', '127.0.0.1')
        remote_port = tunnel.get('remote_port')
        extra_args = tunnel.get('extra_args', '')

        # 验证必需参数
        if not ssh_host:
            print(f"❌ 隧道 '{name}' 缺少 ssh_host 参数")
            return None
        if not local_port or not remote_port:
            print(f"❌ 隧道 '{name}' 缺少端口参数")
            return None

        # 构建端口转发参数（仅支持本地转发 -L）
        forward_arg = f"-L {local_port}:{remote_host}:{remote_port}"

        # 构建完整命令
        cmd = [
            'ssh',
            '-N',  # 不执行远程命令
            '-T',  # 禁用伪终端分配
            '-p', str(ssh_port),
            *forward_arg.split(),
            ssh_host
        ]

        if extra_args:
            cmd.extend(extra_args.split())

        return cmd

    def start_tunnel(self, tunnel):
        """启动单个隧道"""
        name = tunnel.get('name', '未命名隧道')
        cmd = self.build_ssh_command(tunnel)

        if not cmd:
            return None

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP |
                             subprocess.DETACHED_PROCESS |
                             subprocess.CREATE_NO_WINDOW) if sys.platform == 'win32' else 0
            )

            # 等待一小段时间检查进程是否立即失败
            time.sleep(0.5)
            if process.poll() is not None:
                stderr = process.stderr.read().decode('utf-8', errors='ignore')
                return None

            return process

        except Exception as e:
            return None

    def cmd_start(self):
        """启动所有隧道"""
        print("=" * 60)
        print("SSH 隧道管理器 - 启动隧道")
        print("=" * 60)
        print()

        # 检查是否已有运行的隧道
        existing = self.load_pid_info()
        if existing:
            print("⚠️  检测到已有运行的隧道，请先使用 stop.bat 关闭")
            print()
            self.cmd_status()
            sys.exit(1)

        self.load_config()
        tunnel_info = []

        for tunnel in self.tunnels:
            name = tunnel.get('name', '未命名隧道')
            print(f"🚀 启动隧道: {name}")

            process = self.start_tunnel(tunnel)
            if process:
                info = {
                    'name': name,
                    'pid': process.pid,
                    'config': tunnel
                }
                tunnel_info.append(info)
                print(f"✅ 隧道已建立 (PID: {process.pid})")
            else:
                print(f"❌ 启动失败")
            print()

        if not tunnel_info:
            print("❌ 没有成功启动任何隧道")
            sys.exit(1)

        # 保存进程信息
        self.save_pid_info(tunnel_info)

        print("=" * 60)
        print(f"✅ 成功启动 {len(tunnel_info)} 个隧道")
        print("=" * 60)
        print()

        # 显示状态
        self.cmd_status()

    def cmd_status(self):
        """查看隧道状态"""
        tunnel_info = self.load_pid_info()

        if not tunnel_info:
            print("⚠️  没有运行中的隧道")
            return

        print("=" * 60)
        print("隧道状态")
        print("=" * 60)
        print()

        active_count = 0
        dead_pids = []

        for idx, item in enumerate(tunnel_info, 1):
            pid = item['pid']
            name = item['name']
            tunnel = item['config']

            # 检查进程是否还在运行
            try:
                process = psutil.Process(pid)
                if process.is_running():
                    status = "✅ 运行中"
                    active_count += 1
                else:
                    status = "❌ 已停止"
                    dead_pids.append(pid)
            except psutil.NoSuchProcess:
                status = "❌ 已停止"
                dead_pids.append(pid)

            local_port = tunnel.get('local_port')
            remote_host = tunnel.get('remote_host', '127.0.0.1')
            remote_port = tunnel.get('remote_port')
            ssh_host = tunnel.get('ssh_host')

            print(f"{idx}. {name} - {status}")
            print(f"   本地端口 {local_port} → {ssh_host} → {remote_host}:{remote_port}")
            print(f"   进程 PID: {pid}")
            print()

        print("=" * 60)
        print(f"活跃隧道: {active_count}/{len(tunnel_info)}")
        print("=" * 60)

        # 清理已停止的进程记录
        if dead_pids:
            tunnel_info = [t for t in tunnel_info if t['pid'] not in dead_pids]
            if tunnel_info:
                self.save_pid_info(tunnel_info)
            else:
                self.clear_pid_info()

    def cmd_stop(self):
        """关闭所有隧道"""
        tunnel_info = self.load_pid_info()

        if not tunnel_info:
            print("⚠️  没有运行中的隧道")
            return

        print("=" * 60)
        print("关闭隧道")
        print("=" * 60)
        print()

        for item in tunnel_info:
            pid = item['pid']
            name = item['name']

            try:
                process = psutil.Process(pid)
                if process.is_running():
                    print(f"🔌 关闭隧道: {name} (PID: {pid})")
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        process.kill()
                    print(f"✅ 已关闭")
                else:
                    print(f"⚠️  隧道 '{name}' 已经停止")
            except psutil.NoSuchProcess:
                print(f"⚠️  隧道 '{name}' 进程不存在")
            except Exception as e:
                print(f"❌ 关闭隧道 '{name}' 时出错: {e}")

            print()

        # 清除 PID 文件
        self.clear_pid_info()

        print("=" * 60)
        print("✅ 所有隧道已关闭")
        print("=" * 60)


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python tunnel.py start   - 启动所有隧道")
        print("  python tunnel.py status  - 查看隧道状态")
        print("  python tunnel.py stop    - 关闭所有隧道")
        sys.exit(1)

    command = sys.argv[1].lower()
    manager = TunnelManager()

    if command == 'start':
        manager.cmd_start()
    elif command == 'status':
        manager.cmd_status()
    elif command == 'stop':
        manager.cmd_stop()
    else:
        print(f"❌ 未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
