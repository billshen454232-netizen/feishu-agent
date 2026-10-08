# Feishu AI Bot Backend

一个最小可用的 Python/FastAPI 飞书 AI 机器人后端。

## 功能

- 接收飞书开放平台事件回调：`POST /feishu/events`
- 支持飞书 URL 校验 `challenge`
- 私聊机器人时自动回复
- 群聊里只有 @ 机器人时回复
- 按 `chat_id` 在内存中保留最近 N 条上下文
- 模型配置使用 JSON
- 支持两类模型协议：
  - `openai_compatible`：调用 `{base_url}/chat/completions`
  - `claude_messages`：调用 `{base_url}/messages`
- 使用飞书 OpenAPI 回复消息，不依赖 `lark-cli` 子进程
- 可选接入 Obsidian dual-chain vault 知识库，优先用本地索引和双链资料增强回答

## 本地安装

```bash
python -m venv .venv
source .venv/Scripts/activate  # Windows Git Bash
python -m pip install -r requirements.txt
```

如果不使用虚拟环境，也可以直接：

```bash
python -m pip install -r requirements.txt
```

## 配置

复制示例配置：

```bash
cp config/config.example.json config/config.json
```

编辑 `config/config.json`：

```json
{
  "feishu": {
    "app_id": "你的飞书应用 App ID",
    "app_secret": "你的飞书应用 App Secret",
    "verification_token": "飞书事件订阅 Verification Token",
    "encrypt_key": "第一版暂不支持加密事件，建议先留空",
    "base_url": "https://open.feishu.cn"
  },
  "ai": {
    "provider": "openai_compatible",
    "base_url": "https://api.example.com/v1",
    "api_key": "你的模型 API Key",
    "model": "你的模型名",
    "system_prompt": "你是一个飞书里的 AI 助手，请用简洁、准确的中文回答。",
    "temperature": 0.2,
    "max_tokens": 2048,
    "timeout_seconds": 60
  },
  "knowledge_base": {
    "enabled": true,
    "type": "dual_chain_vault",
    "vault_path": "D:/MySecondBrian/vault",
    "min_score": 3,
    "max_results": 5,
    "max_context_chars": 12000,
    "fallback_when_miss": "answer_with_notice"
  }
}
```

### OpenAI-compatible 配置

适用于大多数中转服务、本地模型网关、OpenAI 兼容服务：

```json
"ai": {
  "provider": "openai_compatible",
  "base_url": "https://api.example.com/v1",
  "api_key": "sk-xxx",
  "model": "your-model"
}
```

实际请求地址会是：

```text
{base_url}/chat/completions
```

### Claude Messages 配置

适用于 Anthropic Claude Messages API：

```json
"ai": {
  "provider": "claude_messages",
  "base_url": "https://api.anthropic.com/v1",
  "api_key": "sk-ant-xxx",
  "model": "claude-opus-4-8"
}
```

实际请求地址会是：

```text
{base_url}/messages
```

### Obsidian dual-chain vault 知识库

第一版不需要启动 Obsidian，也不需要 Obsidian 插件进程。后端会直接读取 vault 目录里的索引和 Markdown 文件：

```text
vault_path/
├─ indexes/articles.json
├─ indexes/concepts.json
├─ indexes/qa.json
├─ articles/
├─ concepts/
└─ qa/
```

启用配置：

```json
"knowledge_base": {
  "enabled": true,
  "type": "dual_chain_vault",
  "vault_path": "D:/MySecondBrian/vault",
  "min_score": 3,
  "max_results": 5,
  "max_context_chars": 12000,
  "fallback_when_miss": "answer_with_notice"
}
```

工作方式：

1. 优先搜索 `indexes/qa.json`。
2. 再搜索 `indexes/concepts.json`，通过概念关联到文章。
3. 再搜索 `indexes/articles.json`。
4. 命中后读取对应 `.md` 文件，把本地资料作为上下文交给 AI 回答。
5. 未命中时，会要求 AI 先说明“本地知识库没有找到足够相关的资料”，再基于通用知识回答。

## 本地可读日志

机器人每次完成脚本资料问答、生成/拒绝执行计划或回传执行结果后，都会按本机日期写入 Markdown：

```text
logs/conversations/YYYY-MM-DD.md
```

直接用资源管理器或 VS Code 打开当天文件即可查看。每条记录包含本地时间、用户消息、机器人回复、问答资料来源；执行计划还会保留仅在本机可见的 AI 原始输出、本地结构校验结果和自动纠错过程。该目录已被 Git 忽略，不会被提交。

默认配置：

```json
"conversation_log": {
  "enabled": true,
  "directory": "logs/conversations",
  "max_text_chars": 12000
}
```

## 启动

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

如果没有创建 `config/config.json`，`/health` 会返回 `missing_config`，这是为了方便测试和导入模块。

## 暴露公网地址

飞书事件回调需要公网 HTTPS URL。本地调试可以用 ngrok、cloudflared 或 frp。

示例：

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

然后把生成的 HTTPS 地址配置到飞书开放平台：

```text
https://你的公网域名/feishu/events
```

## 飞书开放平台配置

在飞书开放平台应用后台：

1. 确认机器人能力已启用。
2. 事件订阅里配置 Request URL：

   ```text
   https://你的公网域名/feishu/events
   ```

3. 第一版建议先不要启用 Encrypt Key 加密事件。
4. 订阅事件：

   ```text
   im.message.receive_v1
   card.action.trigger
   ```

   `card.action.trigger` 用于处理待确认运维卡片的“确认执行 / 取消”按钮；服务端必须在 3 秒内返回。

5. 权限里确保包含发送/回复/更新消息相关权限，例如：

   ```text
   im:message
   im:message:send_as_bot
   ```

   交互卡片需要在飞书开放平台按实际提示补齐“更新消息”相关权限。具体权限以平台提示为准，添加权限或事件后需要发布/重新授权。
6. 把机器人加入群聊，群聊里 @ 机器人测试；私聊机器人也会响应。

## 测试服操作交互卡片

群内自然语言请求生成的计划会以卡片展示完整步骤、脚本名和参数，并提供“确认执行 / 取消”按钮。按钮仅引用本地数据库中已存储的待确认计划，不能传入或修改脚本路径、参数或 Shell 命令。只有原请求人可以确认或取消；确认后操作仍经过计划解析、注册表合同和本地防火墙校验，再转入后台提交 Muliu。卡片回调不等待 Muliu 结果，避免超过飞书 3 秒回调限制。

## 部署模式（单机模式 vs Gateway/Worker 分离模式）

由于 Muliu 服务通常部署在公司内网、公网云服务器无法直接建立 HTTP 连接，系统支持通过 `runtime.role` 拆分运行职责：

### 1. 单机模式（`runtime.role: "local"`）
- 本机单进程运行飞书事件、AI 规划与 MuliuExecutor 执行器。
- 适合本地开发、单机验证与自动化测试。示例见 `config/config.example.json`。

### 2. 公网网关（`runtime.role: "gateway"`）
- 部署在阿里云等公网云服务器（例如 `/opt/feishu-agent`）。
- 职责：接收飞书事件回调、生成 AI 计划、投递待确认卡片、管理 SQLite 任务队列、提供内部受控认领接口。
- **安全保障**：无需配置、也无需持有 Muliu 内网地址、账号或密码。配置示例见 `config/config.gateway.example.json`。

### 3. 内网执行器（`runtime.role: "worker"`）
- 部署在可连通 Muliu 的内网机器（例如内网 Jenkins Runner 服务器）。
- 职责：只主动出站向 Gateway 发起 HTTPS 请求认领已确认计划；使用防火墙重新核验计划合法性；通过本地 `MuliuExecutor` 访问 Muliu 并回传终态。
- **安全保障**：无需开放任何公网入站端口，无需配置飞书 App Secret 或 AI API Key。配置示例见 `config/config.worker.example.json`。

## 测试

```bash
python -m pytest -v
python -m compileall src tests
```

## 当前限制

- 上下文只保存在内存中，服务重启后会丢失。
- 第一版不处理飞书加密事件；如启用 Encrypt Key，需要后续补解密逻辑。
- 第一版只处理文本消息。
- 群聊只在消息包含飞书 mentions 字段时响应。
