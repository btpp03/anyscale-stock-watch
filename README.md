# anyscale-stock-watch

Anyscale（KubeRay Connect Launch）免费额度账号的**库存/排队监视**：一旦 serverless cloud 或 hosted GPU 名额放开，立刻推 Telegram，直接点链接去开。

## 为什么能查

Anyscale 控制台的 session cookie 几分钟就失效，magic link 也没法无人值守，所以走**控制台 API key**（`aph0_...`）：

```
GET /api/v2/aioa_cloud_waitlist/check_status   -> {"status":"IN_WAITLIST"|...}
GET /api/v2/clouds/                            -> 已开的 cloud 数
GET /api/v2/userinfo/                          -> 账号/组织核对（key 失效会 401）
```

判定：`status` 不再是 `IN_WAITLIST`，或账下 cloud 数 > 0 ⇒ 有货。

> 注意：API key 在控制台「API keys」里创建，**最长 30 天**到期，到期前要重新生成并更新 secret `AS_KEYS`。想要不过期的，得建 service account（`anyscale service-account create`）再用它的 key。

## Secrets

| 名称 | 值 |
|---|---|
| `AS_KEYS` | JSON 数组：`[{"name":"#4","key":"aph0_..."}]`（多账号就多数组项） |
| `TG_BOT_TOKEN` | Telegram bot token |
| `TG_CHAT_ID` | 你的 chat id（`644320820`） |

## 定时腿（本账号 GA 原生 schedule 不 fire）

`cron-job.org` → `POST https://api.github.com/repos/btpp03/anyscale-stock-watch/actions/workflows/watch.yml/dispatches`

```json
{"ref":"main","inputs":{"source":"cron"}}
```

Header：`Authorization: Bearer <GitHub PAT>`、`Accept: application/vnd.github+json`，频率 10 分钟。
`source=cron` 时**无变化静默**，只有状态变化才推；手动 dispatch（留空 source）每次都推一条结果。

## 还没做的

- 「自动开实例」：`POST /api/v2/clouds/` 的 payload 要等第一次被放行才拿得到，现在硬猜只会 422。等真放行、抓到一次建云请求后，在 `watch.py` 里加一步即可。
- 只会看**当前账号**的排队状态；`IN_WAITLIST` 是账号级，不是你排第几。
