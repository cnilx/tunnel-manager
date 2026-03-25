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
import threading
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
        self.config_lock = threading.RLock()
        self.last_error = None
        self._thread_state = threading.local()

    def _set_last_error(self, message):
        """设置当前线程最近一次错误。"""
        self.last_error = message
        self._thread_state.last_error = message

    def get_last_error(self):
        """获取当前线程最近一次错误。"""
        return getattr(self._thread_state, 'last_error', self.last_error)

    def load_config(self):
        """加载配置文件"""
        with self.config_lock:
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
                    if 'tunnel_type' not in tunnel:
                        tunnel['tunnel_type'] = 'local'

            return self.tunnels

    def save_config(self):
        """保存配置到文件"""
        with self.config_lock:
            config = {'tunnels': self.tunnels}
            config_path = Path(self.config_file)
            temp_path = config_path.with_suffix(f"{config_path.suffix}.tmp")

            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                os.replace(temp_path, config_path)
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    def _build_tunnel_identity(self, tunnel):
        """构建稳定的隧道标识，用于跨 reload 定位同一条配置。"""
        return (
            tunnel.get('name'),
            tunnel.get('ssh_host'),
            tunnel.get('ssh_port', 22),
            tunnel.get('local_bind') or '127.0.0.1',
            tunnel.get('local_port'),
            tunnel.get('remote_host') or '127.0.0.1',
            tunnel.get('remote_port'),
            (tunnel.get('extra_args') or '').strip(),
            tunnel.get('tunnel_type', 'local'),
        )

    def _find_tunnel_unlocked(self, tunnel_ref):
        """按索引、对象引用或稳定标识定位隧道。"""
        if isinstance(tunnel_ref, int):
            if 0 <= tunnel_ref < len(self.tunnels):
                return self.tunnels[tunnel_ref]
            return None

        if isinstance(tunnel_ref, dict):
            for tunnel in self.tunnels:
                if tunnel is tunnel_ref:
                    return tunnel

            tunnel_identity = self._build_tunnel_identity(tunnel_ref)
            for tunnel in self.tunnels:
                if self._build_tunnel_identity(tunnel) == tunnel_identity:
                    return tunnel

            tunnel_name = tunnel_ref.get('name')
        else:
            tunnel_name = tunnel_ref

        if tunnel_name is None:
            return None

        for tunnel in self.tunnels:
            if tunnel.get('name') == tunnel_name:
                return tunnel

        return None

    def get_tunnel_identity(self, tunnel):
        """返回稳定的隧道标识。"""
        return self._build_tunnel_identity(tunnel)

    def get_tunnel_local_bind(self, tunnel):
        """获取本地绑定地址，空值回退到默认地址。"""
        return tunnel.get('local_bind') or '127.0.0.1'

    def get_tunnel_remote_host(self, tunnel):
        """获取远程目标主机，空值回退到默认地址。"""
        tunnel_type = tunnel.get('tunnel_type', 'local')
        default = '0.0.0.0' if tunnel_type == 'remote' else '127.0.0.1'
        return tunnel.get('remote_host') or default

    def get_tunnel_local_endpoint(self, tunnel):
        """返回本地监听端点。"""
        return self.get_tunnel_local_bind(tunnel), tunnel.get('local_port')

    def local_bind_conflicts(self, bind_a, bind_b):
        """判断两个本地绑定地址是否会在同一端口上冲突。"""
        bind_a = (bind_a or '127.0.0.1').strip()
        bind_b = (bind_b or '127.0.0.1').strip()
        wildcard_binds = {'0.0.0.0', '::', '::0', '[::]', '*'}
        if bind_a == bind_b:
            return True
        if bind_a in wildcard_binds or bind_b in wildcard_binds:
            return True
        return False

    def tunnels_conflict_on_local_port(self, tunnel_a, tunnel_b):
        """判断两条隧道配置是否存在本地监听端口冲突。"""
        # 反向隧道不在本机监听，不参与本地端口冲突检测
        if tunnel_a.get('tunnel_type', 'local') == 'remote':
            return False
        if tunnel_b.get('tunnel_type', 'local') == 'remote':
            return False

        local_bind_a, local_port_a = self.get_tunnel_local_endpoint(tunnel_a)
        local_bind_b, local_port_b = self.get_tunnel_local_endpoint(tunnel_b)

        if not local_port_a or not local_port_b:
            return False

        if int(local_port_a) != int(local_port_b):
            return False

        return self.local_bind_conflicts(local_bind_a, local_bind_b)

    def format_local_port_conflict_message(self, tunnel, conflict_tunnel):
        """格式化本地端口冲突提示。"""
        local_bind, local_port = self.get_tunnel_local_endpoint(tunnel)
        conflict_bind, conflict_port = self.get_tunnel_local_endpoint(conflict_tunnel)
        return (
            f"本地端口 {local_bind}:{local_port} 与隧道 "
            f"'{conflict_tunnel.get('name', '未命名隧道')}' 的监听地址 "
            f"{conflict_bind}:{conflict_port} 冲突"
        )

    def find_local_port_conflict(self, tunnel, exclude_tunnel=None, tunnels=None):
        """查找与当前隧道本地监听端口冲突的其他隧道配置。"""
        tunnel_list = tunnels if tunnels is not None else self.tunnels
        exclude_identity = self._build_tunnel_identity(exclude_tunnel) if exclude_tunnel else None
        current_identity = self._build_tunnel_identity(tunnel)

        for other_tunnel in tunnel_list:
            other_identity = self._build_tunnel_identity(other_tunnel)
            if other_tunnel is tunnel or other_identity == current_identity:
                continue
            if exclude_identity and other_identity == exclude_identity:
                continue
            if self.tunnels_conflict_on_local_port(tunnel, other_tunnel):
                return other_tunnel

        return None

    def check_local_port_available(self, tunnel):
        """检查本地监听端口是否可用。"""
        local_bind, local_port = self.get_tunnel_local_endpoint(tunnel)
        if not local_port:
            return False, "缺少本地监听端口"

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sys.platform == 'win32' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((local_bind, int(local_port)))
        except OSError as e:
            return False, f"本地端口 {local_bind}:{local_port} 已被占用或不可用: {e}"

        return True, None

    def validate_tunnel_ports(self, tunnel, exclude_tunnel=None):
        """校验本地监听端口配置与当前主机端口占用情况。"""
        # 反向隧道监听在远端服务器，无法在本机校验端口
        if tunnel.get('tunnel_type', 'local') == 'remote':
            return True, None

        conflict_tunnel = self.find_local_port_conflict(tunnel, exclude_tunnel=exclude_tunnel)
        if conflict_tunnel:
            return False, self.format_local_port_conflict_message(tunnel, conflict_tunnel)

        available, message = self.check_local_port_available(tunnel)
        if not available:
            return False, message

        return True, None

    def get_tunnel_forward_spec(self, tunnel):
        """获取 ssh -L/-R 所需的转发参数。"""
        local_port = tunnel.get('local_port')
        remote_port = tunnel.get('remote_port')
        if not local_port or not remote_port:
            return None

        tunnel_type = tunnel.get('tunnel_type', 'local')
        if tunnel_type == 'remote':
            # -R remote_host:remote_port:local_bind:local_port
            return (
                f"{self.get_tunnel_remote_host(tunnel)}:"
                f"{remote_port}:"
                f"{self.get_tunnel_local_bind(tunnel)}:"
                f"{local_port}"
            )
        else:
            # -L local_bind:local_port:remote_host:remote_port
            return (
                f"{self.get_tunnel_local_bind(tunnel)}:"
                f"{local_port}:"
                f"{self.get_tunnel_remote_host(tunnel)}:"
                f"{remote_port}"
            )

    def _cmdline_has_option_value(self, cmdline, option, expected_value):
        expected_value = str(expected_value)
        for index, arg in enumerate(cmdline):
            if arg == option and index + 1 < len(cmdline) and cmdline[index + 1] == expected_value:
                return True
            if arg.startswith(option) and arg[len(option):] == expected_value:
                return True
        return False

    def _is_expected_ssh_process(self, process, tunnel):
        """校验 PID 是否仍然对应当前隧道配置的 ssh 进程。"""
        ssh_host = tunnel.get('ssh_host')
        ssh_port = tunnel.get('ssh_port', 22)
        forward_spec = self.get_tunnel_forward_spec(tunnel)

        if not ssh_host or not forward_spec:
            return False

        try:
            if not process.is_running():
                return False

            process_name = process.name().lower()
            cmdline = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return False

        if 'ssh' not in process_name or not cmdline:
            return False

        if ssh_host not in cmdline:
            return False

        if not self._cmdline_has_option_value(cmdline, '-p', ssh_port):
            return False

        tunnel_type = tunnel.get('tunnel_type', 'local')
        flag = '-R' if tunnel_type == 'remote' else '-L'
        if not self._cmdline_has_option_value(cmdline, flag, forward_spec):
            return False

        return True

    def get_tunnel_process(self, tunnel):
        """返回与隧道配置匹配的运行中 ssh 进程。"""
        pid = tunnel.get('pid')
        if not pid:
            return None

        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return None

        if self._is_expected_ssh_process(process, tunnel):
            return process

        return None

    def is_tunnel_running(self, tunnel):
        """判断隧道是否在运行。"""
        return self.get_tunnel_process(tunnel) is not None

    def update_tunnel_pid(self, tunnel_ref, pid, save_config=True):
        """更新指定隧道的 PID"""
        with self.config_lock:
            tunnel = self._find_tunnel_unlocked(tunnel_ref)
            if not tunnel:
                return False

            tunnel['pid'] = pid
            if save_config:
                self.save_config()
            return True

    def clear_tunnel_pid(self, tunnel_ref, save_config=True):
        """清除指定隧道的 PID"""
        with self.config_lock:
            tunnel = self._find_tunnel_unlocked(tunnel_ref)
            if not tunnel:
                return False

            tunnel['pid'] = None
            if save_config:
                self.save_config()
            return True

    def clear_all_pids(self):
        """清除所有隧道的 PID"""
        with self.config_lock:
            for tunnel in self.tunnels:
                tunnel['pid'] = None
            self.save_config()

    def remove_tunnel(self, tunnel_ref, save_config=True):
        """删除指定隧道配置。"""
        with self.config_lock:
            tunnel = self._find_tunnel_unlocked(tunnel_ref)
            if not tunnel:
                return False

            self.tunnels.remove(tunnel)
            if save_config:
                self.save_config()
            return True

    def get_running_tunnels(self):
        """获取所有运行中的隧道信息"""
        running = []
        stale_pid_found = False

        with self.config_lock:
            for tunnel in self.tunnels:
                pid = tunnel.get('pid')
                if not pid:
                    continue

                if self.is_tunnel_running(tunnel):
                    running.append(tunnel)
                else:
                    tunnel['pid'] = None
                    stale_pid_found = True

            if stale_pid_found:
                self.save_config()

        return running

    def stop_tunnel(self, tunnel_ref, save_config=True):
        """安全关闭单个隧道，仅会终止匹配当前配置的 ssh 进程。"""
        with self.config_lock:
            tunnel = self._find_tunnel_unlocked(tunnel_ref)
            if not tunnel:
                return False, "隧道不存在"

            pid = tunnel.get('pid')
            if not pid:
                return False, "未启动"

        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self.clear_tunnel_pid(tunnel_ref, save_config=save_config)
            return False, "进程不存在，已清理"

        if not self._is_expected_ssh_process(process, tunnel):
            self.clear_tunnel_pid(tunnel_ref, save_config=save_config)
            return False, f"PID {pid} 与当前隧道配置不匹配，已清理"

        try:
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            return False, f"关闭失败: {e}"

        self.clear_tunnel_pid(tunnel_ref, save_config=save_config)
        return True, "已关闭"

    def build_ssh_command(self, tunnel):
        """构建 SSH 命令"""
        name = tunnel.get('name', '未命名隧道')
        ssh_host = tunnel.get('ssh_host')
        ssh_port = tunnel.get('ssh_port', 22)
        local_port = tunnel.get('local_port')
        remote_host = self.get_tunnel_remote_host(tunnel)
        remote_port = tunnel.get('remote_port')
        extra_args = (tunnel.get('extra_args') or '').strip()

        # 验证必需参数
        if not ssh_host:
            print(f"❌ 隧道 '{name}' 缺少 ssh_host 参数")
            return None
        if not local_port or not remote_port:
            print(f"❌ 隧道 '{name}' 缺少端口参数")
            return None

        # 构建端口转发参数
        local_bind = self.get_tunnel_local_bind(tunnel)
        tunnel_type = tunnel.get('tunnel_type', 'local')
        if tunnel_type == 'remote':
            # 反向隧道：-R remote_host:remote_port:local_bind:local_port
            forward_arg = f"-R {remote_host}:{remote_port}:{local_bind}:{local_port}"
        else:
            # 本地转发：-L local_bind:local_port:remote_host:remote_port
            forward_arg = f"-L {local_bind}:{local_port}:{remote_host}:{remote_port}"

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
        self._set_last_error(None)
        cmd = self.build_ssh_command(tunnel)

        if not cmd:
            self._set_last_error(f"隧道 '{name}' 配置无效")
            return None

        ports_valid, port_message = self.validate_tunnel_ports(tunnel)
        if not ports_valid:
            self._set_last_error(port_message)
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
                try:
                    stderr_output = process.stderr.read().decode('utf-8', errors='ignore').strip()
                except Exception:
                    stderr_output = ""
                self._set_last_error(stderr_output or f"隧道 '{name}' 启动后立即退出")
                return None

            # 等待SSH隧道建立
            time.sleep(2)

            # 再次检查进程是否还活着
            if process.poll() is not None:
                try:
                    stderr_output = process.stderr.read().decode('utf-8', errors='ignore').strip()
                except Exception:
                    stderr_output = ""
                self._set_last_error(stderr_output or f"隧道 '{name}' 未能保持运行")
                return None

            # 测试隧道连通性（反向隧道监听在远端，跳过本地 TCP 探测）
            if tunnel.get('tunnel_type', 'local') != 'remote':
                if not self.test_tunnel_connectivity(local_port):
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except:
                        process.kill()
                    self._set_last_error(f"本地端口 {self.get_tunnel_local_bind(tunnel)}:{local_port} 未成功建立监听")
                    return None

            return process

        except Exception as e:
            self._set_last_error(str(e))
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
                self.update_tunnel_pid(tunnel, process.pid)
                success_count += 1
                print(f"✅ 隧道已建立 (PID: {process.pid})")
            else:
                print(f"❌ 启动失败")
                if self.get_last_error():
                    print(f"   原因: {self.get_last_error()}")
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
        stale_pid_found = False

        for idx, tunnel in enumerate(self.tunnels, 1):
            pid = tunnel.get('pid')
            name = tunnel.get('name', '未命名隧道')

            # 检查进程是否还在运行
            if pid:
                if self.is_tunnel_running(tunnel):
                    status = "✅ 运行中"
                    active_count += 1
                else:
                    status = "❌ 已停止"
                    tunnel['pid'] = None
                    pid = None
                    stale_pid_found = True
            else:
                status = "⚪ 未启动"

            local_port = tunnel.get('local_port')
            remote_host = self.get_tunnel_remote_host(tunnel)
            remote_port = tunnel.get('remote_port')
            ssh_host = tunnel.get('ssh_host')

            print(f"{idx}. {name} - {status}")
            print(f"   本地端口 {local_port} → {ssh_host} → {remote_host}:{remote_port}")
            if pid:
                print(f"   进程 PID: {pid}")
            print()

        # 保存更新后的配置（清除已停止进程的 PID）
        if stale_pid_found:
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

            print(f"🔌 关闭隧道: {name} (PID: {pid})")
            stopped, message = self.stop_tunnel(tunnel, save_config=False)
            if stopped:
                print("✅ 已关闭")
            elif message.startswith("关闭失败: "):
                print(f"❌ 关闭隧道 '{name}' 时出错: {message.split(': ', 1)[1]}")
            else:
                print(f"⚠️  隧道 '{name}' {message}")

            print()

        self.save_config()

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
