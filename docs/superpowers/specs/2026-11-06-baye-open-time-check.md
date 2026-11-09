# 新服开服配置检查（霸业时间 / 选服开服时间）

> **状态：MVP 已实现（异步提交 + 截图回帖，自动判定未接入）**
> **日期：2026-11-06**
> **关联计划：** 项目计划看板（开服配置检查）
> **来源接口：** 同事提供的「霸业时间检查」UI 自动化 API（执行机 `http://<执行机IP>:8899`，`POST /api/baye-checks`）

## 1. 目标

新 S1 服上线前，用机器人替代人工：在飞书群说「检查 1800 服开服配置」，机器人用预先登记的账号在真机上登录目标服，截取「选服列表（含开服时间）」与「霸业页面」两张图，判断开服时间、霸业时间配置是否正确，结果回到飞书。

## 2. 接口要点（来自同事文档）

| 项 | 内容 |
| --- | --- |
| 提交 | `POST /api/baye-checks {serial, account, password, server}` → 202 + 任务 `id`；设备占用 409、参数 / 设备离线 400 |
| 查询 | `GET /api/baye-checks/{id}`，建议每 3 秒；`status`：running / pass / fail / stopped / interrupted |
| 结果 | `pass` 时两张截图：`server`（选服列表，含开服时间）、`baye`（霸业页面）；`time_calculation` 目前固定 `not_implemented` |
| 副作用 | 每次 `pm clear com.bilibili.nslg` 清除应用数据并重新登录；缺包会安装最新 OB 正式包 |
| 时长 | 安装 + 检查最长 60 分钟 |
| 鉴权 / 幂等 | 内网无鉴权；**无幂等键**，重复 POST 会再跑一次（含清数据） |

## 3. 架构（已实现）

```text
飞书「检查 1800 服开服配置」→ Gateway 规划（catalog 模式 Q，合同 baye-open-time-check，backend=baye_check）→ 确认卡片
→ Worker 认领：本地重新校验 → 按服号查账号表 → POST /api/baye-checks → 立即回「已提交（任务 ID）」结束本次作业
→ Worker 后台轮询（状态写 data/baye_checks_pending.json，重启后续查）→ 终态后下载两张截图
→ POST Gateway /internal/worker/jobs/{message_id}/followup → Gateway 上传图片（im:resource）→ 在原话题回复结果卡片
```

- **不占用 Worker 队列**：提交后作业即结束，检查本身 3–10 分钟在后台跑；其他操作照常执行。
- **单独发起、固定国服路由**：带 backend 的合同只能单步计划；路由固定 `cn`（不按服号归属判断）。
- **不进 GS-1 runner manifest**：虚拟路径 `/home/serverGeneralScript/_bot/baye_open_time_check` 只用于登记。
- **账号表只放 Worker 本地**：`config/baye_accounts.xlsx`（或 `.csv`），列：服号、完整服务器名、账号、密码、设备（可空，空则用 `default_serial`）。模板见 `config/baye_accounts.example.csv`，真实表被 `.gitignore` 排除。密码只出现在提交请求体中，不进 Gateway、AI、飞书卡片、日志或 git。
- **不自动重发**：接口无幂等键。提交无响应 / 5xx → 「结果未知」，提示先看执行机控制台；409（手机占用）/ 400 → 明确失败，未开始。
- **回帖去重**：Gateway 按 check_id 去重（进程内），只接受同一 Worker、且计划是开服检查的作业；最多 2 张图、每张 ≤ 8 MB。

## 4. 自动判定（已实现，等执行机返回 OCR 文字）

**期望值来自飞书消息**：`检查 1800 服开服配置，开服时间 11月9日12点`。规划器按通用 `{timestamp}` 规则写 `2026-11-09T12:00`，
系统确定性换算成北京时间时间戳（合同 `baye-open-time-check-with-time`，参数 `["1800", "<timestamp>"]`），确认卡片显示换算后的时间。
消息里没有时间 → 单参数合同，只发截图 + 人工核对提示，不调用 AI。

**规则**（用户给出）：选服列表开服时间 = 期望开服时间；霸业第一阶段持续 4 小时，所以第一阶段结束 = 期望开服时间 + 4 小时
（页面显示「已于 X 完成」时比 X；显示剩余时间 R 时为 截图时间 + R）。容差 ±5 分钟。

**分工**：
- OCR：执行机接口返回识别文字（用户决定；Worker 不装 OCR 包——直连 pypi.org 过慢）。
- AI（Gateway 的 interpretation 模型池）：**只读值**——从 OCR 文字里读出 `open_time_seen`、`baye_end_time`（倒计时由 AI 加到截图时间上）、依据原文、说明。
- 程序：用 `parse_time_text` 解析 AI 读出的时间并与期望值比较，得出 正确 / 不正确 / 无法判定。读不出 → 无法判定，不猜。
- Gateway 收到回帖后先上传截图并立即应答 Worker（Worker 请求超时 30 秒），AI 判定与卡片在后台完成；AI 失败 / 超时只发截图并注明原因。

**执行机需要返回的 OCR 字段**（Worker 兼容以下任一形状，按截图名对应 `server` / `baye`）：

```json
"ocr": {"server": "整张截图识别出的全部文字（按行用换行连接）", "baye": "..."}
"ocr": {"server": {"lines": ["本服开启时间:", "2026年11月9日12:00"]}, "baye": {"lines": [...]}}
"screenshots": [{"name": "server", "label": "...", "url": "...", "ocr_text": "..."}]
```

每张截图文字截断到 4000 字；只含游戏界面文字，不含账号密码。

## 5. Worker 配置

```json
"baye_check": {
  "enabled": true,
  "base_url": "http://<执行机IP>:8899",
  "default_serial": "<设备 serial>",
  "accounts_path": "/var/lib/feishu-baye/baye_accounts.csv"
}
```

依赖：`openpyxl`（读 xlsx；用 csv 可不装）。CSV 可以是 UTF-8 或 Excel 默认的 GBK；服号列可写 `S11800`，按「完整服务器名」开头的数字（`1800服 …`）也能查到。

**账号表更新（Jenkins）**：Worker 机 Jenkins 任务 `feishu_baye_accounts_upload`，「Build with Parameters」上传 CSV → `deployment/worker/install_baye_accounts.py`（系统 python3，只用标准库）校验列与必填项 → 原子写入 `/var/lib/feishu-baye/baye_accounts.csv`（jenkins 所有，600）。校验失败不覆盖旧表；构建日志只打印服号；构建目录里 Jenkins 自带的上传副本会被删除。Worker 每次检查都重新读表，更新无需重启。

## 6. 后续

1. 自动判定（OCR + 期望值比较）。
2. 账号表从企业微信文档同步（当前手动更新）。
