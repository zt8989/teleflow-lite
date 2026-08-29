# 启动时自动连接网关 + Dashboard 顶部菜单

## 背景

TeleFlow 打开后默认停在"未启动"状态,用户必须点托盘菜单"启动 SIP 服务"才能连网关。用户希望:打开 APP 且配置完整时自动进入"连接网关监听"状态;失败或配置不完整则停在"停止"状态。同时 Dashboard 顶部的说明文字(提示去托盘操作)替换为真正的下拉菜单,菜单项与托盘一致。

## 决定

- 新增配置 `sip_auto_connect`(默认 `true`):记录"打开 APP 时是否自动连接网关"。
  - 打开 APP:标志为 true 且配置完整(`sip_server` + `sip_user` 非空)→ 自动 `service.start()`(连接网关监听)。
  - 启动抛异常或配置不完整 → 保持"停止"状态,并把标志置为 false 持久化,避免下次重复失败。
  - 用户在菜单/托盘手动启动 → 标志置 true 保存;手动停止 → 置 false 保存。即"记录上一次的启动服务状态"。
- Dashboard 顶部提示文字替换为"菜单"下拉按钮;菜单项与系统托盘完全一致(同一组 QAction):启动/停止 SIP 服务、显示窗口、设置、测试汇报、退出。

## 范围

1. `Settings.sip_auto_connect: bool = True`(config.py)+ 持久化。
2. app.py:自动连接逻辑(可测函数)+ MainWindow 共享动作菜单 + Dashboard 顶部按钮 + 手动启停时持久化标志。
3. UI 原型同步(顶部菜单 + 自动连接示意)。
4. 测试:config 默认/roundtrip;自动连接(完整/不完整/异常/标志假);dashboard 菜单与托盘共用动作。