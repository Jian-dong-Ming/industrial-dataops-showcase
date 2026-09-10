import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import {
  Activity,
  Cpu,
  Database,
  Factory,
  FileCheck2,
  RadioTower,
  ShieldCheck,
  Tags,
  Waypoints,
} from "lucide-react"

import { DashboardService } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import useAuth from "@/hooks/useAuth"

export const Route = createFileRoute("/_layout/")({
  component: Dashboard,
  head: () => ({
    meta: [{ title: "工作台 - 工业数据运维平台" }],
  }),
})

const capabilities = [
  {
    title: "工业资产管理",
    description: "工厂、产线、设备与测点的分层管理",
    status: "已可用",
    icon: Factory,
  },
  {
    title: "用户与权限",
    description: "管理员、工程师、观察者及工厂级授权",
    status: "已可用",
    icon: ShieldCheck,
  },
  {
    title: "数据导入与治理",
    description: "CSV/XLSX 映射、校验和质量报告",
    status: "已可用",
    icon: Database,
  },
  {
    title: "实时数据接入",
    description: "OPC UA 模拟、订阅、断线重连与趋势展示",
    status: "模拟环境",
    icon: RadioTower,
  },
]

function formatTime(value: string | null | undefined): string {
  if (!value) return "暂无样本"
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value))
}

function formatCount(value: number | undefined): string {
  return value === undefined ? "—" : value.toLocaleString("zh-CN")
}

function Dashboard() {
  const { user: currentUser } = useAuth()
  const summaryQuery = useQuery({
    queryKey: ["dashboard-summary"],
    queryFn: async () => (await DashboardService.readSummary()).data,
    refetchInterval: 30_000,
  })
  const summary = summaryQuery.data

  const assetStats = [
    { label: "工厂", value: summary?.plant_count, icon: Factory },
    {
      label: "产线",
      value: summary?.production_line_count,
      icon: Waypoints,
    },
    { label: "设备", value: summary?.device_count, icon: Cpu },
    { label: "测点", value: summary?.tag_count, icon: Tags },
  ]

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">
          你好，{currentUser?.full_name || currentUser?.email}
        </h1>
        <p className="mt-1 text-muted-foreground">
          当前数据按你的工厂权限统计，每 30 秒自动刷新。
        </p>
      </div>

      <section className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold">运行概览</h2>
            <p className="text-sm text-muted-foreground">
              资产、采集与数据治理链路的实时摘要
            </p>
          </div>
          {summary?.generated_at && (
            <span className="text-xs text-muted-foreground">
              更新于 {formatTime(summary.generated_at)}
            </span>
          )}
        </div>

        {summaryQuery.isError && (
          <Alert variant="destructive">
            <Activity />
            <AlertTitle>运行数据暂时不可用</AlertTitle>
            <AlertDescription>
              后端或数据库可能未就绪，请检查服务健康状态后重试。
            </AlertDescription>
          </Alert>
        )}

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {assetStats.map(({ label, value, icon: Icon }) => (
            <Card key={label}>
              <CardContent className="flex items-center justify-between p-5">
                <div>
                  <p className="text-sm text-muted-foreground">{label}</p>
                  {summaryQuery.isLoading ? (
                    <Skeleton className="mt-2 h-8 w-16" />
                  ) : (
                    <p className="mt-1 text-2xl font-semibold tabular-nums">
                      {formatCount(value)}
                    </p>
                  )}
                </div>
                <span className="rounded-xl bg-primary/10 p-3 text-primary">
                  <Icon className="size-5" />
                </span>
              </CardContent>
            </Card>
          ))}
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <RadioTower className="size-4 text-primary" />
                OPC UA 采集
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="flex justify-between">
                <span className="text-muted-foreground">采集任务</span>
                <span>{formatCount(summary?.acquisition_task_count)}</span>
              </p>
              <p className="flex justify-between">
                <span className="text-muted-foreground">运行 / 已连接</span>
                <span>
                  {formatCount(summary?.running_task_count)} /{" "}
                  {formatCount(summary?.connected_task_count)}
                </span>
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="size-4 text-primary" />
                时序样本
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="flex justify-between">
                <span className="text-muted-foreground">累计样本</span>
                <span>{formatCount(summary?.sample_count)}</span>
              </p>
              <p className="flex justify-between gap-4">
                <span className="text-muted-foreground">最新数据</span>
                <span>{formatTime(summary?.latest_sample_at)}</span>
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <FileCheck2 className="size-4 text-primary" />
                历史数据治理
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="flex justify-between">
                <span className="text-muted-foreground">导入批次 / 已完成</span>
                <span>
                  {formatCount(summary?.import_batch_count)} /{" "}
                  {formatCount(summary?.completed_import_batch_count)}
                </span>
              </p>
              <p className="flex justify-between">
                <span className="text-muted-foreground">已记录质量问题</span>
                <span>{formatCount(summary?.quality_issue_count)}</span>
              </p>
            </CardContent>
          </Card>
        </div>
      </section>

      <section className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold">已实现能力</h2>
          <p className="text-sm text-muted-foreground">
            当前版本边界清晰，不包含真实PLC写控制、质量预测或Agent。
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {capabilities.map(({ title, description, status, icon: Icon }) => (
            <Card key={title} className="bg-card/70">
              <CardHeader className="flex flex-row items-center justify-between gap-3 pb-3">
                <CardTitle className="text-base">{title}</CardTitle>
                <Icon className="size-5 text-primary" />
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="min-h-10 text-sm text-muted-foreground">
                  {description}
                </p>
                <Badge variant="secondary">{status}</Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>
    </div>
  )
}
