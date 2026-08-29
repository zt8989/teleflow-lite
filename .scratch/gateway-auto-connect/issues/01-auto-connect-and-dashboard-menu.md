# 01 — 启动自动连接网关 + Dashboard 顶部菜单与托盘一致

Type: task
Status: resolved
Blocked by:

## 需求

1. 记录上次的"启动服务状态":新配置 `sip_auto_connect`(默认 `true`)。
   - 打开 APP 且配置完整(`sip_server` + `sip_user` 非空)→ 自动 `service.start()`(连接网关监听状态)。
   - 启动抛异常或配置不完整 → 停在"停止"状态,并把标志持久化为 `false`(避免下次重复失败)。
   - 用户在菜单/托盘手动启动 → 持久化 `true`;手动停止 → 持久化 `false`。
2. Dashboard 顶部说明文字替换为"菜单"下拉按钮;菜单项与系统托盘完全一致(同一组 QAction):启动/停止 SIP 服务、显示窗口、设置、测试汇报、退出。

## 验收

- [ ] `sip_auto_connect` 默认 true,roundtrip 保存
- [ ] 配置完整 + true → 启动后 `service.running`
- [ ] 配置不完整(server 或 user 空)→ 不启动,标志落盘 false
- [ ] start 抛异常 → 不启动、记日志、标志落盘 false
- [ ] 标志 false → 不自动启动
- [ ] Dashboard 顶部有菜单按钮,菜单项与托盘菜单为同一组 QAction
- [ ] 手动启停后标志持久化正确
- [ ] 全量 pytest 通过

## Comments

2026-08-29: 创建(接 sip-port-auto-detect 完成后)。

## Answer

2026-08-29 已实现并验证:

- `Settings.sip_auto_connect: bool = True`(config.py);默认 true = 打开 APP 且配置完整(`sip_server` + `sip_user` 非空)自动 `service.start()`。`ConfigStore.save/load` roundtrip 已测。
- `app.py` 新增 `maybe_auto_start_sip(service, store, log)`:配置不完整或 `start()` 抛异常 → 不启动、日志提示、标志落盘 false(避免下次重复失败);标志 false → 不自动启动。
- MainWindow 持有 store:`_toggle_sip` 手动启停后持久化标志(启动 → true,停止 → false),即"记录上一次的启动服务状态"。
- Dashboard 顶部说明文字替换为"菜单 ▾"下拉按钮(`QToolButton` InstantPopup);菜单项与托盘菜单用**同一组 QAction**(`_build_service_actions`),文字/状态天然同步:启动/停止 SIP 服务、显示窗口、设置、测试汇报、退出。
- UI 原型同步:顶部 `菜单 ▾` 下拉(与托盘同按钮列表);托盘/顶部两个 SIP 按钮文字由同一 render 同步。
- 测试(100 个全过,mypy 通过):dashboard 菜单与托盘同一 action 对象;`_toggle_sip` 持久化;auto-start 四路径(完整/不完整/异常/标志关)。