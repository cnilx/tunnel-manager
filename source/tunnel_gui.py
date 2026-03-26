#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SSH 隧道管理工具 - 图形界面版本
支持启动、查看状态、关闭隧道，以及自动重启死掉的进程
"""

import os
import atexit
import re
import signal
import sys
import threading
import time
import socket
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path
import json
from PIL import Image, ImageDraw
import pystray

# 导入核心管理器
from tunnel import TunnelManager


ALL_TAG_FILTER = "全部标签"
UNTAGGED_FILTER = "未设置标签"
ALL_TYPE_FILTER = "全部类型"
STOP_LIKE_STATUSES = {"运行中", "重启中", "启动中"}
APP_NAME = "SSH 隧道管理器"
APP_VERSION = "1.0.4"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"


def normalize_tags(tags_value):
    """将标签输入规范化为去重后的字符串列表。"""
    if isinstance(tags_value, str):
        raw_tags = re.split(r"[,，;；\n]+", tags_value)
    elif isinstance(tags_value, (list, tuple, set)):
        raw_tags = list(tags_value)
    else:
        raw_tags = []

    normalized_tags = []
    for tag in raw_tags:
        if tag is None:
            continue

        tag_text = str(tag).strip()
        if tag_text and tag_text not in normalized_tags:
            normalized_tags.append(tag_text)

    return normalized_tags


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
        self.root.title(APP_TITLE)
        self.root.geometry("1040x660")
        self.root.minsize(920, 620)
        self.root.resizable(True, True)

        # 设置窗口图标(如果有的话)
        try:
            self.root.iconbitmap("tunnel.ico")
        except:
            pass

        self.manager = TunnelManager()
        self.monitor_thread = None
        self.monitoring = False  # 由 start_monitor() 负责设置为 True 并创建线程
        self._tunnel_lock = threading.Lock()  # 防止监控线程与手动操作竞态
        self._restarting_tunnels = set()  # 正在重启/重连中的隧道稳定标识
        self.is_minimized = False  # 是否已最小化到后台
        self.tray_icon = None  # 系统托盘图标
        self.tag_filter_var = tk.StringVar(value=ALL_TAG_FILTER)
        self.type_filter_var = tk.StringVar(value=ALL_TYPE_FILTER)
        self._hovered_action = None
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False

        # 拦截窗口关闭事件
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._register_exit_handlers()

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

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(toolbar, text="标签筛选").pack(side=tk.LEFT, padx=(0, 5))
        self.tag_filter_combo = ttk.Combobox(
            toolbar,
            textvariable=self.tag_filter_var,
            state="readonly",
            width=18,
            values=[ALL_TAG_FILTER]
        )
        self.tag_filter_combo.pack(side=tk.LEFT, padx=5)
        self.tag_filter_combo.bind("<<ComboboxSelected>>", self.on_tag_filter_changed)

        ttk.Label(toolbar, text="类型筛选").pack(side=tk.LEFT, padx=(10, 5))
        self.type_filter_combo = ttk.Combobox(
            toolbar,
            textvariable=self.type_filter_var,
            state="readonly",
            width=10,
            values=[ALL_TYPE_FILTER, "本地转发", "反向转发"]
        )
        self.type_filter_combo.pack(side=tk.LEFT, padx=5)
        self.type_filter_combo.bind("<<ComboboxSelected>>", self.on_type_filter_changed)

        # 主内容区域
        main_frame = ttk.Frame(self.root, padding=(10, 6, 10, 0))
        main_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 隧道列表区域
        list_frame = ttk.Frame(main_frame, padding="10")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        header_frame = ttk.Frame(list_frame)
        header_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))
        ttk.Label(header_frame, text="隧道列表", font=("", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(header_frame, text="添加隧道", command=self.add_tunnel, width=12).pack(side=tk.RIGHT)

        table_frame = ttk.Frame(list_frame)
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        # 创建表格
        columns = ("名称", "状态", "标签", "绑定地址", "本地端口", "远程地址", "PID", "操作")
        self.style = ttk.Style(self.root)
        self.style.configure("TunnelManager.Treeview", rowheight=26)
        self.style.map(
            "TunnelManager.Treeview",
            background=[("selected", "#e9eef5")],
            foreground=[("selected", "#243447")],
            fieldbackground=[("selected", "#e9eef5")]
        )
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            height=13,
            style="TunnelManager.Treeview",
            selectmode="extended"
        )
        self.tree_font = tkfont.nametofont("TkDefaultFont")
        self.operation_separator = " "
        self.operation_column_width = max(
            self.tree_font.measure(self._get_operation_text(actions))
            for actions in (
                ("启动", "编辑", "删除"),
                ("停止", "编辑", "删除"),
                ("编辑", "删除"),
            )
        ) + 24

        for col in columns:
            self.tree.heading(col, text=col)
            if col == "名称":
                self.tree.column(col, width=120, stretch=False)
            elif col == "状态":
                self.tree.column(col, width=78, stretch=False)
            elif col == "标签":
                self.tree.column(col, width=110, stretch=False)
            elif col == "绑定地址":
                self.tree.column(col, width=90, stretch=False)
            elif col == "本地端口":
                self.tree.column(col, width=76, stretch=False)
            elif col == "远程地址":
                self.tree.column(col, width=190, stretch=True)
            elif col == "PID":
                self.tree.column(col, width=78, stretch=False)
            elif col == "操作":
                self.tree.column(col, width=self.operation_column_width, anchor=tk.CENTER, stretch=False)

        self.tree.tag_configure("running", foreground="green")
        self.tree.tag_configure("stopped", foreground="red")
        self.tree.tag_configure("starting", foreground="orange")

        scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar_y.set)

        self.tree.grid(row=0, column=0, sticky=tk.NSEW)
        scrollbar_y.grid(row=0, column=1, sticky=tk.NS)

        # 创建右键菜单
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(label="浏览器中打开", command=self.open_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="启动", command=self.start_selected)
        self.context_menu.add_command(label="停止", command=self.stop_selected)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="编辑", command=self.edit_tunnel)
        self.context_menu.add_command(label="删除", command=self.delete_tunnel)

        # 绑定右键点击事件
        self.tree.bind("<Button-3>", self.show_context_menu)
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Motion>", self.on_tree_motion)
        self.tree.bind("<Leave>", self.on_tree_leave)
        self.tree.bind("<Delete>", self.on_delete_key)

        # 绑定双击事件
        self.tree.bind("<Double-Button-1>", self.on_double_click)

        # 底部日志区域
        log_frame = ttk.LabelFrame(self.root, text="操作日志", padding="10")
        log_frame.pack(side=tk.BOTTOM, fill=tk.BOTH, expand=False, padx=10, pady=(6, 10))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=6, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def log(self, message):
        """添加日志"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def log_async(self, message):
        """从后台线程安全写日志。"""
        self.root.after(0, lambda msg=message: self.log(msg))

    def _root_exists(self):
        """判断根窗口是否仍然存在。"""
        try:
            return bool(self.root and self.root.winfo_exists())
        except Exception:
            return False

    def _register_exit_handlers(self):
        """注册异常退出时的清理钩子。"""
        atexit.register(self._handle_process_exit)

        for signal_name in ("SIGINT", "SIGTERM"):
            signum = getattr(signal, signal_name, None)
            if signum is None:
                continue
            try:
                signal.signal(signum, self._handle_signal_exit)
            except (ValueError, OSError):
                continue

    def _handle_process_exit(self):
        """进程退出时尽力执行一次清理逻辑。"""
        self._run_shutdown_cleanup(emit_log=False)

    def _handle_signal_exit(self, signum, frame):
        """收到退出信号时执行统一清理。"""
        try:
            if self._root_exists():
                self.root.after(0, lambda: self.shutdown_application(f"收到信号 {signum}", emit_log=False))
            else:
                self._run_shutdown_cleanup(emit_log=False)
        except Exception:
            self._run_shutdown_cleanup(emit_log=False)
        raise SystemExit(0)

    def _run_shutdown_cleanup(self, emit_log=True):
        """执行一次性的退出清理逻辑。"""
        with self._shutdown_lock:
            if self._shutdown_started:
                return False
            self._shutdown_started = True

        self.monitoring = False

        if emit_log and self._root_exists():
            try:
                self.log("正在关闭所有隧道...")
            except Exception:
                pass

        try:
            self.update_auto_start_status(emit_log=emit_log and self._root_exists())
        except Exception:
            pass

        try:
            self.manager.cmd_stop()
        except Exception:
            pass

        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass

        return True

    def shutdown_application(self, reason=None, emit_log=True):
        """统一的应用退出入口。"""
        self._run_shutdown_cleanup(emit_log=emit_log)

        try:
            if self._root_exists():
                self.root.destroy()
        except Exception:
            pass

    def _get_tunnel_tags(self, tunnel):
        """获取标准化后的标签列表。"""
        return normalize_tags(tunnel.get('tags', []))

    def _get_known_ssh_hosts(self, exclude_tunnel=None):
        """获取已配置过的 SSH 主机列表。"""
        ssh_hosts = []
        for tunnel in self.manager.tunnels:
            if exclude_tunnel is not None and tunnel is exclude_tunnel:
                continue

            ssh_host = str(tunnel.get('ssh_host') or '').strip()
            if ssh_host and ssh_host not in ssh_hosts:
                ssh_hosts.append(ssh_host)

        return ssh_hosts

    def _get_known_tags(self, exclude_tunnel=None):
        """获取已配置过的标签列表。"""
        tags = []
        for tunnel in self.manager.tunnels:
            if exclude_tunnel is not None and tunnel is exclude_tunnel:
                continue

            for tag in self._get_tunnel_tags(tunnel):
                if tag not in tags:
                    tags.append(tag)

        return tags

    def _validate_tunnel_config_ports(self, tunnel_data, exclude_tunnel=None):
        """校验隧道配置中的本地监听端口是否与现有配置冲突。"""
        # 反向隧道监听在远端服务器，跳过本地端口冲突检测
        if tunnel_data.get('tunnel_type', 'local') == 'remote':
            return True, None
        conflict_tunnel = self.manager.find_local_port_conflict(tunnel_data, exclude_tunnel=exclude_tunnel)
        if conflict_tunnel:
            return False, self.manager.format_local_port_conflict_message(tunnel_data, conflict_tunnel)
        return True, None

    def _format_tunnel_tags(self, tunnel):
        """将标签列表格式化为界面显示文本。"""
        tags = self._get_tunnel_tags(tunnel)
        return "、".join(tags) if tags else "-"

    def _build_tag_filter_values(self, tunnels):
        """根据当前配置生成标签筛选选项。"""
        tag_values = []
        has_untagged = False

        for tunnel in tunnels:
            tags = self._get_tunnel_tags(tunnel)
            if tags:
                for tag in tags:
                    if tag not in tag_values:
                        tag_values.append(tag)
            else:
                has_untagged = True

        filter_values = [ALL_TAG_FILTER]
        filter_values.extend(tag_values)
        if has_untagged:
            filter_values.append(UNTAGGED_FILTER)
        return filter_values

    def _matches_tag_filter(self, tunnel, selected_filter):
        """判断隧道是否匹配当前标签筛选。"""
        if not selected_filter or selected_filter == ALL_TAG_FILTER:
            return True

        tags = self._get_tunnel_tags(tunnel)
        if selected_filter == UNTAGGED_FILTER:
            return not tags

        return selected_filter in tags

    def _get_current_tag_filter(self):
        """获取当前标签筛选值。"""
        return self.tag_filter_var.get() or ALL_TAG_FILTER

    def _get_tag_scope_text(self):
        """返回当前筛选范围的用户可见描述。"""
        current_filter = self._get_current_tag_filter()
        if current_filter == ALL_TAG_FILTER:
            return "全部标签"
        return f"标签“{current_filter}”"

    def _sync_tag_filter_options(self, tunnels):
        """同步标签筛选下拉选项，并保留当前可用选择。"""
        filter_values = self._build_tag_filter_values(tunnels)
        current_filter = self.tag_filter_var.get() or ALL_TAG_FILTER

        self.tag_filter_combo.configure(values=filter_values)
        if current_filter not in filter_values:
            self.tag_filter_var.set(ALL_TAG_FILTER)

    def on_tag_filter_changed(self, event=None):
        """切换标签筛选时刷新列表。"""
        self.refresh_status(emit_log=False)

    def on_type_filter_changed(self, event=None):
        """切换类型筛选时刷新列表。"""
        self.refresh_status(emit_log=False)

    def _matches_type_filter(self, tunnel):
        """判断隧道是否匹配当前类型筛选。"""
        selected = self.type_filter_var.get() or ALL_TYPE_FILTER
        if selected == ALL_TYPE_FILTER:
            return True
        tunnel_type = tunnel.get('tunnel_type', 'local')
        if selected == "反向转发":
            return tunnel_type == 'remote'
        return tunnel_type == 'local'

    def _get_tunnel_by_item_id(self, item_id):
        """根据 Treeview 行 id 获取当前配置中的隧道。"""
        try:
            index = int(str(item_id))
        except (TypeError, ValueError):
            return None

        if 0 <= index < len(self.manager.tunnels):
            return self.manager.tunnels[index]

        return None

    def _set_tree_item_status(self, item_id, status, tag=None, pid=None):
        """更新表格中某一行的状态。"""
        if not self.tree.exists(item_id):
            return

        self.tree.set(item_id, "状态", status)
        if pid is not None:
            self.tree.set(item_id, "PID", str(pid))
        if tag:
            self.tree.item(item_id, tags=(tag,))
        self._set_tree_item_operation(item_id)

    def _get_row_status(self, item_id):
        """读取表格当前行的状态文本。"""
        if not self.tree.exists(item_id):
            return ""
        return str(self.tree.set(item_id, "状态") or "")

    def _get_row_actions(self, item_id=None, status=None):
        """根据当前状态返回可用操作。"""
        current_status = status if status is not None else self._get_row_status(item_id)
        if current_status in STOP_LIKE_STATUSES:
            return ("停止", "编辑", "删除")
        if current_status == "正在停止...":
            return ("编辑", "删除")
        return ("启动", "编辑", "删除")

    def _get_action_label(self, action_name, hovered=False):
        """构建操作按钮文本。"""
        return f"【{action_name}】" if hovered else f"〔{action_name}〕"

    def _get_operation_text(self, actions, hovered_action=None):
        """表格操作列显示文本。"""
        return self.operation_separator.join(
            self._get_action_label(action_name, hovered=(action_name == hovered_action))
            for action_name in actions
        )

    def _get_action_column_id(self):
        """返回 Treeview 中操作列的列序号。"""
        return f"#{len(self.tree['columns'])}"

    def _set_tree_item_operation(self, item_id, hovered_action=None):
        """更新某一行操作列的显示文本。"""
        if self.tree.exists(item_id):
            actions = self._get_row_actions(item_id=item_id)
            self.tree.set(item_id, "操作", self._get_operation_text(actions, hovered_action))

    def _clear_action_hover(self):
        """清除操作列悬停效果。"""
        if self._hovered_action:
            item_id, _ = self._hovered_action
            self._set_tree_item_operation(item_id)
            self._hovered_action = None
        self.tree.configure(cursor="")

    def _get_row_action_at(self, item_id, event_x):
        """根据鼠标横坐标判断命中的操作。"""
        column_id = self._get_action_column_id()
        if not self.tree.exists(item_id):
            return None

        cell_bbox = self.tree.bbox(item_id, column_id)
        if not cell_bbox:
            return None

        cell_x, _, cell_width, _ = cell_bbox
        if cell_width <= 0:
            return None

        actions = self._get_row_actions(item_id=item_id)
        separator_width = self.tree_font.measure(self.operation_separator)
        labels = [self._get_action_label(action_name) for action_name in actions]
        label_widths = [self.tree_font.measure(label) for label in labels]
        content_width = sum(label_widths) + separator_width * (len(labels) - 1)
        content_start = cell_x + max((cell_width - content_width) / 2, 0)
        content_end = content_start + content_width

        if event_x < content_start or event_x > content_end:
            return None

        current_x = content_start
        for index, (action_name, label_width) in enumerate(zip(actions, label_widths)):
            next_x = current_x + label_width
            padding = max(label_width * 0.12, 6)
            padded_start = max(content_start, current_x - padding)
            padded_end = min(content_end, next_x + padding)

            if padded_start <= event_x <= padded_end:
                return action_name

            current_x = next_x + separator_width
            if index < len(actions) - 1 and event_x < current_x:
                return action_name

        return actions[-1] if actions else None

    def _invoke_row_action(self, item_id, action_name):
        """执行表格行内操作。"""
        if not self.tree.exists(item_id):
            return

        selection = self.tree.selection()
        if not (action_name == "删除" and item_id in selection and len(selection) > 1):
            self.tree.selection_set(item_id)

        if action_name == "启动":
            self.start_selected()
        elif action_name == "停止":
            self.stop_selected()
        elif action_name == "编辑":
            self.edit_tunnel()
        elif action_name == "删除":
            self.delete_tunnel()

    def _get_tunnel_open_url(self, tunnel):
        """构建默认浏览器打开地址。

        本地转发：http://local_bind:local_port
        反向转发：http://ssh_host:remote_port（服务器暴露的端口）
        """
        tunnel_type = tunnel.get('tunnel_type', 'local')

        if tunnel_type == 'remote':
            remote_port = tunnel.get('remote_port')
            if not remote_port:
                return None
            ssh_host = tunnel.get('ssh_host', '')
            # 去掉 user@ 前缀，只保留主机名/IP
            host = ssh_host.split('@')[-1] if ssh_host else ''
            if not host:
                return None
            return f"http://{host}:{remote_port}"
        else:
            local_port = tunnel.get('local_port')
            if not local_port:
                return None
            local_bind = self.manager.get_tunnel_local_bind(tunnel)
            browser_host = '127.0.0.1' if local_bind in {'0.0.0.0', '::', '::0', '[::]', '*'} else local_bind
            return f"http://{browser_host}:{local_port}"

    def _is_tunnel_name_unique(self, name, exclude_tunnel=None):
        """校验隧道名称是否唯一。"""
        normalized_name = name.strip()
        for tunnel in self.manager.tunnels:
            if exclude_tunnel is not None and tunnel is exclude_tunnel:
                continue
            if tunnel.get('name', '').strip() == normalized_name:
                return False
        return True

    def _set_restarting(self, tunnel, restarting=True):
        """维护正在重启/重连的隧道标记。"""
        tunnel_id = self.manager.get_tunnel_identity(tunnel)
        if restarting:
            self._restarting_tunnels.add(tunnel_id)
        else:
            self._restarting_tunnels.discard(tunnel_id)

    def refresh_status(self, emit_log=True):
        """刷新隧道状态"""
        self._clear_action_hover()

        # 清空表格
        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            rows = []
            config_changed = False

            with self.manager.config_lock:
                self.manager.load_config()
                self._sync_tag_filter_options(self.manager.tunnels)
                selected_filter = self.tag_filter_var.get() or ALL_TAG_FILTER

                for index, tunnel in enumerate(self.manager.tunnels):
                    if not self._matches_tag_filter(tunnel, selected_filter):
                        continue
                    if not self._matches_type_filter(tunnel):
                        continue

                    name = tunnel.get('name', '未命名')
                    tunnel_type = tunnel.get('tunnel_type', 'local')
                    type_tag = '[R]' if tunnel_type == 'remote' else '[L]'
                    display_name = f"{type_tag} {name}"
                    local_port = tunnel.get('local_port')
                    remote_host = self.manager.get_tunnel_remote_host(tunnel)
                    remote_port = tunnel.get('remote_port')
                    ssh_host = tunnel.get('ssh_host')
                    pid = tunnel.get('pid')
                    tags_display = self._format_tunnel_tags(tunnel)

                    remote_addr = f"{ssh_host} -> {remote_host}:{remote_port}"
                    tunnel_id = self.manager.get_tunnel_identity(tunnel)

                    if tunnel_id in self._restarting_tunnels:
                        status = "重启中"
                        tag = "starting"
                        display_pid = "-"
                    elif pid and self.manager.is_tunnel_running(tunnel):
                        status = "运行中"
                        tag = "running"
                        display_pid = pid
                    else:
                        status = "未启动"
                        tag = "stopped"
                        display_pid = "-"
                        if pid:
                            tunnel['pid'] = None
                            config_changed = True

                    local_bind = self.manager.get_tunnel_local_bind(tunnel)
                    rows.append(
                        (
                            str(index),
                            (
                                display_name,
                                status,
                                tags_display,
                                local_bind,
                                local_port,
                                remote_addr,
                                display_pid,
                                self._get_operation_text(self._get_row_actions(status=status)),
                            ),
                            tag,
                        )
                    )

                if config_changed:
                    self.manager.save_config()

            for item_id, values, tag in rows:
                self.tree.insert("", tk.END, iid=item_id, values=values, tags=(tag,))

            if emit_log:
                self.log("状态已刷新")

        except Exception as e:
            self.log(f"刷新状态失败: {e}")
            messagebox.showerror("错误", f"刷新状态失败: {e}")

    def start_all(self):
        """启动所有隧道"""
        try:
            current_filter = self._get_current_tag_filter()
            scope_text = self._get_tag_scope_text()
            self.log(f"正在启动{scope_text}的隧道...")

            # 在后台线程中执行
            def start_thread():
                try:
                    config_changed = False
                    tunnels_to_start = []
                    skipped_count = 0
                    matched_count = 0
                    start_errors = []

                    with self.manager.config_lock:
                        self.manager.load_config()

                        for index, tunnel in enumerate(self.manager.tunnels):
                            if not self._matches_tag_filter(tunnel, current_filter):
                                continue
                            if not self._matches_type_filter(tunnel):
                                continue

                            matched_count += 1
                            name = tunnel.get('name', '未命名隧道')
                            if self.manager.is_tunnel_running(tunnel):
                                skipped_count += 1
                                self.root.after(0, lambda n=name: self.log(f"隧道 {n} 已在运行，跳过"))
                            else:
                                if tunnel.get('pid'):
                                    tunnel['pid'] = None
                                    config_changed = True
                                tunnels_to_start.append((str(index), tunnel))

                        if config_changed:
                            self.manager.save_config()

                    if matched_count == 0:
                        self.root.after(0, lambda s=scope_text: self.log(f"{s}下没有可启动的隧道"))
                        return

                    if not tunnels_to_start:
                        self.root.after(0, lambda s=scope_text: self.log(f"{s}下没有需要启动的隧道"))
                        return

                    # 并行启动所有隧道
                    started_count = 0
                    start_threads = []
                    result_lock = threading.Lock()

                    def start_single_tunnel(item_id, tunnel):
                        nonlocal started_count
                        name = tunnel.get('name', '未命名隧道')
                        self.root.after(
                            0,
                            lambda i=item_id: self._set_tree_item_status(i, "启动中", "starting", "-")
                        )
                        self.log_async(f"启动隧道: {name}")

                        process = self.manager.start_tunnel(tunnel)

                        if process:
                            self.manager.update_tunnel_pid(tunnel, process.pid)
                            with result_lock:
                                started_count += 1
                            self.root.after(
                                0,
                                lambda i=item_id, p=process.pid: self._set_tree_item_status(i, "运行中", "running", p)
                            )
                            self.log_async(f"隧道 {name} 已启动 (PID: {process.pid})")
                        else:
                            self.root.after(
                                0,
                                lambda i=item_id: self._set_tree_item_status(i, "未启动", "stopped", "-")
                            )
                            error_message = self.manager.get_last_error() or "未知原因"
                            with result_lock:
                                start_errors.append(f"{name}: {error_message}")
                            self.log_async(f"隧道 {name} 启动失败: {error_message}")

                    # 为每个隧道创建独立线程
                    for item_id, tunnel in tunnels_to_start:
                        t = threading.Thread(target=start_single_tunnel, args=(item_id, tunnel), daemon=True)
                        start_threads.append(t)
                        t.start()

                    # 等待所有线程完成
                    for t in start_threads:
                        t.join()

                    self.root.after(
                        0,
                        lambda s=scope_text: self.log(
                            f"{s}完成：启动 {started_count} 个，跳过 {skipped_count} 个"
                        )
                    )
                    if start_errors:
                        error_summary = "\n".join(start_errors[:8])
                        if len(start_errors) > 8:
                            error_summary += f"\n... 另有 {len(start_errors) - 8} 个启动失败"
                        self.root.after(
                            0,
                            lambda summary=error_summary: messagebox.showerror("启动失败", f"以下隧道启动失败：\n\n{summary}")
                        )

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
            current_filter = self._get_current_tag_filter()
            scope_text = self._get_tag_scope_text()
            self.log(f"正在停止{scope_text}的隧道...")

            # 在后台线程中执行
            def stop_thread():
                config_changed = False
                running = []
                matched_count = 0

                with self.manager.config_lock:
                    self.manager.load_config()

                    for index, tunnel in enumerate(self.manager.tunnels):
                        if not self._matches_tag_filter(tunnel, current_filter):
                            continue
                        if not self._matches_type_filter(tunnel):
                            continue

                        matched_count += 1
                        if self.manager.is_tunnel_running(tunnel):
                            running.append((str(index), tunnel))
                        elif tunnel.get('pid'):
                            tunnel['pid'] = None
                            config_changed = True

                    if config_changed:
                        self.manager.save_config()

                if matched_count == 0:
                    self.root.after(0, lambda s=scope_text: self.log(f"{s}下没有可停止的隧道"))
                    return

                if not running:
                    self.root.after(0, lambda s=scope_text: self.log(f"{s}下没有运行中的隧道"))
                    return

                for item_id, tunnel in running:
                    pid = tunnel.get('pid')
                    name = tunnel.get('name')

                    self.root.after(0, lambda i=item_id: self._set_tree_item_status(i, "正在停止..."))

                    with self._tunnel_lock:
                        stopped, message = self.manager.stop_tunnel(tunnel)

                    if stopped:
                        self.log_async(f"关闭隧道: {name} (PID: {pid})")
                        self.log_async(f"隧道 {name} 已关闭")
                    elif message.startswith("关闭失败: "):
                        self.log_async(f"关闭隧道 {name} 时出错: {message.split(': ', 1)[1]}")
                    else:
                        self.log_async(f"隧道 {name} {message}")

                self.root.after(0, lambda s=scope_text: self.log(f"{s}的隧道已关闭"))
                self.root.after(0, self.refresh_status)

            threading.Thread(target=stop_thread, daemon=True).start()

        except Exception as e:
            self.log(f"停止失败: {e}")
            messagebox.showerror("错误", f"停止失败: {e}")

    def open_selected(self):
        """在默认浏览器中打开选中的本地端口网页。"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要打开的隧道")
            return

        with self.manager.config_lock:
            self.manager.load_config()
            opened_urls = []

            for item_id in selection:
                tunnel = self._get_tunnel_by_item_id(item_id)
                if not tunnel:
                    continue

                open_url = self._get_tunnel_open_url(tunnel)
                if not open_url:
                    continue

                webbrowser.open(open_url)
                opened_urls.append((tunnel.get('name', '未命名隧道'), open_url))

        if not opened_urls:
            messagebox.showwarning("提示", "选中的隧道没有可打开的本地端口")
            return

        for tunnel_name, open_url in opened_urls:
            self.log(f"已打开 {tunnel_name}: {open_url}")

    def start_selected(self):
        """启动选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要启动的隧道")
            return

        # 在后台线程中执行启动
        def start_thread():
            config_changed = False
            tunnels_to_start = []
            start_errors = []

            with self.manager.config_lock:
                self.manager.load_config()

                for item_id in selection:
                    tunnel = self._get_tunnel_by_item_id(item_id)
                    if not tunnel:
                        continue

                    name = tunnel.get('name', '未命名隧道')
                    if self.manager.is_tunnel_running(tunnel):
                        self.log_async(f"隧道 {name} 已在运行，跳过")
                        continue

                    if tunnel.get('pid'):
                        tunnel['pid'] = None
                        config_changed = True

                    tunnels_to_start.append((item_id, tunnel))

                if config_changed:
                    self.manager.save_config()

            if not tunnels_to_start:
                return

            # 并行启动所有选中的隧道
            started_count = 0
            start_threads = []
            result_lock = threading.Lock()

            def start_single_tunnel(item_id, tunnel):
                nonlocal started_count
                name = tunnel.get('name', '未命名隧道')
                self.root.after(0, lambda i=item_id: self._set_tree_item_status(i, "启动中", "starting", "-"))
                self.log_async(f"正在启动隧道: {name}")

                process = self.manager.start_tunnel(tunnel)

                if process:
                    self.manager.update_tunnel_pid(tunnel, process.pid)
                    with result_lock:
                        started_count += 1
                    self.root.after(
                        0,
                        lambda i=item_id, p=process.pid: self._set_tree_item_status(i, "运行中", "running", p)
                    )
                    self.log_async(f"隧道 {name} 已启动 (PID: {process.pid})")
                else:
                    self.root.after(0, lambda i=item_id: self._set_tree_item_status(i, "未启动", "stopped", "-"))
                    error_message = self.manager.get_last_error() or "未知原因"
                    with result_lock:
                        start_errors.append(f"{name}: {error_message}")
                    self.log_async(f"隧道 {name} 启动失败: {error_message}")

            # 为每个隧道创建独立线程
            for item_id, tunnel in tunnels_to_start:
                t = threading.Thread(target=start_single_tunnel, args=(item_id, tunnel), daemon=True)
                start_threads.append(t)
                t.start()

            # 等待所有线程完成
            for t in start_threads:
                t.join()

            self.log_async(f"完成：启动 {started_count} 个选中隧道")
            if start_errors:
                error_summary = "\n".join(start_errors[:8])
                if len(start_errors) > 8:
                    error_summary += f"\n... 另有 {len(start_errors) - 8} 个启动失败"
                self.root.after(
                    0,
                    lambda summary=error_summary: messagebox.showerror("启动失败", f"以下隧道启动失败：\n\n{summary}")
                )

        threading.Thread(target=start_thread, daemon=True).start()

    def stop_selected(self):
        """停止选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要停止的隧道")
            return

        def stop_thread():
            config_changed = False
            tunnels_to_stop = []

            with self.manager.config_lock:
                self.manager.load_config()

                for item_id in selection:
                    tunnel = self._get_tunnel_by_item_id(item_id)
                    if not tunnel:
                        continue

                    if self.manager.is_tunnel_running(tunnel):
                        tunnels_to_stop.append((item_id, tunnel))
                    elif tunnel.get('pid'):
                        tunnel['pid'] = None
                        config_changed = True

                if config_changed:
                    self.manager.save_config()

            if not tunnels_to_stop:
                self.log_async("没有需要停止的隧道")
                self.root.after(0, self.refresh_status)
                return

            for item_id, tunnel in tunnels_to_stop:
                name = tunnel.get('name', '未命名隧道')
                pid = tunnel.get('pid')
                self.root.after(0, lambda i=item_id: self._set_tree_item_status(i, "正在停止..."))

                with self._tunnel_lock:
                    stopped, message = self.manager.stop_tunnel(tunnel)

                if stopped:
                    self.log_async(f"关闭隧道: {name} (PID: {pid})")
                    self.log_async(f"隧道 {name} 已关闭")
                elif message.startswith("关闭失败: "):
                    self.log_async(f"停止隧道 {name} 失败: {message.split(': ', 1)[1]}")
                else:
                    self.log_async(f"隧道 {name} {message}")

            self.root.after(0, self.refresh_status)

        threading.Thread(target=stop_thread, daemon=True).start()

    def show_logs(self):
        """显示详细日志"""
        messagebox.showinfo("日志", "日志功能待实现")

    def add_tunnel(self):
        """添加新隧道"""
        with self.manager.config_lock:
            self.manager.load_config()
            existing_ssh_hosts = self._get_known_ssh_hosts()
            existing_tags = self._get_known_tags()

        dialog = TunnelEditDialog(
            self.root,
            "添加隧道",
            existing_ssh_hosts=existing_ssh_hosts,
            existing_tags=existing_tags
        )
        if dialog.result:
            with self.manager.config_lock:
                self.manager.load_config()
                if not self._is_tunnel_name_unique(dialog.result['name']):
                    messagebox.showerror("错误", f"隧道名称已存在: {dialog.result['name']}")
                    return
                ports_valid, port_message = self._validate_tunnel_config_ports(dialog.result)
                if not ports_valid:
                    messagebox.showerror("错误", port_message)
                    return

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

        item_id = selection[0]

        with self.manager.config_lock:
            self.manager.load_config()
            tunnel = self._get_tunnel_by_item_id(item_id)
            if not tunnel:
                messagebox.showerror("错误", "无法定位选中的隧道配置")
                return
            tunnel_snapshot = dict(tunnel)
            existing_ssh_hosts = self._get_known_ssh_hosts(exclude_tunnel=tunnel)
            existing_tags = self._get_known_tags(exclude_tunnel=tunnel)

        dialog = TunnelEditDialog(
            self.root,
            "编辑隧道",
            tunnel_snapshot,
            existing_ssh_hosts=existing_ssh_hosts,
            existing_tags=existing_tags
        )
        if not dialog.result:
            return

        with self.manager.config_lock:
            self.manager.load_config()
            tunnel = self._get_tunnel_by_item_id(item_id)
            if not tunnel:
                messagebox.showerror("错误", "隧道配置已发生变化，请刷新后重试")
                return

            if not self._is_tunnel_name_unique(dialog.result['name'], exclude_tunnel=tunnel):
                messagebox.showerror("错误", f"隧道名称已存在: {dialog.result['name']}")
                return
            ports_valid, port_message = self._validate_tunnel_config_ports(dialog.result, exclude_tunnel=tunnel)
            if not ports_valid:
                messagebox.showerror("错误", port_message)
                return

            was_running = self.manager.is_tunnel_running(tunnel)
            original_name = tunnel.get('name', '未命名隧道')

            new_tunnel = dict(tunnel)
            new_tunnel.update(dialog.result)
            new_tunnel['pid'] = None

        if was_running:
            self.log(f"隧道 {original_name} 正在运行，先停止...")
            with self._tunnel_lock:
                stopped, message = self.manager.stop_tunnel(tunnel, save_config=False)

            if stopped:
                self.log(f"隧道 {original_name} 已关闭")
            elif message.startswith("关闭失败: "):
                self.log(f"停止隧道失败: {message.split(': ', 1)[1]}")
                messagebox.showerror("错误", f"停止隧道失败: {message.split(': ', 1)[1]}")
                return
            else:
                self.log(f"隧道 {original_name} {message}")

        with self.manager.config_lock:
            self.manager.load_config()
            current_tunnel = self._get_tunnel_by_item_id(item_id)
            if not current_tunnel:
                messagebox.showerror("错误", "隧道配置已发生变化，请刷新后重试")
                return

            self.manager.tunnels[int(item_id)] = new_tunnel
            self.manager.save_config()

        self.log(f"已更新隧道配置: {new_tunnel['name']}")
        self.refresh_status()

        if was_running:
            self._set_restarting(new_tunnel, True)
            self.refresh_status()

            def restart_after_edit(tunnel_data):
                self.log_async(f"正在重启隧道: {tunnel_data['name']}")
                proc = self.manager.start_tunnel(tunnel_data)
                self._set_restarting(tunnel_data, False)
                if proc:
                    self.manager.update_tunnel_pid(tunnel_data, proc.pid)
                    self.log_async(f"隧道 {tunnel_data['name']} 已重启 (PID: {proc.pid})")
                else:
                    error_message = self.manager.get_last_error() or "未知原因"
                    self.log_async(f"隧道 {tunnel_data['name']} 重启失败: {error_message}")
                self.root.after(0, self.refresh_status)

            threading.Thread(target=restart_after_edit, args=(new_tunnel,), daemon=True).start()

    def delete_tunnel(self):
        """删除选中的隧道"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请先选择要删除的隧道")
            return

        with self.manager.config_lock:
            self.manager.load_config()
            tunnel_snapshots = []
            names = []
            for item_id in selection:
                tunnel = self._get_tunnel_by_item_id(item_id)
                if not tunnel:
                    continue
                tunnel_snapshots.append(dict(tunnel))
                names.append(tunnel.get('name', '未命名隧道'))

        if not tunnel_snapshots:
            messagebox.showwarning("提示", "没有找到可删除的隧道")
            return

        if not messagebox.askyesno("确认删除", f"确定要删除 {len(names)} 个隧道吗？\n{', '.join(names)}"):
            return

        def delete_thread():
            with self.manager.config_lock:
                self.manager.load_config()
                tunnels_to_delete = [dict(tunnel) for tunnel in tunnel_snapshots]

            removable_tunnels = []
            for tunnel in tunnels_to_delete:
                name = tunnel.get('name', '未命名隧道')
                if self.manager.is_tunnel_running(tunnel):
                    self.log_async(f"删除前先关闭隧道: {name}")
                    with self._tunnel_lock:
                        stopped, message = self.manager.stop_tunnel(tunnel)

                    if stopped:
                        self.log_async(f"隧道 {name} 已关闭")
                    elif message.startswith("关闭失败: "):
                        self.log_async(f"删除隧道 {name} 前停止失败: {message.split(': ', 1)[1]}")
                        continue
                    else:
                        self.log_async(f"隧道 {name} {message}")

                removable_tunnels.append(tunnel)

            if not removable_tunnels:
                self.root.after(0, self.refresh_status)
                return

            with self.manager.config_lock:
                for tunnel in removable_tunnels:
                    if self.manager.remove_tunnel(tunnel, save_config=False):
                        self.log_async(f"已删除隧道: {tunnel.get('name', '未命名隧道')}")
                self.manager.save_config()

            self.root.after(0, self.refresh_status)

        threading.Thread(target=delete_thread, daemon=True).start()

    def show_context_menu(self, event):
        """显示右键菜单"""
        # 选中右键点击的项
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def on_delete_key(self, event):
        """Delete 键触发批量删除。"""
        if self.tree.selection():
            self.delete_tunnel()
            return "break"

    def on_tree_click(self, event):
        """处理表格点击事件，支持行内操作列。"""
        item_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not item_id:
            return

        if column_id != self._get_action_column_id():
            return

        action_name = self._get_row_action_at(item_id, event.x)
        if not action_name:
            return "break"

        self.root.after(0, lambda i=item_id, a=action_name: self._invoke_row_action(i, a))
        return "break"

    def on_tree_motion(self, event):
        """悬停到操作列时提供手型与高亮反馈。"""
        item_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)

        if not item_id or column_id != self._get_action_column_id():
            self._clear_action_hover()
            return

        action_name = self._get_row_action_at(item_id, event.x)
        if not action_name:
            self._clear_action_hover()
            return

        new_hovered_action = (item_id, action_name)
        if self._hovered_action == new_hovered_action:
            self.tree.configure(cursor="hand2")
            return

        if self._hovered_action:
            old_item_id, _ = self._hovered_action
            self._set_tree_item_operation(old_item_id)

        self._hovered_action = new_hovered_action
        self._set_tree_item_operation(item_id, hovered_action=action_name)
        self.tree.configure(cursor="hand2")

    def on_tree_leave(self, event):
        """鼠标离开表格时清理操作悬停效果。"""
        self._clear_action_hover()

    def on_double_click(self, event):
        """双击表格行时的处理"""
        item = self.tree.identify_row(event.y)
        if not item:
            return

        if self.tree.identify_column(event.x) == self._get_action_column_id():
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
            f"SSH隧道管理器 {APP_VERSION}",
            image,
            f"{APP_TITLE} - 点击显示窗口",
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

    def minimize_to_tray(self):
        """最小化到托盘并继续在后台运行。"""
        self.root.withdraw()
        self.is_minimized = True

        if not self.tray_icon:
            self.create_tray_icon()

        self.log("程序已最小化到后台运行，隧道继续工作")

    def quit_app(self):
        """退出应用"""
        self.root.after(0, lambda: self.shutdown_application("托盘退出"))

    def _quit_app_impl(self):
        """退出应用的实现（在主线程中执行）"""
        self.shutdown_application("托盘退出")

    def update_auto_start_status(self, emit_log=True):
        """更新所有隧道的auto_start状态"""
        try:
            running_count = 0
            with self.manager.config_lock:
                self.manager.load_config()

                for tunnel in self.manager.tunnels:
                    is_running = self.manager.is_tunnel_running(tunnel)
                    tunnel['auto_start'] = is_running
                    if is_running:
                        running_count += 1
                    elif tunnel.get('pid'):
                        tunnel['pid'] = None

                self.manager.save_config()

            if emit_log and running_count:
                self.log(f"已保存 {running_count} 个隧道的自动启动状态")
        except Exception as e:
            if emit_log and self._root_exists():
                self.log(f"保存自动启动状态失败: {e}")

    def auto_start_tunnels(self):
        """自动启动标记为auto_start的隧道"""
        try:
            with self.manager.config_lock:
                self.manager.load_config()
                auto_start_tunnels = [
                    (str(index), tunnel)
                    for index, tunnel in enumerate(self.manager.tunnels)
                    if tunnel.get('auto_start', False)
                ]

            if not auto_start_tunnels:
                return

            self.log(f"检测到 {len(auto_start_tunnels)} 个隧道需要自动启动...")

            # 在后台线程中启动隧道
            def start_thread():
                # 并行启动所有隧道
                started_count = 0
                start_threads = []
                result_lock = threading.Lock()

                def start_single_tunnel(item_id, tunnel):
                    nonlocal started_count
                    name = tunnel.get('name', '未命名隧道')
                    self.root.after(0, lambda i=item_id: self._set_tree_item_status(i, "启动中", "starting", "-"))
                    self.log_async(f"自动启动隧道: {name}")

                    process = self.manager.start_tunnel(tunnel)

                    if process:
                        self.manager.update_tunnel_pid(tunnel, process.pid)
                        with result_lock:
                            started_count += 1
                        self.root.after(
                            0,
                            lambda i=item_id, p=process.pid: self._set_tree_item_status(i, "运行中", "running", p)
                        )
                        self.log_async(f"隧道 {name} 已启动 (PID: {process.pid})")
                    else:
                        self.root.after(0, lambda i=item_id: self._set_tree_item_status(i, "未启动", "stopped", "-"))
                        error_message = self.manager.get_last_error() or "未知原因"
                        self.log_async(f"隧道 {name} 启动失败: {error_message}")

                # 为每个隧道创建独立线程
                for item_id, tunnel in auto_start_tunnels:
                    t = threading.Thread(target=start_single_tunnel, args=(item_id, tunnel), daemon=True)
                    start_threads.append(t)
                    t.start()

                # 等待所有线程完成
                for t in start_threads:
                    t.join()

                self.log_async(f"自动启动完成：成功 {started_count} 个")

            threading.Thread(target=start_thread, daemon=True).start()

        except Exception as e:
            self.log(f"自动启动隧道失败: {e}")

    def on_closing(self):
        """窗口关闭时的处理"""
        self.minimize_to_tray()

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
        """监控隧道进程,自动重启死掉的进程或不通的隧道"""
        while self.monitoring:
            try:
                with self.manager.config_lock:
                    self.manager.load_config()
                    tunnels = list(self.manager.tunnels)

                for tunnel in tunnels:
                    name = tunnel.get('name')
                    pid = tunnel.get('pid')

                    if not pid:
                        continue

                    # 在锁外做连通性探测（纯读操作，避免持锁时间过长）
                    local_port = tunnel.get('local_port')
                    tunnel_type = tunnel.get('tunnel_type', 'local')
                    port_reachable = None  # None 表示跳过探测
                    if tunnel_type == 'remote':
                        # 反向隧道：检测远端端口连通性（单次探测，避免监控循环阻塞）
                        if tunnel.get('remote_port'):
                            port_reachable = self.manager.test_remote_tunnel_connectivity(tunnel, max_retries=1)
                    elif isinstance(local_port, int) and 1 <= local_port <= 65535:
                        try:
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                                sock.settimeout(1.5)
                                result = sock.connect_ex(('127.0.0.1', local_port))
                            port_reachable = (result == 0)
                        except Exception:
                            port_reachable = False

                    with self._tunnel_lock:
                        try:
                            process = self.manager.get_tunnel_process(tunnel)
                            if not process:
                                # 进程死掉了,尝试重启
                                self.log_async(f"检测到隧道 {name} 已关闭,正在重启...")
                                self._set_restarting(tunnel, True)
                                self.root.after(0, self.refresh_status)
                                new_process = self.manager.start_tunnel(tunnel)
                                self._set_restarting(tunnel, False)
                                if new_process:
                                    self.manager.update_tunnel_pid(tunnel, new_process.pid)
                                    self.log_async(f"隧道 {name} 已重启 (PID: {new_process.pid})")
                                    self.root.after(0, self.refresh_status)
                                else:
                                    self.manager.clear_tunnel_pid(tunnel)
                                    error_message = self.manager.get_last_error() or "未知原因"
                                    self.log_async(f"隧道 {name} 重启失败: {error_message}")
                                    self.root.after(0, self.refresh_status)
                            elif port_reachable is False:
                                # 进程存活但端口不通,重连
                                self.log_async(f"检测到隧道 {name} 不通，正在重连...")
                                self._set_restarting(tunnel, True)
                                self.root.after(0, self.refresh_status)
                                terminate_ok = True
                                try:
                                    process.terminate()
                                    try:
                                        process.wait(timeout=3)
                                    except Exception:
                                        try:
                                            process.kill()
                                        except Exception as kill_err:
                                            self.log_async(f"隧道 {name} 强制终止失败: {kill_err}")
                                except Exception as term_err:
                                    self.log_async(f"隧道 {name} 终止失败，跳过重连: {term_err}")
                                    terminate_ok = False
                                if terminate_ok:
                                    new_process = self.manager.start_tunnel(tunnel)
                                    self._set_restarting(tunnel, False)
                                    if new_process:
                                        self.manager.update_tunnel_pid(tunnel, new_process.pid)
                                        self.log_async(f"隧道 {name} 已重连 (PID: {new_process.pid})")
                                        self.root.after(0, self.refresh_status)
                                    else:
                                        self.manager.clear_tunnel_pid(tunnel)
                                        error_message = self.manager.get_last_error() or "未知原因"
                                        self.log_async(f"隧道 {name} 重连失败: {error_message}")
                                        self.root.after(0, self.refresh_status)
                                else:
                                    self._set_restarting(tunnel, False)
                                    self.manager.clear_tunnel_pid(tunnel)
                                    self.root.after(0, self.refresh_status)
                        except Exception as e:
                            self.log_async(f"监控隧道 {name} 时出错: {e}")

            except Exception as e:
                self.log_async(f"监控线程异常: {e}")

            time.sleep(10)  # 每10秒检查一次


class TunnelEditDialog:
    """隧道编辑对话框"""
    def __init__(self, parent, title, tunnel=None, existing_ssh_hosts=None, existing_tags=None):
        self.result = None
        self.existing_ssh_hosts = existing_ssh_hosts or []
        self.existing_tags = existing_tags or []
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("620x580")
        self.dialog.minsize(620, 580)
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()

        # 创建表单
        frame = ttk.Frame(self.dialog, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)

        # 名称
        ttk.Label(frame, text="名称:").grid(row=0, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.name_var = tk.StringVar(value=tunnel.get('name', '') if tunnel else '')
        name_entry = PlaceholderEntry(frame, textvariable=self.name_var, placeholder="", width=52)
        name_entry.grid(row=0, column=1, sticky=tk.EW, pady=8)

        # SSH主机
        ttk.Label(frame, text="SSH主机:").grid(row=1, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.ssh_host_var = tk.StringVar(value=tunnel.get('ssh_host', '') if tunnel else '')
        ssh_host_combo = ttk.Combobox(
            frame,
            textvariable=self.ssh_host_var,
            values=self.existing_ssh_hosts,
            width=50
        )
        ssh_host_combo.grid(row=1, column=1, sticky=tk.EW, pady=8)

        # SSH端口
        ttk.Label(frame, text="SSH端口:").grid(row=2, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.ssh_port_var = tk.StringVar(value=str(tunnel.get('ssh_port', 22)) if tunnel else '22')
        ssh_port_entry = PlaceholderEntry(frame, textvariable=self.ssh_port_var, placeholder="", width=52)
        ssh_port_entry.grid(row=2, column=1, sticky=tk.EW, pady=8)

        # 隧道类型
        self.tunnel_type_var = tk.StringVar(value=(tunnel.get('tunnel_type', 'local') if tunnel else 'local'))
        type_frame = ttk.Frame(frame)
        type_frame.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(8, 2))
        ttk.Label(type_frame, text="隧道类型:").pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(
            type_frame, text="本地转发 -L（将远端服务映射到本机）",
            variable=self.tunnel_type_var, value='local',
            command=self._on_type_changed
        ).pack(side=tk.LEFT, padx=(0, 16))
        ttk.Radiobutton(
            type_frame, text="反向转发 -R（将本机服务暴露到远端）",
            variable=self.tunnel_type_var, value='remote',
            command=self._on_type_changed
        ).pack(side=tk.LEFT)

        # 流量流向提示
        self._type_hint_var = tk.StringVar()
        ttk.Label(frame, textvariable=self._type_hint_var, foreground="gray").grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=(0, 4)
        )

        # 本地绑定地址
        self.local_bind_label = ttk.Label(frame, text="本地绑定地址:")
        self.local_bind_label.grid(row=5, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.local_bind_var = tk.StringVar(value=tunnel.get('local_bind', '127.0.0.1') if tunnel else '127.0.0.1')
        self.local_bind_combo = ttk.Combobox(frame, textvariable=self.local_bind_var, values=['127.0.0.1', '0.0.0.0'], width=50)
        self.local_bind_combo.grid(row=5, column=1, sticky=tk.EW, pady=8)

        # 本地端口
        self.local_port_label = ttk.Label(frame, text="本地端口:")
        self.local_port_label.grid(row=6, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.local_port_var = tk.StringVar(value=str(tunnel.get('local_port', '')) if tunnel else '')
        self.local_port_entry = PlaceholderEntry(frame, textvariable=self.local_port_var, placeholder="", width=52)
        self.local_port_entry.grid(row=6, column=1, sticky=tk.EW, pady=8)

        # 远程主机
        self.remote_host_label = ttk.Label(frame, text="远端服务地址:")
        self.remote_host_label.grid(row=7, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.remote_host_var = tk.StringVar(value=(tunnel.get('remote_host') or '127.0.0.1') if tunnel else '127.0.0.1')
        self.remote_host_combo = ttk.Combobox(frame, textvariable=self.remote_host_var, values=['127.0.0.1'], width=50)
        self.remote_host_combo.grid(row=7, column=1, sticky=tk.EW, pady=8)

        # 远程端口
        self.remote_port_label = ttk.Label(frame, text="远端服务端口:")
        self.remote_port_label.grid(row=8, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.remote_port_var = tk.StringVar(value=str(tunnel.get('remote_port', '')) if tunnel else '')
        remote_port_entry = PlaceholderEntry(frame, textvariable=self.remote_port_var, placeholder="", width=52)
        remote_port_entry.grid(row=8, column=1, sticky=tk.EW, pady=8)

        # 标签
        ttk.Label(frame, text="标签:").grid(row=9, column=0, sticky=tk.W, pady=8, padx=(0, 12))
        self.tags_var = tk.StringVar(value=', '.join(normalize_tags(tunnel.get('tags', []))) if tunnel else '')
        self.tags_combo = ttk.Combobox(
            frame,
            textvariable=self.tags_var,
            values=self.existing_tags,
            width=50
        )
        self.tags_combo.grid(row=9, column=1, sticky=tk.EW, pady=8)
        self.tags_combo.bind("<<ComboboxSelected>>", self.on_tag_selected)
        ttk.Label(
            frame,
            text="可直接输入；也可从下拉选择已有标签，多个标签用逗号分隔",
            foreground="gray"
        ).grid(row=10, column=1, sticky=tk.W)

        # 按钮
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=11, column=0, columnspan=2, pady=24)
        ttk.Button(button_frame, text="确定", command=self.ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=self.cancel).pack(side=tk.LEFT, padx=5)

        # 根据初始类型初始化标签
        self._on_type_changed()

        # 居中显示
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (self.dialog.winfo_width() // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (self.dialog.winfo_height() // 2)
        self.dialog.geometry(f"+{x}+{y}")

        self.dialog.wait_window()

    def _on_type_changed(self):
        """切换隧道类型时更新表单标签和下拉候选。"""
        t = self.tunnel_type_var.get()
        if t == 'remote':
            # 反向转发：本机提供服务，服务器暴露端口
            self.local_bind_label.config(text="本机服务地址:")
            self.local_port_label.config(text="本机服务端口:")
            self.remote_host_label.config(text="远端监听地址:")
            self.remote_port_label.config(text="远端监听端口:")
            self.local_port_entry.placeholder = ""
            self.local_bind_combo.config(values=['127.0.0.1'])
            if self.local_bind_var.get().strip() == '0.0.0.0':
                self.local_bind_var.set('127.0.0.1')
            self.remote_host_combo.config(values=['0.0.0.0', '127.0.0.1'])
            if not self.remote_host_var.get().strip():
                self.remote_host_var.set('0.0.0.0')
            self._type_hint_var.set(
                "访问 远端IP:远端监听端口 → 流量转发到 本机服务地址:本机服务端口"
            )
        else:
            # 本地转发：本机开端口，访问远端服务
            self.local_bind_label.config(text="本机监听地址:")
            self.local_port_label.config(text="本机监听端口:")
            self.remote_host_label.config(text="远端服务地址:")
            self.remote_port_label.config(text="远端服务端口:")
            self.local_port_entry.placeholder = ""
            self.local_bind_combo.config(values=['127.0.0.1', '0.0.0.0'])
            self.remote_host_combo.config(values=['127.0.0.1'])
            self._type_hint_var.set(
                "访问 本机监听地址:本机监听端口 → 流量转发到 远端服务地址:远端服务端口"
            )

    def on_tag_selected(self, event=None):
        """从下拉选择已有标签时追加到当前标签列表。"""
        selected_tag = self.tags_combo.get().strip()
        if not selected_tag:
            return

        tags = normalize_tags(self.tags_var.get())
        if selected_tag not in tags:
            tags.append(selected_tag)

        self.tags_var.set(', '.join(tags))
        self.tags_combo.selection_clear()

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
        tunnel_type = self.tunnel_type_var.get()
        local_bind = self.local_bind_var.get().strip() or '127.0.0.1'
        if tunnel_type == 'remote' and local_bind == '0.0.0.0':
            messagebox.showerror("错误", "反向隧道的本机服务地址不能为 0.0.0.0，请填写具体地址（如 127.0.0.1）")
            return
        remote_host_default = '0.0.0.0' if tunnel_type == 'remote' else '127.0.0.1'
        remote_host = self.remote_host_var.get().strip() or remote_host_default
        tags = normalize_tags(self.tags_var.get())

        self.result = {
            'name': self.name_var.get().strip(),
            'ssh_host': self.ssh_host_var.get().strip(),
            'ssh_port': ssh_port,
            'local_bind': local_bind,
            'local_port': local_port,
            'remote_host': remote_host,
            'remote_port': remote_port,
            'tags': tags,
            'tunnel_type': tunnel_type,
        }

        self.dialog.destroy()

    def cancel(self):
        """取消按钮"""
        self.dialog.destroy()


def main():
    app = None
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
        if app is not None:
            try:
                app._run_shutdown_cleanup(emit_log=False)
            except Exception:
                pass
        import traceback
        error_msg = f"GUI启动失败:\n{e}\n\n{traceback.format_exc()}"
        try:
            messagebox.showerror("错误", error_msg)
        except:
            print(error_msg)
            input("\n按回车键退出...")


if __name__ == "__main__":
    main()
