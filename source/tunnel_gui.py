#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SSH 隧道管理工具 - 图形界面版本
支持启动、查看状态、关闭隧道，以及自动重启死掉的进程
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
import json
from PIL import Image, ImageDraw
import pystray

# 导入核心管理器
from tunnel import TunnelManager


class PlaceholderEntry(tk.Entry):
    """带placeholder提示的输入框"""
    def __init__(self, master=None, placeholder="", placeholder_color='grey', **kwargs):
        # 提取textvariable以便特殊处理
        self.text_var = kwargs.pop('textvariable', None)

        super().__init__(master, **kwargs)

        self.placeholder = placeholder
        self.placeholder_color = placeholder_color
        self.default_fg_color = self['fg']
        self.showing_placeholder = False

        # 如果有初始值，显示初始值
        initial_value = self.text_var.get() if self.text_var else ''
        if initial_value:
            self.insert(0, initial_value)
            self['fg'] = self.default_fg_color
        else:
            self._show_placeholder()

        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<KeyRelease>", self._on_key_release)

    def _show_placeholder(self):
        """显示placeholder"""
        self.showing_placeholder = True
        self.delete(0, tk.END)
        super().insert(0, self.placeholder)
        self['fg'] = self.placeholder_color

    def _on_focus_in(self, event):
        """获得焦点时"""
        if self.showing_placeholder:
            self.showing_placeholder = False
            self.delete(0, tk.END)
            self['fg'] = self.default_fg_color

    def _on_focus_out(self, event):
        """失去焦点时"""
        current = super().get()
        if not current:
            self._show_placeholder()
        else:
            # 更新绑定的变量
            if self.text_var:
                self.text_var.set(current)

    def _on_key_release(self, event):
        """按键释放时同步更新变量"""
        if not self.showing_placeholder and self.text_var:
            self.text_var.set(super().get())

    def get(self):
        """获取值，如果是placeholder则返回空字符串"""
        if self.showing_placeholder:
            return ''
        return super().get()


class TunnelGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SSH 隧道管理器")
        self.root.geometry("900x600")
        self.root.resizable(True, True)

        # 设置窗口图标(如果有的话)
        try:
            self.root.iconbitmap("tunnel.ico")
        except:
            pass

        self.manager = TunnelManager()
        self.monitor_thread = None
        self.monitoring = True  # 自动重启默认开启
        self.is_minimized = False  # 是否已最小化到后台
        self.tray_icon = None  # 系统托盘图标

        # 拦截窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.setup_ui()
        self.refresh_status()
        self.start_monitor()  # 启动时自动开启监控
        self.auto_start_tunnels()  # 自动启动标记的隧道

    def setup_ui(self):
        """设置用户界面"""
        # 顶部工具栏
        toolbar = ttk.Frame(self.root, padding="5")
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="启动所有隧道", command=self.start_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="停止所有隧道", command=self.stop_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(toolbar, text="刷新状态", command=self.refresh_status).pack(side=tk.LEFT, padx=5)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        # 开机自启动复选框
        self.auto_start_var = tk.BooleanVar(value=self.is_auto_start_enabled())
        ttk.Checkbutton(
            toolbar,
            text="开机自启动",
            variable=self.auto_start_var,
            command=self.toggle_auto_start
        ).pack(side=tk.LEFT, padx=5)

        # 主内容区域
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 隧道列表
        list_frame = ttk.LabelFrame(main_frame, text="隧道列表", padding="10")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 创建表格
        columns = ("名称", "状态", "本地端口", "远程地址", "PID")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=15)

        for col in columns:
            self.tree.heading(col, text=col)
            if col == "名称":
                self.tree.column(col, width=120)
            elif col == "状态":
                self.tree.column(col, width=80)
            elif col == "本地端口":
                self.tree.column(col, width=80)
            elif col == "远程地址":
                self.tree.column(col, width=300)
            elif col == "PID":
                self.tree.column(col, width=80)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 创建右键菜单
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="启动", command=self.start_selected)
        self.context_menu.add_command(label="停止", command=self.stop_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="编辑", command=self.edit_tunnel)
        self.context_menu.add_command(label="删除", command=self.delete_tunnel)

        # 绑定右键点击事件
        self.tree.bind("<Button-3>", self.show_context_menu)

        # 绑定双击事件
        self.tree.bind("<Double-Button-1>", self.on_double_click)

        # 右侧按钮区
        button_frame = ttk.Frame(main_frame, padding="10")
        button_frame.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Button(button_frame, text="启动选中", command=self.start_selected, width=15).pack(pady=5)
        ttk.Button(button_frame, text="停止选中", command=self.stop_selected, width=15).pack(pady=5)
        ttk.Separator(button_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        ttk.Button(button_frame, text="添加隧道", command=self.add_tunnel, width=15).pack(pady=5)
        ttk.Button(button_frame, text="编辑选中", command=self.edit_tunnel, width=15).pack(pady=5)
        ttk.Button(button_frame, text="删除选中", command=self.delete_tunnel, width=15).pack(pady=5)

        # 底部日志区域
        log_frame = ttk.LabelFrame(self.root, text="操作日志", padding="10")
        log_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=10, pady=10)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        """添加日志"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def refresh_status(self):
        """刷新隧道状态"""
        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            # 加载配置
            self.manager.load_config()

            # 显示所有隧道
            for tunnel in self.manager.tunnels:
                name = tunnel.get('name', '未命名')
                local_port = tunnel.get('local_port')
                remote_host = tunnel.get('remote_host', '127.0.0.1')
                remote_port = tunnel.get('remote_port')
                ssh_host = tunnel.get('ssh_host')
                pid = tunnel.get('pid')

                remote_addr = f"{ssh_host} -> {remote_host}:{remote_port}"

                # 检查进程状态
                if pid:
                    try:
                        import psutil
                        process = psutil.Process(pid)
                        if process.is_running():
                            status = "运行中"
                            tag = "running"
                        else:
                            status = "未启动"
                            tag = "stopped"
                            pid = "-"
                            tunnel['pid'] = None
                    except psutil.NoSuchProcess:
                        status = "未启动"
                        tag = "stopped"
                        pid = "-"
                        tunnel['pid'] = None
                    except Exception:
                        status = "未启动"
                        tag = "stopped"
                        pid = "-"
                        tunnel['pid'] = None
                else:
                    status = "未启动"
                    tag = "stopped"
                    pid = "-"

                item_id = self.tree.insert("", tk.END, values=(name, status, local_port, remote_addr, pid))
                self.tree.tag_configure("running", foreground="green")
                self.tree.tag_configure("stopped", foreground="red")
                self.tree.tag_configure("starting", foreground="orange")
                self.tree.item(item_id, tags=(tag,))

            # 保存更新后的配置
            self.manager.save_config()

            self.log("状态已刷新")

        except Exception as e:
            self.log(f"刷新状态失败: {e}")
            messagebox.showerror("错误", f"刷新状态失败: {e}")

    def start_all(self):
        """启动所有隧道"""
        try:
            self.log("正在启动所有隧道...")

            # 在后台线程中执行
            def start_thread():
                try:
                    self.manager.load_config()

                    # 创建已运行隧道的名称集合
                    running_names = set()
                    for tunnel in self.manager.tunnels:
                        pid = tunnel.get('pid')
                        if pid:
                            try:
                                import psutil
                                process = psutil.Process(pid)
                                if process.is_running():
                                    running_names.add(tunnel.get('name'))
                            except:
                                pass

                    # 找到需要启动的隧道
                    tunnels_to_start = []
                    for tunnel in self.manager.tunnels:
                        name = tunnel.get('name', '未命名隧道')
                        if name in running_names:
                            self.root.after(0, lambda n=name: self.log(f"隧道 {n} 已在运行，跳过"))
                        else:
                            tunnels_to_start.append(tunnel)

                    if not tunnels_to_start:
                        self.root.after(0, lambda: self.log("没有需要启动的隧道"))
                        return

                    # 定义更新状态的函数
                    def update_status_starting(tunnel_name):
                        for item_id in self.tree.get_children():
                            values = self.tree.item(item_id)['values']
                            if values[0] == tunnel_name:
                                self.tree.set(item_id, "状态", "启动中")
                                self.tree.item(item_id, tags=("starting",))
                                break

                    def update_status_running(tunnel_name, pid):
                        for item_id in self.tree.get_children():
                            values = self.tree.item(item_id)['values']
                            if values[0] == tunnel_name:
                                self.tree.set(item_id, "状态", "运行中")
                                self.tree.set(item_id, "PID", str(pid))
                                self.tree.item(item_id, tags=("running",))
                                break

                    def update_status_failed(tunnel_name):
                        for item_id in self.tree.get_children():
                            values = self.tree.item(item_id)['values']
                            if values[0] == tunnel_name:
                                self.tree.set(item_id, "状态", "未启动")
                                self.tree.set(item_id, "PID", "-")
                                self.tree.item(item_id, tags=("stopped",))
                                break

                    # 并行启动所有隧道
                    started_count = 0
                    start_threads = []

                    def start_single_tunnel(tunnel):
                        nonlocal started_count
                        name = tunnel.get('name', '未命名隧道')
                        self.root.after(0, lambda n=name: update_status_starting(n))
                        self.root.after(0, lambda n=name: self.log(f"启动隧道: {n}"))

                        process = self.manager.start_tunnel(tunnel)

                        if process:
                            self.manager.update_tunnel_pid(name, process.pid)
                            started_count += 1
                            self.root.after(0, lambda n=name, p=process.pid: update_status_running(n, p))
                            self.root.after(0, lambda n=name, p=process.pid: self.log(f"隧道 {n} 已启动 (PID: {p})"))
                        else:
                            self.root.after(0, lambda n=name: update_status_failed(n))
                            self.root.after(0, lambda n=name: self.log(f"隧道 {n} 启动失败"))

                    # 为每个隧道创建独立线程
                    for tunnel in tunnels_to_start:
                        t = threading.Thread(target=start_single_tunnel, args=(tunnel,), daemon=True)
                        start_threads.append(t)
                        t.start()

                    # 等待所有线程完成
                    for t in start_threads:
                        t.join()

                    self.root.after(0, lambda: self.log(f"完成：启动 {started_count} 个，跳过 {len(running_names)} 个"))

                except Exception as e:
                    self.root.after(0, lambda: self.log(f"启动失败: {e}"))
                    self.root.after(0, lambda: messagebox.showerror("错误", f"启动失败: {e}"))

            threading.Thread(target=start_thread, daemon=True).start()

        except Exception as e:
            self.log(f"启动失败: {e}")
            messagebox.showerror("错误", f"启动失败: {e}")

    def stop_all(self):
        """停止所有隧道"""
        try:
            self.log("正在停止所有隧道...")

            # 在后台线程中执行
            def stop_thread():
                self.manager.load_config()
                running = self.manager.get_running_tunnels()

                if not running:
                    self.root.after(0, lambda: self.log("没有运行中的隧道"))
                    return

                for tunnel in running:
                    pid = tunnel.get('pid')
                    name = tunnel.get('name')

                    # 找到对应的表格项并显示"正在停止..."
                    def update_status_stopping(tunnel_name):
                        for item_id in self.tree.get_children():
                            values = self.tree.item(item_id)['values']
                            if values[0] == tunnel_name:
                                self.tree.set(item_id, "状态", "正在停止...")
                                break

                    self.root.after(0, lambda n=name: update_status_stopping(n))

                    try:
                        import psutil
                        process = psutil.Process(pid)
                        if process.is_running():
                            self.root.after(0, lambda n=name, p=pid: self.log(f"关闭隧道: {n} (PID: {p})"))
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except psutil.TimeoutExpired:
                                process.kill()
                            self.manager.clear_tunnel_pid(name)
                            self.root.after(0, lambda n=name: self.log(f"隧道 {n} 已关闭"))
                        else:
                            self.manager.clear_tunnel_pid(name)
                            self.root.after(0, lambda n=name: self.log(f"隧道 {n} 已经停止"))
                    except psutil.NoSuchProcess:
                        self.manager.clear_tunnel_pid(name)
                        self.root.after(0, lambda n=name: self.log(f"隧道 {n} 进程已不存在"))
                    except Exception as e:
                        self.root.after(0, lambda n=name, err=e: self.log(f"关闭隧道 {n} 时出错: {err}"))

                self.root.after(0, lambda: self.log("所有隧道已关闭"))
                self.root.after(0, self.refresh_status)

            threading.Thread(target=stop_thread, daemon=True).start()

        except Exception as e:
            self.log(f"停止失败: {e}")
            messagebox.showerror("错误", f"停止失败: {e}")

    def start_selected(self):
        """启动选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要启动的隧道")
            return

        # 在后台线程中执行启动
        def start_thread():
            self.manager.load_config()

            # 找到需要启动的隧道
            tunnels_to_start = []
            for item_id in selection:
                values = self.tree.item(item_id)['values']
                name = values[0]

                # 查找对应的隧道配置
                tunnel = next((t for t in self.manager.tunnels if t.get('name') == name), None)
                if tunnel:
                    pid = tunnel.get('pid')
                    # 如果已经在运行，跳过
                    if pid:
                        try:
                            import psutil
                            process = psutil.Process(pid)
                            if process.is_running():
                                self.root.after(0, lambda n=name: self.log(f"隧道 {n} 已在运行，跳过"))
                                continue
                        except:
                            pass
                    tunnels_to_start.append((item_id, tunnel))

            if not tunnels_to_start:
                return

            # 并行启动所有选中的隧道
            started_count = 0
            start_threads = []

            def start_single_tunnel(item_id, tunnel):
                nonlocal started_count
                name = tunnel.get('name', '未命名隧道')
                self.root.after(0, lambda i=item_id: self.tree.set(i, "状态", "启动中"))
                self.root.after(0, lambda i=item_id: self.tree.item(i, tags=("starting",)))
                self.root.after(0, lambda n=name: self.log(f"正在启动隧道: {n}"))

                process = self.manager.start_tunnel(tunnel)

                if process:
                    self.manager.update_tunnel_pid(name, process.pid)
                    started_count += 1
                    self.root.after(0, lambda i=item_id: self.tree.set(i, "状态", "运行中"))
                    self.root.after(0, lambda i=item_id, p=process.pid: self.tree.set(i, "PID", str(p)))
                    self.root.after(0, lambda i=item_id: self.tree.item(i, tags=("running",)))
                    self.root.after(0, lambda n=name, p=process.pid: self.log(f"隧道 {n} 已启动 (PID: {p})"))
                else:
                    self.root.after(0, lambda i=item_id: self.tree.set(i, "状态", "未启动"))
                    self.root.after(0, lambda i=item_id: self.tree.set(i, "PID", "-"))
                    self.root.after(0, lambda i=item_id: self.tree.item(i, tags=("stopped",)))
                    self.root.after(0, lambda n=name: self.log(f"隧道 {n} 启动失败"))

            # 为每个隧道创建独立线程
            for item_id, tunnel in tunnels_to_start:
                t = threading.Thread(target=start_single_tunnel, args=(item_id, tunnel), daemon=True)
                start_threads.append(t)
                t.start()

            # 等待所有线程完成
            for t in start_threads:
                t.join()

        threading.Thread(target=start_thread, daemon=True).start()

    def stop_selected(self):
        """停止选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要停止的隧道")
            return

        self.manager.load_config()

        for item_id in selection:
            values = self.tree.item(item_id)['values']
            name = values[0]

            # 查找对应的隧道配置
            tunnel = next((t for t in self.manager.tunnels if t.get('name') == name), None)
            if tunnel:
                pid = tunnel.get('pid')
                if pid:
                    try:
                        import psutil
                        process = psutil.Process(pid)
                        if process.is_running():
                            process.terminate()
                            process.wait(timeout=3)
                            self.manager.clear_tunnel_pid(name)
                            self.log(f"隧道 {name} 已关闭")
                        else:
                            self.manager.clear_tunnel_pid(name)
                            self.log(f"隧道 {name} 已经关闭")
                    except psutil.NoSuchProcess:
                        self.manager.clear_tunnel_pid(name)
                        self.log(f"隧道 {name} 进程已不存在，已清理")
                    except Exception as e:
                        self.log(f"停止隧道 {name} 失败: {e}")

        self.refresh_status()

    def show_logs(self):
        """显示详细日志"""
        messagebox.showinfo("日志", "日志功能待实现")

    def add_tunnel(self):
        """添加新隧道"""
        dialog = TunnelEditDialog(self.root, "添加隧道")
        if dialog.result:
            self.manager.tunnels.append(dialog.result)
            self.manager.save_config()
            self.log(f"已添加隧道: {dialog.result['name']}")
            self.refresh_status()

    def edit_tunnel(self):
        """编辑选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要编辑的隧道")
            return

        if len(selection) > 1:
            messagebox.showwarning("提示", "一次只能编辑一个隧道")
            return

        values = self.tree.item(selection[0])['values']
        name = values[0]
        status = values[1]

        # 查找对应的隧道配置
        tunnel = next((t for t in self.manager.tunnels if t.get('name') == name), None)
        if tunnel:
            dialog = TunnelEditDialog(self.root, "编辑隧道", tunnel)
            if dialog.result:
                # 检查隧道是否正在运行
                was_running = (status == "运行中")
                old_name = name

                # 如果隧道正在运行，先停止它
                if was_running:
                    self.log(f"隧道 {old_name} 正在运行，先停止...")
                    pid = tunnel.get('pid')
                    if pid:
                        try:
                            import psutil
                            process = psutil.Process(pid)
                            if process.is_running():
                                process.terminate()
                                process.wait(timeout=3)
                                self.log(f"隧道 {old_name} 已关闭")
                            self.manager.clear_tunnel_pid(old_name)
                        except Exception as e:
                            self.log(f"停止隧道失败: {e}")

                # 更新隧道配置
                idx = self.manager.tunnels.index(tunnel)
                self.manager.tunnels[idx] = dialog.result
                self.manager.save_config()
                self.log(f"已更新隧道配置: {dialog.result['name']}")

                # 如果之前在运行，重新启动
                if was_running:
                    self.log(f"正在重启隧道: {dialog.result['name']}")
                    process = self.manager.start_tunnel(dialog.result)
                    if process:
                        self.manager.update_tunnel_pid(dialog.result['name'], process.pid)
                        self.log(f"隧道 {dialog.result['name']} 已重启 (PID: {process.pid})")
                    else:
                        self.log(f"隧道 {dialog.result['name']} 重启失败")

                self.refresh_status()

    def delete_tunnel(self):
        """删除选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的隧道")
            return

        names = [self.tree.item(item)['values'][0] for item in selection]

        if not messagebox.askyesno("确认删除", f"确定要删除 {len(names)} 个隧道吗？\n{', '.join(names)}"):
            return

        # 删除隧道
        for name in names:
            tunnel = next((t for t in self.manager.tunnels if t.get('name') == name), None)
            if tunnel:
                self.manager.tunnels.remove(tunnel)
                self.log(f"已删除隧道: {name}")

        self.manager.save_config()
        self.refresh_status()

    def show_context_menu(self, event):
        """显示右键菜单"""
        # 选中右键点击的项
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def on_double_click(self, event):
        """双击表格行时的处理"""
        item = self.tree.identify_row(event.y)
        if not item:
            return

        # 获取选中行的信息
        values = self.tree.item(item)['values']
        if not values:
            return

        name = values[0]
        status = values[1]

        # 如果未启动，则启动
        if status == "未启动":
            self.tree.selection_set(item)
            self.start_selected()

    def show_settings(self):
        """显示设置对话框"""
        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        # 开机自启动选项
        auto_start_var = tk.BooleanVar(value=self.is_auto_start_enabled())
        ttk.Checkbutton(
            frame,
            text="开机自动启动（后台运行）",
            variable=auto_start_var
        ).pack(anchor=tk.W, pady=10)

        ttk.Label(
            frame,
            text="启用后，程序将在系统启动时自动运行并最小化到托盘",
            font=("", 9),
            foreground="gray"
        ).pack(anchor=tk.W, padx=20)

        # 按钮
        button_frame = ttk.Frame(frame)
        button_frame.pack(pady=20)

        def save_settings():
            if auto_start_var.get():
                self.enable_auto_start()
            else:
                self.disable_auto_start()
            dialog.destroy()
            self.log("设置已保存")

        ttk.Button(button_frame, text="保存", command=save_settings, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=5)

        dialog.wait_window()

    def toggle_auto_start(self):
        """切换开机自启动状态"""
        if self.auto_start_var.get():
            # 用户勾选了，确认是否启用
            if messagebox.askyesno("确认", "是否启用开机自启动？\n\n程序将在系统启动时自动运行并最小化到托盘。"):
                self.enable_auto_start()
            else:
                # 用户取消，恢复复选框状态
                self.auto_start_var.set(False)
        else:
            # 用户取消勾选，确认是否禁用
            if messagebox.askyesno("确认", "是否禁用开机自启动？"):
                self.disable_auto_start()
            else:
                # 用户取消，恢复复选框状态
                self.auto_start_var.set(True)

    def is_auto_start_enabled(self):
        """检查是否启用了开机自启动"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ
            )
            try:
                winreg.QueryValueEx(key, "SSH隧道管理器")
                winreg.CloseKey(key)
                return True
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except:
            return False

    def enable_auto_start(self):
        """启用开机自启动"""
        try:
            import winreg
            # 获取当前程序路径
            if getattr(sys, 'frozen', False):
                # 打包后的exe路径
                app_path = sys.executable
            else:
                # 开发环境，使用python脚本
                app_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'

            # 添加 --minimized 参数，让程序启动时最小化到托盘
            app_path = f'{app_path} --minimized'

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_WRITE
            )
            winreg.SetValueEx(key, "SSH隧道管理器", 0, winreg.REG_SZ, app_path)
            winreg.CloseKey(key)
            self.log("已启用开机自启动")
        except Exception as e:
            self.log(f"启用开机自启动失败: {e}")
            messagebox.showerror("错误", f"启用开机自启动失败: {e}")

    def disable_auto_start(self):
        """禁用开机自启动"""
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_WRITE
            )
            try:
                winreg.DeleteValue(key, "SSH隧道管理器")
                self.log("已禁用开机自启动")
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except Exception as e:
            self.log(f"禁用开机自启动失败: {e}")
            messagebox.showerror("错误", f"禁用开机自启动失败: {e}")

    def create_tray_icon(self):
        """创建系统托盘图标"""
        # 创建一个简单的图标
        image = Image.new('RGB', (64, 64), color='blue')
        draw = ImageDraw.Draw(image)
        draw.rectangle([16, 16, 48, 48], fill='white')

        # 创建托盘菜单
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", self.show_window, default=True),
            pystray.MenuItem("退出", self.quit_app)
        )

        # 创建托盘图标，单击显示窗口
        self.tray_icon = pystray.Icon(
            "SSH隧道管理器",
            image,
            "SSH隧道管理器 - 点击显示窗口",
            menu
        )

        # 在后台线程中运行托盘图标
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self):
        """显示窗口"""
        self.root.after(0, self._show_window_impl)

    def _show_window_impl(self):
        """显示窗口的实现（在主线程中执行）"""
        self.root.deiconify()  # 恢复窗口
        self.root.lift()  # 置顶
        self.root.focus_force()  # 获取焦点
        self.is_minimized = False
        self.log("窗口已恢复")

    def quit_app(self):
        """退出应用"""
        self.root.after(0, self._quit_app_impl)

    def _quit_app_impl(self):
        """退出应用的实现（在主线程中执行）"""
        self.log("正在关闭所有隧道...")
        self.monitoring = False  # 停止监控

        # 更新隧道的auto_start状态
        self.update_auto_start_status()

        try:
            self.manager.cmd_stop()  # 关闭所有隧道
        except:
            pass
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    def update_auto_start_status(self):
        """更新所有隧道的auto_start状态"""
        try:
            self.manager.load_config()
            running_names = set()

            # 获取当前运行的隧道名称
            for tunnel in self.manager.tunnels:
                pid = tunnel.get('pid')
                if pid:
                    try:
                        import psutil
                        process = psutil.Process(pid)
                        if process.is_running():
                            running_names.add(tunnel.get('name'))
                    except:
                        pass

            # 更新配置中的auto_start字段
            for tunnel in self.manager.tunnels:
                name = tunnel.get('name', '')
                tunnel['auto_start'] = (name in running_names)

            # 保存配置
            self.manager.save_config()

            if running_names:
                self.log(f"已保存 {len(running_names)} 个隧道的自动启动状态")
        except Exception as e:
            self.log(f"保存自动启动状态失败: {e}")

    def auto_start_tunnels(self):
        """自动启动标记为auto_start的隧道"""
        try:
            self.manager.load_config()
            auto_start_tunnels = [t for t in self.manager.tunnels if t.get('auto_start', False)]

            if not auto_start_tunnels:
                return

            self.log(f"检测到 {len(auto_start_tunnels)} 个隧道需要自动启动...")

            # 在后台线程中启动隧道
            def start_thread():
                # 定义更新状态的函数
                def update_status_starting(tunnel_name):
                    for item_id in self.tree.get_children():
                        values = self.tree.item(item_id)['values']
                        if values[0] == tunnel_name:
                            self.tree.set(item_id, "状态", "启动中")
                            self.tree.item(item_id, tags=("starting",))
                            break

                def update_status_running(tunnel_name, pid):
                    for item_id in self.tree.get_children():
                        values = self.tree.item(item_id)['values']
                        if values[0] == tunnel_name:
                            self.tree.set(item_id, "状态", "运行中")
                            self.tree.set(item_id, "PID", str(pid))
                            self.tree.item(item_id, tags=("running",))
                            break

                def update_status_failed(tunnel_name):
                    for item_id in self.tree.get_children():
                        values = self.tree.item(item_id)['values']
                        if values[0] == tunnel_name:
                            self.tree.set(item_id, "状态", "未启动")
                            self.tree.set(item_id, "PID", "-")
                            self.tree.item(item_id, tags=("stopped",))
                            break

                # 并行启动所有隧道
                started_count = 0
                start_threads = []

                def start_single_tunnel(tunnel):
                    nonlocal started_count
                    name = tunnel.get('name', '未命名隧道')
                    self.root.after(0, lambda n=name: update_status_starting(n))
                    self.root.after(0, lambda n=name: self.log(f"自动启动隧道: {n}"))

                    process = self.manager.start_tunnel(tunnel)

                    if process:
                        self.manager.update_tunnel_pid(name, process.pid)
                        started_count += 1
                        self.root.after(0, lambda n=name, p=process.pid: update_status_running(n, p))
                        self.root.after(0, lambda n=name, p=process.pid: self.log(f"隧道 {n} 已启动 (PID: {p})"))
                    else:
                        self.root.after(0, lambda n=name: update_status_failed(n))
                        self.root.after(0, lambda n=name: self.log(f"隧道 {n} 启动失败"))

                # 为每个隧道创建独立线程
                for tunnel in auto_start_tunnels:
                    t = threading.Thread(target=start_single_tunnel, args=(tunnel,), daemon=True)
                    start_threads.append(t)
                    t.start()

                # 等待所有线程完成
                for t in start_threads:
                    t.join()

                self.root.after(0, lambda: self.log(f"自动启动完成：成功 {started_count} 个"))

            threading.Thread(target=start_thread, daemon=True).start()

        except Exception as e:
            self.log(f"自动启动隧道失败: {e}")

    def on_closing(self):
        """窗口关闭时的处理"""
        # 创建自定义对话框
        dialog = tk.Toplevel(self.root)
        dialog.title("关闭选项")
        dialog.geometry("350x120")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (dialog.winfo_width() // 2)
        y = (dialog.winfo_screenheight() // 2) - (dialog.winfo_height() // 2)
        dialog.geometry(f"+{x}+{y}")

        # 提示文本
        frame = ttk.Frame(dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="请选择关闭方式", font=("", 10)).pack(pady=(0, 15))

        # 按钮
        button_frame = ttk.Frame(frame)
        button_frame.pack()

        def minimize_to_background():
            """最小化到后台"""
            dialog.destroy()
            self.root.withdraw()  # 隐藏窗口
            self.is_minimized = True

            # 创建系统托盘图标（如果还没有）
            if not self.tray_icon:
                self.create_tray_icon()

            self.log("程序已最小化到后台运行，隧道继续工作")

        def close_completely():
            """完全关闭"""
            dialog.destroy()
            self.log("正在关闭所有隧道...")
            self.monitoring = False  # 停止监控

            # 更新隧道的auto_start状态
            self.update_auto_start_status()

            try:
                self.manager.cmd_stop()  # 关闭所有隧道
            except:
                pass
            if self.tray_icon:
                self.tray_icon.stop()
            self.root.destroy()  # 关闭窗口

        ttk.Button(
            button_frame,
            text="后台运行",
            command=minimize_to_background,
            width=15
        ).pack(side=tk.LEFT, padx=5)

        ttk.Button(
            button_frame,
            text="直接关闭",
            command=close_completely,
            width=15
        ).pack(side=tk.LEFT, padx=5)

        dialog.wait_window()

    def start_monitor(self):
        """启动监控线程"""
        if not self.monitoring:
            self.monitoring = True
            self.monitor_thread = threading.Thread(target=self.monitor_tunnels, daemon=True)
            self.monitor_thread.start()
            self.log("已启动自动重启监控")

    def stop_monitor(self):
        """停止监控线程"""
        self.monitoring = False
        self.log("已关闭自动重启监控")

    def monitor_tunnels(self):
        """监控隧道进程,自动重启死掉的进程"""
        while self.monitoring:
            try:
                self.manager.load_config()

                for tunnel in self.manager.tunnels:
                    name = tunnel.get('name')
                    pid = tunnel.get('pid')

                    if not pid:
                        continue

                    try:
                        import psutil
                        process = psutil.Process(pid)
                        if not process.is_running():
                            # 进程死掉了,尝试重启
                            self.root.after(0, lambda n=name: self.log(f"检测到隧道 {n} 已关闭,正在重启..."))
                            new_process = self.manager.start_tunnel(tunnel)
                            if new_process:
                                self.manager.update_tunnel_pid(name, new_process.pid)
                                self.root.after(0, lambda n=name, p=new_process.pid: self.log(f"隧道 {n} 已重启 (PID: {p})"))
                                self.root.after(0, self.refresh_status)
                            else:
                                self.root.after(0, lambda n=name: self.log(f"隧道 {n} 重启失败"))
                    except psutil.NoSuchProcess:
                        # 进程不存在,尝试重启
                        self.root.after(0, lambda n=name: self.log(f"检测到隧道 {n} 进程不存在,正在重启..."))
                        new_process = self.manager.start_tunnel(tunnel)
                        if new_process:
                            self.manager.update_tunnel_pid(name, new_process.pid)
                            self.root.after(0, lambda n=name, p=new_process.pid: self.log(f"隧道 {n} 已重启 (PID: {p})"))
                            self.root.after(0, self.refresh_status)
                        else:
                            self.root.after(0, lambda n=name: self.log(f"隧道 {n} 重启失败"))
                    except:
                        pass

            except Exception as e:
                pass

            time.sleep(10)  # 每10秒检查一次


class TunnelEditDialog:
    """隧道编辑对话框"""
    def __init__(self, parent, title, tunnel=None):
        self.result = None
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("500x400")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 创建表单
        frame = ttk.Frame(self.dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        # 名称
        ttk.Label(frame, text="名称:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.name_var = tk.StringVar(value=tunnel.get('name', '') if tunnel else '')
        name_entry = PlaceholderEntry(frame, textvariable=self.name_var, placeholder="例如: 生产环境MySQL", width=40)
        name_entry.grid(row=0, column=1, pady=5)

        # SSH主机
        ttk.Label(frame, text="SSH主机:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.ssh_host_var = tk.StringVar(value=tunnel.get('ssh_host', '') if tunnel else '')
        ssh_host_entry = PlaceholderEntry(frame, textvariable=self.ssh_host_var, placeholder="例如: user@server.com", width=40)
        ssh_host_entry.grid(row=1, column=1, pady=5)

        # SSH端口
        ttk.Label(frame, text="SSH端口:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.ssh_port_var = tk.StringVar(value=str(tunnel.get('ssh_port', 22)) if tunnel else '22')
        ssh_port_entry = PlaceholderEntry(frame, textvariable=self.ssh_port_var, placeholder="默认: 22", width=40)
        ssh_port_entry.grid(row=2, column=1, pady=5)

        # 本地端口
        ttk.Label(frame, text="本地端口:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.local_port_var = tk.StringVar(value=str(tunnel.get('local_port', '')) if tunnel else '')
        local_port_entry = PlaceholderEntry(frame, textvariable=self.local_port_var, placeholder="你电脑上监听的端口，例如: 13306", width=40)
        local_port_entry.grid(row=3, column=1, pady=5)

        # 远程主机
        ttk.Label(frame, text="远程主机:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.remote_host_var = tk.StringVar(value=tunnel.get('remote_host', '127.0.0.1') if tunnel else '127.0.0.1')
        remote_host_entry = PlaceholderEntry(frame, textvariable=self.remote_host_var, placeholder="默认: 127.0.0.1", width=40)
        remote_host_entry.grid(row=4, column=1, pady=5)

        # 远程端口
        ttk.Label(frame, text="远程端口:").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.remote_port_var = tk.StringVar(value=str(tunnel.get('remote_port', '')) if tunnel else '')
        remote_port_entry = PlaceholderEntry(frame, textvariable=self.remote_port_var, placeholder="目标服务的端口，例如: 3306", width=40)
        remote_port_entry.grid(row=5, column=1, pady=5)

        # 按钮
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=20)
        ttk.Button(button_frame, text="确定", command=self.ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=self.cancel).pack(side=tk.LEFT, padx=5)

        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")

        self.dialog.wait_window()

    def ok(self):
        """确定按钮"""
        # 验证必填字段
        if not self.name_var.get().strip():
            messagebox.showerror("错误", "名称不能为空")
            return
        if not self.ssh_host_var.get().strip():
            messagebox.showerror("错误", "SSH主机不能为空")
            return
        if not self.local_port_var.get().strip():
            messagebox.showerror("错误", "本地端口不能为空")
            return
        if not self.remote_port_var.get().strip():
            messagebox.showerror("错误", "远程端口不能为空")
            return

        # 验证端口号
        try:
            ssh_port = int(self.ssh_port_var.get())
            local_port = int(self.local_port_var.get())
            remote_port = int(self.remote_port_var.get())
            if not (1 <= ssh_port <= 65535 and 1 <= local_port <= 65535 and 1 <= remote_port <= 65535):
                raise ValueError()
        except ValueError:
            messagebox.showerror("错误", "端口号必须是1-65535之间的整数")
            return

        # 构建结果
        self.result = {
            'name': self.name_var.get().strip(),
            'ssh_host': self.ssh_host_var.get().strip(),
            'ssh_port': ssh_port,
            'local_port': local_port,
            'remote_host': self.remote_host_var.get().strip(),
            'remote_port': remote_port,
        }

        self.dialog.destroy()

    def cancel(self):
        """取消按钮"""
        self.dialog.destroy()


def main():
    try:
        # 检查是否有 --minimized 参数
        minimized = '--minimized' in sys.argv

        root = tk.Tk()
        app = TunnelGUI(root)

        # 如果是最小化启动，直接隐藏窗口并创建托盘图标
        if minimized:
            root.withdraw()
            app.is_minimized = True
            app.create_tray_icon()
            app.log("程序已在后台启动")

        root.mainloop()
    except Exception as e:
        import traceback
        error_msg = f"GUI启动失败:\n{e}\n\n{traceback.format_exc()}"
        try:
            messagebox.showerror("错误", error_msg)
        except:
            print(error_msg)
            input("\n按回车键退出...")


if __name__ == "__main__":
    main()
