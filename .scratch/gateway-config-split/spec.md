# 网关/电话汇报 SIP 配置改为 域名或IP + 端口 + 分机号

## 背景

当前配置把"与网关的连接"和"对外打电话目标"都存成单个 SIP URI 字符串(`sip_server: "sip:192.168.1.189:5060"`、`report_target: "sip:8000@192.168.1.116:5060"`),设置界面要手填完整 URI,容易输错。用户希望拆成 **域名或IP、端口、分机号** 三个字段分别填写。

## 决定

- 网关注册配置:`sip_server`(URI)拆为 `sip_host`(域名或 IP)+ `sip_server_port`(端口,默认 5060);`sip_user` 继续作为分机号(SIP 账号)。
- 对外打电话(电话汇报/座机)配置:`report_target`(URI)拆为 `report_host`(域名或 IP)+ `report_port`(端口,默认 5060)+ `report_extension`(座机分机号)。
- 设置 UI 与全局不变量不采用 URI 输入框,改为"地址 / 端口 / 分机号"三字段(QSpinBox 端口 1-65535)。
- 旧配置迁移:`ConfigStore.load` 解析旧 URI(兼容 `sip:user@host:port`、`host:port`、无端口、IPv6 方括号)拆分到新字段。
- 后端(pjsua2)与电话汇报调用方由新字段合成完整 URI,不再依赖字符串解析(`_sip_uri`/`_host_of` 删除)。

## 范围

1. config.py:字段拆分 + 旧 URI 迁移 + 默认值。
2. pjsua2_backend.py:registrarUri/idUri 由 `sip_host`+`sip_server_port`+`sip_user` 合成。
3. sip.py:`start_report` 默认目标由 `report_host`+`report_port`+`report_extension` 合成。
4. app.py:设置界面两处改三字段;`maybe_auto_start_sip` 完整性判定改 `sip_host`+`sip_user`。
5. 测试与 UI 原型同步。