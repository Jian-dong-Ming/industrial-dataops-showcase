# 后端目录

开发、配置与测试入口见 [开发说明](../development.md)。

| 目录 | 内容 |
| --- | --- |
| `app/api/routes` | REST 接口与输入输出处理 |
| `app/api/permissions.py` | 工厂对象级授权 |
| `app/models.py` | 资产、样本、任务与知识等数据模型 |
| `app/alembic/versions` | 数据库结构迁移 |
| `app/data_import` | 文件解析、映射、质量校验与后台导入 |
| `app/opcua` | 模拟器、节点浏览、采集监督器与批量写入 |
| `app/assistant` | 模型适配、检索、工具、响应检查与评测 |
| `tests` | 隔离数据库上的回归测试 |
| `scripts` | 初始化、测试、性能与显式模型评测入口 |

业务逻辑使用 PostgreSQL 事务与唯一键约束；不要用 SQLite 替代其并发、锁和冲突语义验证。修改接口结构后运行根目录 `scripts/generate-client.sh` 更新前端客户端。
