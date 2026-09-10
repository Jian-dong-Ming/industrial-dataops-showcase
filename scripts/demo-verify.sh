#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_URL="${1:-http://127.0.0.1:8200}"

cd "${PROJECT_ROOT}"

echo "[1/4] 校验 Docker Compose 配置"
docker compose config --quiet

echo "[2/4] 检查核心容器"
required_services=(db backend opcua-simulator opcua-collector import-worker)
for service in "${required_services[@]}"; do
  container_id="$(docker compose ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "失败：${service} 容器不存在，请先启动项目。" >&2
    exit 1
  fi
  state="$(docker inspect --format '{{.State.Status}}' "${container_id}")"
  if [[ "${state}" != "running" ]]; then
    echo "失败：${service} 当前状态为 ${state}。" >&2
    exit 1
  fi
  echo "  - ${service}: running"
done

echo "[3/4] 检查后端、数据库就绪状态和接口文档"
health_body="$(curl --fail --show-error --silent "${BASE_URL}/api/v1/utils/health-check/")"
if [[ "${health_body}" != "true" ]]; then
  echo "失败：健康检查返回 ${health_body}，预期为 true。" >&2
  exit 1
fi
curl --fail --show-error --silent "${BASE_URL}/docs" > /dev/null
curl --fail --show-error --silent "${BASE_URL}/api/v1/openapi.json" > /dev/null

echo "[4/4] 检查前端入口"
curl --fail --show-error --silent "${BASE_URL}/" > /dev/null

echo
echo "演示前检查通过："
echo "  平台入口  ${BASE_URL}/"
echo "  API文档   ${BASE_URL}/docs"
echo "  健康检查  ${BASE_URL}/api/v1/utils/health-check/"
