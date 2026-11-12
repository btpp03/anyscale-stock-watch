#!/usr/bin/env python3
"""Anyscale 库存监视（serverless cloud / hosted GPU 排队状态）。

信号（Bearer API key，无需 cookie）：
  GET /api/v2/aioa_cloud_waitlist/check_status  -> {"status":"IN_WAITLIST"|...}
  GET /api/v2/clouds/                           -> 已有 cloud 列表
  GET /api/v2/userinfo/                         -> 账号/组织核对

判定：waitlist 状态不再是 IN_WAITLIST，或账下已有 cloud ⇒ 视为「有货」。
只在状态变化时推 Telegram（state.json 落库提交），避免连发。

环境变量：
  AS_KEYS         JSON 数组 [{"name":"#4","key":"aph0_..."}]（也可直接放单个 key 字符串）
  TG_BOT_TOKEN / TG_CHAT_ID   可选
  TG_THREAD_ID   可选（Telegram 话题 id，DM 每会话会变，不要硬编码）
  INPUT_SOURCE   cron = 定时腿（无变化静默）；manual = 手动（推一条结果）
  DRY_RUN=1      只打印不发消息（本地调试）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://console.anyscale.com"
REPO_URL = "https://github.com/btpp03/anyscale-stock-watch"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
SOURCE = (os.environ.get("INPUT_SOURCE", "manual") or "manual").strip()
DRY = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "True")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/145.0 Safari/537.36")
BJ = timezone(timedelta(hours=8))


def log(*a):
    print(datetime.now(BJ).strftime("%m-%d %H:%M:%S"), *a, flush=True)


def load_keys():
    raw = os.environ.get("AS_KEYS", "").strip()
    if not raw:
        return []
    try:
        v = json.loads(raw)
        if isinstance(v, list):
            return [x for x in v if x.get("key")]
    except Exception:
        pass
    return [{"name": "#1", "key": raw}]


def api(path, key):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return -1, repr(e)


def tg(text):
    if DRY:
        log("DRY tg:", text.replace("\n", " | "))
        return
    tok = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not chat:
        log("tg skipped (no creds)")
        return
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    th = os.environ.get("TG_THREAD_ID", "").strip()
    if th:
        payload["message_thread_id"] = int(th)
    req = urllib.request.Request("https://api.telegram.org/bot%s/sendMessage" % tok,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            log("tg ok", r.status)
    except Exception as e:
        log("tg failed:", e)


def read_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def probe(name, key):
    st_u, b_u = api("/api/v2/userinfo/", key)
    who = ""
    if st_u == 200:
        try:
            d = json.loads(b_u)["result"]
            orgs = d.get("organizations") or [{}]
            who = "%s / %s" % (orgs[0].get("name", "?"), d.get("email", "?"))
        except Exception:
            who = "?"
    if st_u in (401, 403):
        return {"name": name, "error": "key 失效 (HTTP %s)" % st_u, "status": None, "clouds": None}
    st_w, b_w = api("/api/v2/aioa_cloud_waitlist/check_status", key)
    status = None
    if st_w == 200:
        try:
            status = json.loads(b_w)["result"]["status"]
        except Exception:
            status = "?"
    st_c, b_c = api("/api/v2/clouds/", key)
    clouds = None
    if st_c == 200:
        try:
            clouds = json.loads(b_c)["metadata"]["total"]
        except Exception:
            clouds = "?"
    return {"name": name, "who": who, "status": status, "clouds": clouds,
            "http": [st_u, st_w, st_c], "error": None}


def is_open(r):
    if r.get("error"):
        return False
    if isinstance(r.get("clouds"), int) and r["clouds"] > 0:
        return True
    s = (r.get("status") or "").upper()
    return bool(s) and s != "IN_WAITLIST" and s not in ("?", "UNKNOWN")


def main():
    keys = load_keys()
    if not keys:
        log("FATAL: AS_KEYS 未配置")
        return 2

    state = read_state()
    changed = []
    lines = []
    any_open = False
    dirty = False
    for k in keys:
        r = probe(k["name"], k["key"])
        if r.get("error"):
            log(r["name"], "ERROR", r["error"])
            lines.append("⚠️ %s %s" % (r["name"], r["error"]))
            continue
        log(r["name"], r.get("who"), "| waitlist:", r["status"], "| clouds:", r["clouds"])
        lines.append("%s %s — %s / clouds=%s" % (r["name"], r.get("who"), r["status"], r["clouds"]))
        sig = "%s|%s" % (r["status"], r["clouds"])
        old = (state.get(k["name"]) or {}).get("sig")
        if sig != old:
            dirty = True
            state[k["name"]] = {"sig": sig, "status": r["status"], "clouds": r["clouds"],
                                "ts": datetime.now(BJ).strftime("%Y-%m-%d %H:%M")}
        if is_open(r):
            any_open = True
            if sig != old:
                changed.append(r["name"])
        elif old and old != sig:
            changed.append(k["name"] + "(状态变化)")

    if dirty:
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)

    body = "\n".join(lines)
    warn = ""
    exp = os.environ.get("KEY_EXPIRES", "").strip()
    if exp:
        try:
            days = (datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=BJ) - datetime.now(BJ)).days
            if days <= 10:
                warn = "\n⚠️ API key %s 过期，还剩 %d 天，去 console → API keys 换新的并更新 secret AS_KEYS" % (exp, days)
        except Exception:
            pass
    if warn:
        lines.append(warn.strip())
        body = "\n".join(lines)
    if any_open:
        head = "🚨 Anyscale 有货了！立即去开：https://console.anyscale.com/cloud-setup"
        if SOURCE != "cron" or changed:
            tg("%s\n\n%s\n\n来源:%s | %s" % (head, body, SOURCE, REPO_URL))
        else:
            log("open, but already alerted (silent)")
        return 0

    if changed:
        tg("🔔 Anyscale 排队状态变化\n\n%s\n\n来源:%s | %s" % (body, SOURCE, REPO_URL))
    elif warn and state.get("_exp_warn") != datetime.now(BJ).strftime("%Y-%m-%d"):
        state["_exp_warn"] = datetime.now(BJ).strftime("%Y-%m-%d")
        with open(STATE_PATH, "w") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        tg("ℹ️ Anyscale 监视正常（仍排队）\n\n%s\n\n%s" % (body, REPO_URL))
    elif SOURCE != "cron":
        tg("✅ Anyscale 监视正常（仍排队）\n\n%s\n\n%s" % (body, REPO_URL))
    else:
        log("no change, silent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
