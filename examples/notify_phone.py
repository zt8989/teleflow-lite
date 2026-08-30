# -*- coding: utf-8 -*-
"""
WorkBuddy Stop Hook: 检测汇报标记 -> TeleFlow RPC 呼叫座机播报
========================================================
由 WorkBuddy hooks(Stop 事件) 调用, 通过 stdin 接收 JSON payload:
  {"last_assistant_message": "...", "session_id": "...", ...}

这是生产版 hook 脚本(曾部署在 ~/sip-lab/notify_phone.py, 现已收进仓库):
相比精简参考实现 examples/report_hook.py, 它额外处理 WorkBuddy payload
损坏 JSON(缺逗号)的正则兜底、会话 jsonl(transcript_path) 提取与重试、
以及 hook 进程 stderr 不可见时的 debug 日志落盘。

逻辑:
  1. 提取 last_assistant_message
  2. 不含标记 __PHONE_REPORT__ -> 静默退出(exit 0, 不打扰)
  3. 含标记 -> 取汇报文本 -> POST TeleFlow RPC /v1/report
     (TeleFlow 负责 TTS 合成 + 外呼座机播放 + EOF 自动挂断;
      RPC 地址/令牌从 ~/.config/teleflow/config.json 与配置保持一致)

用法(由 hook 配置调用, 也可手动测试):
  echo '{"last_assistant_message":"... __PHONE_REPORT__"}' | python notify_phone.py
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

MARK = "__PHONE_REPORT__"
MAX_TEXT = 1500                              # 播报文本上限(字), 防超长
TELEFLOW_CONFIG = os.path.join(
    os.path.expanduser("~"), ".config", "teleflow", "config.json"
)
DEBUG_LOG = r"C:/Users/zhouteng/sip-lab/hook_debug.log"   # hook stdin 参数完整记录


def log(msg):
    sys.stderr.write(f"[notify_phone] {msg}\n")
    sys.stderr.flush()


def debug_log(msg):
    """hook 进程的 stderr 不落盘(实测), 写文件是唯一可靠诊断记录。
    内容可能含非法 Unicode/控制字符, 写入前做 replace 转义, 避免整行丢失。"""
    try:
        msg = msg.encode("utf-8", errors="replace").decode("utf-8")
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def robust_parse(raw):
    """WorkBuddy 的 hook payload 实测为损坏 JSON(缺逗号分隔符)。
    返回 (payload, broken): broken=True 表示原始 JSON 解析失败,
    此时 payload 字段不可全信(正则提取可能截断), 调用方应优先走 transcript 兜底。"""
    if not raw.strip():
        return {}, False
    try:
        return json.loads(raw), False
    except Exception:
        debug_log("JSON 解析失败, 降级为正则提取关键字段")
        payload = {}
        m = re.search(r'"transcript_path"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            payload["transcript_path"] = m.group(1)
        m = re.search(r'"session_id"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            payload["session_id"] = m.group(1)
        m = re.search(r'"last_assistant_message"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if m:
            payload["last_assistant_message"] = m.group(1)
        debug_log(f"正则提取结果: {json.dumps(payload, ensure_ascii=False)[:500]}")
        return payload, True


def clean_markdown(text):
    """清理 Markdown 语法符号, 使 TTS 播报只念正文(不念星号/井号/竖线等)"""
    # 行内代码: `code` -> code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # 粗体: **x** / __x__ -> x
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.S)
    # 斜体: *x* / _x_ -> x
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text, flags=re.S)
    text = re.sub(r"(?<!_)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", text, flags=re.S)
    # 删除线: ~~x~~ -> x
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.S)
    # 链接: [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # 标题标记: 行首 #+ -> 去掉
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    # 无序列表: 行首 - / * / + -> 去掉
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    # 有序列表: 行首 1. / 1、 -> 去掉
    text = re.sub(r"^\s*\d+[.、)]\s+", "", text, flags=re.M)
    # 引用: > -> 去掉
    text = re.sub(r"^>\s*", "", text, flags=re.M)
    # 分隔线: --- / *** / ___ -> 删整行
    text = re.sub(r"^\s*([-*_]\s*){3,}\s*$", "", text, flags=re.M)
    # 表格竖线 | -> 顿号(读作停顿)
    text = re.sub(r"\|", "，", text)
    # 多余空行压缩
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_report(message):
    """提取标记前的汇报文本, 去掉标记行、Markdown 符号与首尾空白"""
    text = message.replace(MARK, "").strip()
    # 去掉可能包裹的代码块标记行
    text = re.sub(r"^```.*$", "", text, flags=re.M).strip()
    text = clean_markdown(text)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "。以上为汇报摘要。"
    return text


def rpc_report(text):
    """调用 TeleFlow RPC /v1/report 播报座机

    TeleFlow 完成全部下游工作: TTS 合成(带缓存) + 外呼座机 + 播放 + EOF 自动挂断。
    目标/语音等参数取 TeleFlow 自身配置(report_extension/sip_host/tts_voice),
    令牌与端口也从 config.json 读取, 改设置后无需改本脚本。
    """
    if not os.path.exists(TELEFLOW_CONFIG):
        raise RuntimeError(f"TeleFlow 配置不存在: {TELEFLOW_CONFIG}")
    with open(TELEFLOW_CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    port = int(cfg.get("rpc_port") or 8731)
    token = cfg.get("rpc_token") or ""
    if not token:
        raise RuntimeError("TeleFlow 未配置 rpc_token, 无法鉴权")
    debug_log(f"TeleFlow 配置: rpc_port={port}")

    url = f"http://127.0.0.1:{port}/v1/report"
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            reply = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8") or "{}").get("error", "")
        except Exception:
            pass
        raise RuntimeError(f"RPC 返回 {e.code}: {detail or e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"无法连接 TeleFlow RPC(程序是否在运行?): {e.reason}")
    debug_log(f"RPC /v1/report 接受: {json.dumps(reply, ensure_ascii=False)}")
    return reply.get("report_id", "?")


def extract_last_assistant_from_transcript(path):
    """兜底: 从 WorkBuddy 会话 jsonl(transcript_path) 提取最后一条 assistant 消息文本

    WorkBuddy 的 Stop hook payload 在 isFinalOutput 不满足时不会填充
    last_assistant_message(实测多次触发均缺失), 故回退读会话文件。
    """
    if not path or not os.path.exists(path):
        debug_log(f"transcript 不存在: {path}")
        log(f"transcript 不存在: {path}")
        return ""
    text = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") != "message" or d.get("role") != "assistant":
                    continue
                content = d.get("content") or []
                parts = []
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            parts.append(c.get("text", ""))
                if parts:
                    text = "".join(parts)   # 逐行覆盖, 保留最后一条
    except Exception as e:
        log(f"transcript 读取失败: {e}")
        debug_log(f"transcript 读取失败: {e}")
    if text:
        debug_log(f"transcript 提取成功: 长度={len(text)}, 尾部200字={text[-200:]!r}")
    else:
        debug_log("transcript 未提取到 assistant 消息文本")
    return text


def main():
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception as e:
        debug_log(f"stdin 读取失败: {e}")
    debug_log(f"=== hook 调用 === stdin_raw_len={len(raw)}")
    debug_log(f"stdin 原始内容(repr): {raw[:3000]!r}")

    payload, broken = robust_parse(raw)
    debug_log(f"payload 全部字段: {json.dumps(payload, ensure_ascii=False)[:3000]}")

    message = payload.get("last_assistant_message") or ""
    source = "payload"
    if not message or broken:
        tp = payload.get("transcript_path") or ""
        debug_log(f"消息为空或 payload 损坏(broken={broken}), 尝试 transcript_path={tp}")
        message = extract_last_assistant_from_transcript(tp)
        source = "transcript"
        if MARK not in message:
            # hook 在会话结束瞬间触发, jsonl 最后消息可能尚未写完, 等待重试
            debug_log("首次提取无标记, 等待2秒重试(应对会话文件写入时序)")
            time.sleep(2)
            message = extract_last_assistant_from_transcript(tp)
    debug_log(f"消息来源={source}, 长度={len(message)}, 首200字={message[:200]!r}, 含标记={MARK in message}")

    if MARK not in message:
        debug_log("无标记, 静默退出")
        log(f"无标记, 静默退出 (消息来源={source}, 长度={len(message)})")
        sys.exit(0)  # 无标记: 静默退出, 不打扰

    debug_log("检测到标记, 开始播报流程")
    log(f"检测到汇报标记(来源={source}), 会话 {payload.get('session_id', '?')[:8]}")
    text = extract_report(message)
    if not text:
        log("汇报文本为空, 跳过")
        sys.exit(0)

    log(f"文本就绪({len(text)}字), 调用 TeleFlow RPC 播报座机…")
    report_id = rpc_report(text)
    log(f"汇报已提交: report_id={report_id}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"异常: {e}")
        sys.exit(1)