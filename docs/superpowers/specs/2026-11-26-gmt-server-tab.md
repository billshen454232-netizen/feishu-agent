# 飞书修改 GMT 选服页签

运维群消息「把 5202 服的 GMT 选服页签改成赛季 10、剧本 16」生成单服预检计划。Gateway 按资产表核对服号归属，再把预检送入现有 Worker 队列。Worker 通过 GMT 单服查询读取记录。确认卡展示原值和目标值，原请求人确认后才能执行。

Worker 在提交前按记录指纹复核服号、ServerId、服名及原值。它使用 GMT 批量修改接口的单目标格式，提交 `season` 和 `scenario`，再核对成功回执和回读结果。写入响应或结果不明时保留未知终态，核对记录后再处理。

Worker 在本机 `config/config.json` 的 `gmt.origins.cn` 和 `gmt.origins.overseas` 配置对应的测试 GMT HTTPS 站点。国服和海外各自复用对应运维账号及 GMT 会话。旧的 `gmt.origin` 仍可作为海外地址。预检和确认执行进入同一个运维队列。Gateway 和飞书只接收服号、页签字段和结果。

国服 GS-1 运行版本包含相同的单服查询、批量修改入口和 `ServerNameList` 字段处理。验收覆盖受控合同、两侧资产路由与账号选择、原值变化、业务拒绝与写入后回读。线上验收以已部署 Gateway 和 Worker 上的飞书确认卡及 GMT 回读为准。
