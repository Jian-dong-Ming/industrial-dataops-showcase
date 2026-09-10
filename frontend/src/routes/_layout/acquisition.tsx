import { createFileRoute } from "@tanstack/react-router"

import AcquisitionManager from "@/components/Acquisition/AcquisitionManager"

export const Route = createFileRoute("/_layout/acquisition")({
  component: Acquisition,
  head: () => ({
    meta: [{ title: "实时采集 - 工业数据运维平台" }],
  }),
})

function Acquisition() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">OPC UA 实时采集</h1>
        <p className="mt-1 text-muted-foreground">
          配置测点订阅、观察连接状态和数据质量，并查看实时趋势。
        </p>
      </div>
      <AcquisitionManager />
    </div>
  )
}
