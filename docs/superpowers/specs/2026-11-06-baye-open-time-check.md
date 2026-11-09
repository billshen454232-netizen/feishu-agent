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

## 4. 「配置是否正确」的判定（后续阶段）

MVP 不做 OCR、不做自动判定，卡片附人工核对提示。期望规则（用户给出）：

- 开服时间：选服列表显示的开服时间应等于实际开服时间；
- 霸业时间：第一个霸业阶段持续 4 小时；内部账号开服前即可进入，所以 **霸业剩余时间 ≈ 开服时间 + 4 小时 − 截图时间**。

后续可选：Worker 本地 OCR（rapidocr + onnxruntime，已同意安装）或请同事接口返回识别文字，再与 Server Manager `server-config` 的开服时间做确定性比较。

## 5. Worker 配置

```json
"baye_check": {
  "enabled": true,
  "base_url": "http://<执行机IP>:8899",
  "default_serial": "<设备 serial>",
  "accounts_path": "config/baye_accounts.xlsx"
}
```

依赖：`openpyxl`（读 xlsx；用 csv 可不装）。

## 6. 后续

1. 自动判定（OCR + 期望值比较）。
2. 账号表从企业微信文档同步（当前手动更新）。
