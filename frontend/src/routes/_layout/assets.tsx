import { createFileRoute } from "@tanstack/react-router"

import AssetManager from "@/components/Assets/AssetManager"

export const Route = createFileRoute("/_layout/assets")({
  component: Assets,
  head: () => ({
    meta: [{ title: "资产管理 - 工业数据运维平台" }],
  }),
})

function Assets() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">工业资产管理</h1>
        <p className="text-muted-foreground">
          管理工厂、产线、设备和测点；资产可禁用或重新启用，历史引用始终保留。
        </p>
      </div>
      <AssetManager />
    </div>
  )
}
