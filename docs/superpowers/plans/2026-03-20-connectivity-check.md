# 守护进程连通性检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在守护监控线程检测到 SSH 进程存活时，额外做一次 socket 连通性探测，若不通则 terminate 旧进程并重连。

**Architecture:** 在 `monitor_tunnels()` 的 for 循环体内，先在锁外对 `local_port` 做 socket 探测，再进入 `_tunnel_lock` 块根据进程存活状态和探测结果决策是否重连。不引入新线程、新配置项、新状态字段。

**Tech Stack:** Python 3, psutil, socket（标准库）, tkinter（GUI 线程调度）

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `source/tunnel_gui.py` | 修改 | 修改 `monitor_tunnels()` 方法（第 1047-1090 行）|

---

### Task 1: 在 monitor_tunnels() 中增加连通性探测与重连逻辑

**Files:**
- Modify: `source/tunnel_gui.py:1047-1090`

- [ ] **Step 1: 阅读当前 monitor_tunnels() 实现，确认理解现有逻辑**

  阅读 `source/tunnel_gui.py` 第 1047-1091 行。
  确认：
  - `_tunnel_lock` 覆盖范围（第 1060 行的 `with` 块）
  - 进程不存活时的重启逻辑（第 1064-1073 行）
  - `psutil.NoSuchProcess` 分支（第 1074-1083 行）
  - 外层 `except Exception` 兜底（第 1084-1085 行）

- [ ] **Step 2: 修改 monitor_tunnels() 方法**

  将 `source/tunnel_gui.py` 第 1047-1090 行替换为以下实现：

  ```python
  def monitor_tunnels(self):
      """监控隧道进程,自动重启死掉的进程或不通的隧道"""
      while self.monitoring:
          try:
              self.manager.load_config()

              for tunnel in self.manager.tunnels:
                  name = tunnel.get('name')
                  pid = tunnel.get('pid')

                  if not pid:
                      continue

                  # 在锁外做连通性探测（纯读操作，避免持锁时间过长）
                  local_port = tunnel.get('local_port')
                  port_reachable = None  # None 表示跳过探测
                  if isinstance(local_port, int) and 1 <= local_port <= 65535:
                      try:
                          sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                          sock.settimeout(1.5)
                          result = sock.connect_ex(('127.0.0.1', local_port))
                          sock.close()
                          port_reachable = (result == 0)
                      except Exception:
                          port_reachable = False

                  with self._tunnel_lock:
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
                          elif port_reachable is False:
                              # 进程存活但端口不通,重连
                              self.root.after(0, lambda n=name: self.log(f"检测到隧道 {n} 不通，正在重连..."))
                              try:
                                  process.terminate()
                                  try:
                                      process.wait(timeout=3)
                                  except psutil.TimeoutExpired:
                                      try:
                                          process.kill()
                                      except Exception as kill_err:
                                          self.root.after(0, lambda n=name, err=kill_err: self.log(f"隧道 {n} 强制终止失败: {err}"))
                              except Exception:
                                  pass
                              new_process = self.manager.start_tunnel(tunnel)
                              if new_process:
                                  self.manager.update_tunnel_pid(name, new_process.pid)
                                  self.root.after(0, lambda n=name, p=new_process.pid: self.log(f"隧道 {n} 已重连 (PID: {p})"))
                                  self.root.after(0, self.refresh_status)
                              else:
                                  self.root.after(0, lambda n=name: self.log(f"隧道 {n} 重连失败"))
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
                      except Exception as e:
                          self.root.after(0, lambda n=name, err=e: self.log(f"监控隧道 {n} 时出错: {err}"))

          except Exception as e:
              self.root.after(0, lambda err=e: self.log(f"监控线程异常: {err}"))

          time.sleep(10)  # 每10秒检查一次
  ```

- [ ] **Step 3: 添加 socket 导入（必须执行）**

  `source/tunnel_gui.py` 当前不存在 `import socket`，必须添加。
  在文件顶部 `import time` 所在行的下方添加：

  ```python
  import socket
  ```

- [ ] **Step 4: 人工验证关键逻辑点**

  检查修改后的代码，逐条确认：
  - [ ] `port_reachable = None` 初始化，`local_port` 无效时不探测
  - [ ] socket 探测在 `with self._tunnel_lock:` **之前**（锁外）
  - [ ] `elif port_reachable is False:` 使用 `is False` 而非 `== False`（排除 None 情况）
  - [ ] terminate → wait(3s) → kill fallback，kill 失败时记录日志（`隧道 {n} 强制终止失败: {err}`）
  - [ ] 进程不存活分支（`not process.is_running()` 和 `NoSuchProcess`）保持不变

- [ ] **Step 5: 手动测试**

  启动程序，打开一个正常隧道，确认：
  1. 正常情况：监控日志无异常输出，隧道保持运行
  2. 可选验证：手动终止 SSH 进程后约 10 秒内看到重启日志

- [ ] **Step 6: 提交**

  ```bash
  git add source/tunnel_gui.py
  git commit -m "feat:守护进程增加连通性探测，进程存活但不通时自动重连"
  ```
