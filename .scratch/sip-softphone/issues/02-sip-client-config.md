# 02 — 配置模型改为 SIP 客户端账号

**What to build:** 设置项从「服务端」语义改为「客户端」语义：用户在一处填写自己的 SIP 注册服务器、用户名、密码，TeleFlow 据此作为客户端注册。音频设备 / 钩子命令等字段保持不变。

**Blocked by:** 01 — 移除 PBX 注册器（先清掉 `ata_*` 服务端字段，再引入客户端字段，保证套件分批常绿）。

**Status:** ready-for-agent

- [ ] `Settings` 新增客户端字段：`sip_server`（注册 / 代理 URI，例如 `sip:provider.example.com`）、`sip_user`（AOR / 认证用户名）、`sip_password`。
- [ ] 移除 `ata_port` / `ata_registrar_port` / `ata_password` / `sip_number` 服务端字段。
- [ ] 旧配置含 `ata_*` 时尽量迁移到新客户端字段（`ata_port`/`gateway_port`→若作为服务器地址可映射提示；`sip_number`→`sip_user`）。
- [ ] 新字段 load/save 往返保持；`test_config.py` 更新断言；全量套件绿色。
