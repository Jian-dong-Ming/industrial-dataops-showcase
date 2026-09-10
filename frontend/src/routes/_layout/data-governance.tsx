import { createFileRoute } from "@tanstack/react-router"

import DataGovernanceManager from "@/components/DataGovernance/DataGovernanceManager"

export const Route = createFileRoute("/_layout/data-governance")({
  component: DataGovernance,
  head: () => ({
    meta: [{ title: "数据治理 - 工业数据运维平台" }],
  }),
})

function DataGovernance() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          工业数据导入与治理
        </h1>
        <p className="mt-1 text-muted-foreground">
          对 CSV/XLSX 历史数据进行字段映射、质量校验、去重和可追溯入库。
        </p>
      </div>
      <DataGovernanceManager />
    </div>
  )
}
