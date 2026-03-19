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
import psutil
import socket
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
            # 确保每个隧道都有 pid 字段
            for tunnel in self.tunnels:
                if 'pid' not in tunnel:
                    tunnel['pid'] = None

        return self.tunnels

    def save_config(self):
        """保存配置到文件"""
        config = {'tunnels': self.tunnels}
        with open(self.config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def update_tunnel_pid(self, tunnel_name, pid):
        """更新指定隧道的 PID"""
        for tunnel in self.tunnels:
            if tunnel.get('name') == tunnel_name:
                tunnel['pid'] = pid
                break
        self.save_config()

    def clear_tunnel_pid(self, tunnel_name):
        """清除指定隧道的 PID"""
        for tunnel in self.tunnels:
            if tunnel.get('name') == tunnel_name:
                tunnel['pid'] = None
                break
        self.save_config()

    def clear_all_pids(self):
        """清除所有隧道的 PID"""
        for tunnel in self.tunnels:
            tunnel['pid'] = None
        self.save_config()

    def get_running_tunnels(self):
        """获取所有运行中的隧道信息"""
        running = []
        for tunnel in self.tunnels:
            pid = tunnel.get('pid')
            if pid:
                try:
                    process = psutil.Process(pid)
                    if process.is_running():
                        running.append(tunnel)
                    else:
                        # 进程已停止，清除 PID
                        tunnel['pid'] = None
                except psutil.NoSuchProcess:
                    # 进程不存在，清除 PID
                    tunnel['pid'] = None

        # 如果有 PID 被清除，保存配置
        if any(t.get('pid') is None for t in self.tunnels):
            self.save_config()

        return running

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

    def test_tunnel_connectivity(self, local_port, timeout=2, max_retries=5):
        """测试隧道连通性"""
        for attempt in range(max_retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                result = sock.connect_ex(('127.0.0.1', local_port))
                sock.close()

                if result == 0:
                    return True

                if attempt < max_retries - 1:
                    time.sleep(2)

            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(2)

        return False

    def start_tunnel(self, tunnel):
        """启动单个隧道"""
        name = tunnel.get('name', '未命名隧道')
        local_port = tunnel.get('local_port')
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
                return None

            # 等待SSH隧道建立
            time.sleep(2)

            # 再次检查进程是否还活着
            if process.poll() is not None:
                return None

            # 测试隧道连通性
            if not self.test_tunnel_connectivity(local_port):
                process.terminate()
                try:
                    process.wait(timeout=3)
                except:
                    process.kill()
                return None

            return process

        except Exception:
            return None

    def cmd_start(self):
        """启动所有隧道"""
        print("=" * 60)
        print("SSH 隧道管理器 - 启动隧道")
        print("=" * 60)
        print()

        # 检查是否已有运行的隧道
        self.load_config()
        running = self.get_running_tunnels()
        if running:
            print("⚠️  检测到已有运行的隧道，请先使用 stop.bat 关闭")
            print()
            self.cmd_status()
            sys.exit(1)

        success_count = 0

        for tunnel in self.tunnels:
            name = tunnel.get('name', '未命名隧道')
            print(f"🚀 启动隧道: {name}")

            process = self.start_tunnel(tunnel)
            if process:
                self.update_tunnel_pid(name, process.pid)
                success_count += 1
                print(f"✅ 隧道已建立 (PID: {process.pid})")
            else:
                print(f"❌ 启动失败")
            print()

        if success_count == 0:
            print("❌ 没有成功启动任何隧道")
            sys.exit(1)

        print("=" * 60)
        print(f"✅ 成功启动 {success_count} 个隧道")
        print("=" * 60)
        print()

        # 显示状态
        self.cmd_status()

    def cmd_status(self):
        """查看隧道状态"""
        self.load_config()

        if not self.tunnels:
            print("⚠️  没有配置任何隧道")
            return

        print("=" * 60)
        print("隧道状态")
        print("=" * 60)
        print()

        active_count = 0

        for idx, tunnel in enumerate(self.tunnels, 1):
            pid = tunnel.get('pid')
            name = tunnel.get('name', '未命名隧道')

            # 检查进程是否还在运行
            if pid:
                try:
                    process = psutil.Process(pid)
                    if process.is_running():
                        status = "✅ 运行中"
                        active_count += 1
                    else:
                        status = "❌ 已停止"
                        tunnel['pid'] = None
                except psutil.NoSuchProcess:
                    status = "❌ 已停止"
                    tunnel['pid'] = None
            else:
                status = "⚪ 未启动"

            local_port = tunnel.get('local_port')
            remote_host = tunnel.get('remote_host', '127.0.0.1')
            remote_port = tunnel.get('remote_port')
            ssh_host = tunnel.get('ssh_host')

            print(f"{idx}. {name} - {status}")
            print(f"   本地端口 {local_port} → {ssh_host} → {remote_host}:{remote_port}")
            if pid:
                print(f"   进程 PID: {pid}")
            print()

        # 保存更新后的配置（清除已停止进程的 PID）
        self.save_config()

        print("=" * 60)
        print(f"活跃隧道: {active_count}/{len(self.tunnels)}")
        print("=" * 60)

    def cmd_stop(self):
        """关闭所有隧道"""
        self.load_config()
        running = self.get_running_tunnels()

        if not running:
            print("⚠️  没有运行中的隧道")
            return

        print("=" * 60)
        print("关闭隧道")
        print("=" * 60)
        print()

        for tunnel in running:
            pid = tunnel.get('pid')
            name = tunnel.get('name', '未命名隧道')

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
                    self.clear_tunnel_pid(name)
                else:
                    print(f"⚠️  隧道 '{name}' 已经停止")
                    self.clear_tunnel_pid(name)
            except psutil.NoSuchProcess:
                print(f"⚠️  隧道 '{name}' 进程不存在")
                self.clear_tunnel_pid(name)
            except Exception as e:
                print(f"❌ 关闭隧道 '{name}' 时出错: {e}")

            print()

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
