# 本地开发

首次运行先完成 README 中的配置、迁移与模拟资产初始化。以下开发方式与全容器运行二选一，避免重复占用端口。

## 后端与前端

在仓库根目录安装依赖、启动数据库与测试邮件服务：

```bash
uv sync --frozen --all-groups
bun install --frozen-lockfile
docker compose up -d --wait db mailcatcher
```

在 `backend/` 中启动后端：

```bash
uv run bash scripts/prestart.sh
uv run fastapi dev --port 8200
```

在另一个终端、仓库根目录启动前端：

```bash
bun run dev
```

页面位于 http://localhost:5173，接口位于 http://localhost:8200。`frontend/.env` 仅包含公开的本地地址，不包含服务密钥。生产打包使用同源 API；不要把本地开发地址带入部署产物。

采集和导入是独立进程。从 `backend/` 分别运行 `uv run python -m app.data_import.jobs` 和 `uv run python -m app.opcua.collector`。本地采集任务的端点必须是宿主机可达地址；容器服务名只在 Docker 网络中解析。不要同时启动两份采集监督器。

## 数据与配置

- `.env`、`.env.ai` 是本地私有配置，已被 Git 和 Docker 构建上下文排除。
- Docker 数据库存放于项目命名卷。默认按 Compose 项目名隔离；只有明确需要接入既有数据库时才设置 `DB_VOLUME_NAME`。
- `IMPORT_STORAGE_HOST_DIR` 是 Docker 挂载的宿主机目录；本地 Python 使用 `IMPORT_STORAGE_DIR`。
- 修改环境配置后需重新创建相关容器；单纯重启不会重新注入环境变量。
- 数据库迁移前先备份。不要用删除数据卷代替故障排查。

## 修改与验证

```bash
uv run prek run --all-files
bash scripts/test-local.sh
```

后端测试入口只重建 `_test` 结尾的专用数据库，不修改业务库。报告默认保存到 `.reports/coverage`；可通过 `COVERAGE_HTML_DIR` 改到独立数据盘。不要并行运行共用同一测试库的两套测试。

API 结构变化后，运行 `bash scripts/generate-client.sh`，检查生成的 TypeScript 客户端变更。前端构建使用 `bun run build`，产物写入 `backend/app/frontend`。

浏览器测试会修改账户、资产和导入记录，建议通过独立环境或 GitHub Actions 运行整套测试。真实模型测试默认跳过；显式启用前先确认外发资料和费用。
