# Worker 企微表格配置页

## 当前状态

配置页已部署到 JKS Worker，Nginx 80 端口的 `/wecom-bot/` 返回已验证的登录页及配置接口。已测试未登录 HTTP 401、登录 HTTP 200 和跨站写入 HTTP 403。企微 CLI 1.3.4 可以在服务账号下运行。扫码测试起初返回 `PermissionError`，因为二维码输出路径位于 CLI 保护的授权配置目录。现将每次授权的工作目录用于保存二维码，授权配置放在其 `config/` 子目录。已通过 Nginx 验证页面接口返回有效 PNG。用户已完成扫码并通过「保存并验证」确认 Worker 可读取所选表格。值班查询接受机器人所在的飞书群消息。本地已接入飞书入口、值班队列与 Worker 回传。合成数据验证了多群查询、机器人提及、队列隔离、重复回传及失败路径。用实际授权表验证了「日期」和「值班人一」两列，可以查到当天记录。用户已确认飞书值班查询跑通。新版配置页支持维护多张表，待部署到 JKS。

## 配置与数据流

- `tools/wecom_bot/admin.mjs` 提供配置页。后端监听 Worker 的 `127.0.0.1:18409`。Nginx 可在现有 80 端口的站点内使用 `/wecom-bot/` 反代，转向 `http://127.0.0.1:18409/` 时去掉路径前缀，代理 Host 固定为 `127.0.0.1:18409`。服务的 `WECOM_ADMIN_PUBLIC_ORIGIN` 配置为浏览器实际访问的来源地址（协议和主机，必要时含端口）。配置页使用独立管理员凭据。
- `tools/wecom_bot/table-settings.mjs` 管理表格列表。每条配置包含名称、链接和用途。新增或编辑时试读该表，验证通过后写入 systemd 专用状态目录。旧配置中的值班表会作为列表第一条。飞书值班查询从这一个工作簿读取各子表，按子表名称列出值班结果。链接中的 `tab` 只用于配置页试读对应子表。
- 所有表共享一份企微 CLI 授权。重新扫码后，验证新授权能读取全部已登记的表格，再切换到新授权。
- `src/duty_queries.py` 验证飞书群消息及对本机器人的提及，将查询日期排入独立来源队列。`tools/wecom_bot/duty-poller.mjs` 主动向 Gateway 领取请求，通过 `sheet.mjs` 查值班人。Gateway 使用返回的匹配记录回复原消息。

## 部署及验收

服务单元模板位于 `deployment/worker/wecom-bot.service`，程序安装到 `/opt/wecom-bot`，已保存的链接和授权由 `/var/lib/wecom-bot` 持久化。

- Gateway 的服务环境配置 `FEISHU_DUTY_BOT_OPEN_ID`（机器人 Open ID）和 `FEISHU_DUTY_TOKEN`（独立凭据）。同时配置飞书事件验证 Token。
- Worker 的 `/etc/wecom-bot/bot.env` 增加 `FEISHU_DUTY_GATEWAY_ORIGIN`（Gateway HTTPS 来源地址）和同一份 `FEISHU_DUTY_TOKEN`。
- 发布 Gateway 和 Worker 代码后，在机器人所在的群 @ 机器人发「今天谁值班」，用当天在线表核对。移除 Gateway 的 `FEISHU_DUTY_TOKEN` 可以停用入口。

## 复杂表格问答

本地已有 `@机器人 查表 <问题>` 的接单、队列隔离及原消息回传。Gateway 从 `FEISHU_WECOM_TABLE_IDS` 读取明确启用的表格 ID，只接受来自这些表的查询结果。返回的答复不写入知识库归档。`tools/wecom_bot/table-reader.mjs` 通过服务的私有 Unix socket 读取已登记的工作簿，返回带坐标的有限范围，保留空白位置，并区分部分覆盖和读取失败。

`team-kb` 的查表轮询在本机按名称和用途选择数据源，送入模型的是脱敏问题、所选表格的有限摘录及实际坐标。回复由程序从已读取的单元格位置生成引用，部分覆盖时显示提示。

试用在 Gateway 的 `FEISHU_WECOM_TABLE_IDS` 填写两张已启用表的登记 ID。在 `team-kb` 的本地环境文件将 `WECOM_TABLE_AI_ORIGINS` 设为经确认可处理企微资料的公司 HTTPS 来源地址，多个地址用逗号分隔。查表从现有 `/opt/team-kb/ai.json` 选用匹配来源的模型。

发布时更新 Gateway，JKS 安装 `tools/wecom_bot/` 的运行文件及 `wecom-bot.service`，并同步 `tools/team_kb/` 到知识库服务的运行目录。三个服务按各自的发布流程重启。发布后在机器人所在群发送「@机器人 查表 本周还有哪些测试没完成？」，用回复中的子表和单元格位置对照原表。

代码已通过本地模拟验收，JKS 新版发布以及两张真实表的模型解读仍待验证。
