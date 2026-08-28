# 11 — 分机号作为 ATA 注册身份（仅 sip_number，移除 accounts）

**What to build:** `sip_number`（默认 1001）成为 ATA 注册时使用的**唯一** SIP 身份（AOR / 注册用户名）。HX4E 以该号码作为「注册用户名」向 TeleFlow 注册；TeleFlow 据此识别该注册，并在 UI / 日志中以「分机号」标识这次 ATA 注册，对齐用户既有的「分机号」心智模型（原 FreeSWITCH 下 ATA 就是按分机号注册的）。同时**移除 `accounts` 字段**——它与 `sip_number` 语义重叠且本软件只桥接一台 ATA，保留只会增加冗余（与 ticket 10 退役 `sip_port` 同一思路）。

**Blocked by:** 09 — 必须先有注册路径，身份识别才能挂接。

**Status:** wontfix

**Superseded by:** feature `sip-softphone` — TeleFlow 重新设计为纯 SIP 软电话客户端，`sip_number` 作为 ATA 注册身份的定位被客户端账号字段（`sip_user` 等）取代（见 `sip-softphone` 议题 02）。

- [ ] HX4E 以 `sip_number` 作为注册用户名（`Authorization` / AOR 的 user 部分）注册时，注册被接受并归到该分机号身份。
- [ ] 软件侧身份与 `sip_number` 对齐：配置 `sip_number` 改变后，期望的注册用户名随之改变（无需改其他字段）。
- [ ] 仪表盘 / 日志以 `sip_number` 标识 ATA 注册（而不只是显示 Contact URI），方便对应用户既有的分机号概念。
- [ ] **移除 `accounts` 字段**：从 `config.py` 的 `Settings` 删除；从设置 UI 删除「账号管理」相关控件（`app.py` 中的 `_accounts` / `_render_accounts` / 添加处理逻辑）；旧配置文件中残留的 `accounts` 键在加载时被忽略（向后兼容）。
- [ ] 更新 `tests/test_config.py` 中对 `accounts` 的断言（改为验证该字段已不存在 / 被忽略），既有配置兼容测试仍通过。
- [ ] 测试覆盖：以 `sip_number` 身份注册成功；`accounts` 字段在配置读写中被干净去除。

## 实现提示

- 注册身份的 user 部分与 `sip_number` 对齐；`accounts` 整字段删除，不再作为白名单或平权集合。
- 与 **10** 配合：身份识别发生在同一 `ata_registrar_port` 上，并与 `ata_password` 认证并存（术语已统一为 ATA，见 10）。
