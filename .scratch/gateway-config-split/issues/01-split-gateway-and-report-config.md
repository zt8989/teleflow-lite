# 01 — 网关与汇报目标配置拆分(域名或IP/端口/分机号)

Type: task
Status: resolved
Blocked by:

## 需求

- `sip_server`(URI)→ `sip_host` + `sip_server_port`;`report_target`(URI)→ `report_host` + `report_port` + `report_extension`。
- 设置界面两处改为"地址 / 端口 / 分机号"三字段;端口 QSpinBox 1-65535。
- 旧配置迁移:load 时解析旧 URI(`sip:user@host:port`、`host:port`、无端口、IPv6 方括号),拆入新字段。
- 后端由新字段合成 URI;删除 `_sip_uri` / `_host_of`。
- 对外拨打目标恒带端口:`sip:8000@192.168.1.116:5060`。

## 验收

- [ ] 新字段默认值:sip_host=""、sip_server_port=5060、report_host=""、report_port=5060、report_extension=""
- [ ] 旧 `sip_server`/`report_target` 迁移正确(含无端口/带 user 前缀)
- [ ] `Settings` roundtrip 保留新字段
- [ ] pjsua2 注册 URI 由新字段合成(registrarUri=`sip:{host}:{port}`)
- [ ] `start_report` 默认目标由新字段合成并带端口
- [ ] 设置对话框两处三字段 load/save 正确
- [ ] `maybe_auto_start_sip` 完整性判定用 `sip_host` + `sip_user`
- [ ] 全量 pytest 通过

## Comments

2026-08-29: 创建。

## Answer

2026-08-29 已实现并验证:

- `Settings`:删除 `sip_server`/`report_target`,新增 `sip_host`(网关域名或 IP)、`sip_server_port`(默认 5060)、`report_host`、`report_port`(默认 5060)、`report_extension`(座机分机号);`sip_user` 继续作分机号/认证账号。
- `ConfigStore.load` 迁移:旧单 URI(`sip:user@host:port`、`host:port`、无端口、裸 host)由 `_split_sip_uri` 解析拆分;显式新字段优先(setdefault)。真实配置验证:`sip:192.168.1.189:5060` → host/5060,`sip:8000@192.168.1.116:5060` → ext/host/port 全部正确。
- `pjsua2_backend.py`:`registrarUri`/`idUri` 由新字段合成,删除 `_sip_uri`/`_host_of`。
- `sip.py`:`start_report` 默认目标由新字段合成并恒带端口(`sip:8000@192.168.1.116:5060`)。
- `app.py`:设置界面两处改为"地址 / 端口 / 分机号"三字段(QSpinBox 1-65535);`maybe_auto_start_sip` 完整性判定改用 `sip_host`+`sip_user`。
- UI 原型同步(网关地址/端口/分机号;座机地址/端口/分机号)。
- 测试:新增迁移(带端口/不带端口/新字段优先)+ 默认/roundtrip 断言更新;105 个测试通过(1 次 RPC 偶发建连失败与本次改动无关,重跑通过),mypy 通过;真实后端注册成功(port 5061)。