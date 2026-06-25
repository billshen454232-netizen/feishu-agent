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
   ```
5. 权限里确保包含发送/回复消息相关权限，例如：
   ```text
   im:message
   im:message:send_as_bot
   ```
   具体权限以飞书开放平台提示为准，添加权限后需要发布/重新授权。
6. 把机器人加入群聊，群聊里 @ 机器人测试；私聊机器人也会响应。

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
