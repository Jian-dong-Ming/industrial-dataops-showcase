# 前端目录

React、TypeScript、Vite、TanStack Router/Query、Tailwind CSS 与 shadcn/ui。

在根目录执行 `bun install --frozen-lockfile`、`bun run dev`，页面默认位于 http://localhost:5173。后端开发端口为 8200，完整配置见 [开发说明](../development.md)。

- `src/routes`：页面与路由。
- `src/components`：领域表单、列表、通用组件。
- `src/client`：由 OpenAPI 生成的接口客户端，不手工更改生成结果。
- `tests`：登录、资产、治理、采集与助手的 Playwright 验证。

在本目录执行 `bun run build`，页面产物写入 `backend/app/frontend` 并由 FastAPI 提供。部署使用同源接口地址。修改接口后从根目录运行 `bash scripts/generate-client.sh`。

整套浏览器测试会修改数据，只在隔离环境运行；真实模型用例默认跳过。不要提交 `playwright/.auth`、测试报告或浏览器 trace，它们可能包含登录状态及用户数据。
