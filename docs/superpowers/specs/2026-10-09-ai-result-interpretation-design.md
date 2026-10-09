# 架构设计方案：Gateway 端 AI 结果智能研读与精准解答层

**状态：待实施（准备接棒）**  
**日期：2026-10-09**  
**设计目标**：在飞书运维机器人的结果投递链路中增加大模型后处理层。当执行器完成底层脚本并回传数据后，由阿里云 Gateway 的 AI 服务结合用户的原始问题与业务资产自动研读日志，直接给出精准、提炼后的业务结论，彻底告别“把整段原始机器 JSON 或长日志直接甩给用户”。

---

## 1. 业务痛点与诉求

### 1.1 现状痛点
用户在飞书提问：“5010 服务器是否存在 avatar_patch_79941 patch？”
- **现状流转**：AI 调度 `cc_patch.py -s 5010 -ck`，Muliu 返回了包含 17 个补丁的长 JSON。
- **痛点体现**：机器人直接把 17 个补丁的原始 JSON 输出到群里，用户仍然需要用肉眼去一页页翻找目标补丁，失去了 AI 作为“智能助手”的意义。

### 1.2 预期效果
用户发起目标导向的提问后，AI 自动研读日志，首句话直接给出确切答案：
```text
✅ 执行完成：查询 5010 服 Patch 列表

🤖 AI 检查结论：
【存在】已在 5010 服务器当前 Patch 列表中找到该补丁：
  • 补丁名称：avatar_patch_79941.lua
  • 列表序号：第 13 项
  • 补丁类型：avatar (order: 1)
  • 校验状态：文件存在，状态正常

（共检索了 17 个补丁，除该补丁外其余文件均正常）
```

---

## 2. 架构设计与职责边界

```text
[飞书用户 @机器人 提问]
  │ （例如："5010 是否存在 avatar_patch_79941" 或 "查看海外所有DEV环境代码版本"）
  ▼
[阿里云 Gateway - Planning 阶段]
  │ 结合 muliu_script_catalog.md 与 server_environments.json 生成执行计划
  ▼
[群内卡片确认] -> [内网 Worker 领取] -> [Muliu Task 89 执行脚本] -> [Worker 回传原始终态]
  ▼
[阿里云 Gateway - 接收到 Worker 终态结果] ◄───【新增 AI 研读层】
  │
  ├─ 1. 判断是否触发 AI 研读：
  │    - 失败 / 结果未知 / 无输出：跳过，直接输出原始清晰错误；
  │    - 简单的单操作执行（如"关闭5000服"输出"正常关服"）：跳过，避免画蛇添足；
  │    - 满足条件（包含目标检测疑问、多服批量查询、长列表数据、日志诊断等）：触发研读！
  │
  ├─ 2. 构造研读上下文，调用阿里云本地 ai_client.generate()：
  │    - 用户原始提问（job.text）
  │    - 业务配置知识（server_environments.json 的区域与环境划分）
  │    - 脚本执行输出内容（已剔除 Task 89 脚手架的纯净业务日志）
  │
  ├─ 3. 安全与降级保障：
  │    - 提示词设定强约束：严禁捏造任何数据，必须 100% 忠实于执行输出，严禁带出内部 IP；
  │    - 设置 8 秒硬超时；若大模型调用超时或报错，自动静默降级为原有格式化文本 format_execution_result()；
  │
  ▼
[飞书结果卡片更新与详细消息投递]
  - AI 提炼结论置顶展示；
  - 原始详细数据附在下方，兼顾直接结论与审计证据。
```

---

## 3. 详细设计规范

### 3.1 触发判定规则
在 `src/message_worker.py` 中增加判定函数：
```python
def should_interpret_with_ai(job_text: str, result: MuliuExecutionResult) -> bool:
    if not result.succeeded or not result.step_results:
        return False
    # 只要满足以下任一条件即可触发：
    # 1. 用户问题具有明确的目标核实或比较倾向
    intent_keywords = ("是否存在", "有没有", "是否有", "是否包含", "对比", "分析", "有哪些", "哪个是", "什么版本")
    if any(kw in job_text for kw in intent_keywords):
        return True
    # 2. 调用的脚本本身属于数据汇总类（如批量查询、Patch列表检查）
    paths = {step.path for step in result.step_results}
    if any(p.endswith(("batch_server_query.py", "cc_patch.py", "server_log_query.py")) for p in paths):
        return True
    return False
```

### 3.2 研读提示词（Prompt Template）
```text
你是飞书测试服运维 AI 助手。用户发起了一个运维请求，后端脚本已执行完成并返回了真实数据。
请根据【用户原始提问】和【实际执行结果】，结合【服务器环境资产】，为用户直接生成清晰、专业的分析结论。

【用户原始提问】：
{user_text}

【环境资产参考（区域与环境归属）】：
{environment_context}

【实际执行结果数据】：
{execution_data}

【输出约束】：
1. 针对用户问题的第一句话必须直接给出核心结论（例如：“【存在】已在列表中找到…”、“【不存在】未找到该补丁”、“【版本完全一致】…”）；
2. 提取关键事实并结构化呈现（例如条目序号、补丁类型、文件状态，或按港澳台/韩服等地区分组）；
3. 严禁捏造任何服务器、版本或状态，必须 100% 忠实于执行结果数据；
4. 严禁包含任何内部 IPv4 地址（如 10.x.x.x、192.168.x.x）；
5. 保持精简得体，使用适合飞书阅读的 Markdown 排版。
```

### 3.3 修改点与文件清单
1. **`src/app.py`**：
   在 `/internal/worker/jobs/{message_id}/result` 路由中，收到结果后先调用 `await job_processor.interpret_execution_result(pending_job, result, reply)`，再将最终回复投递给飞书。
2. **`src/message_worker.py`**：
   - 实现 `interpret_execution_result` 方法；
   - 内部包含 `should_interpret_with_ai` 判定、超时控制（asyncio.wait_for）与异常静默降级逻辑。
3. **`config/muliu_script_catalog.md`**：
   在模式 H（Patch 管理）中，追加“检查某补丁是否存在”、“核实 5010 是否包含某个 patch”等自然语言示例，确保 Planner 能够直接识别。
4. **单元测试（`tests/test_operation_flow.py` 或新建测试文件）**：
   覆盖正常 AI 提炼展示、AI 超时降级回退、失败任务不触发提炼等关键用例。
