import { Database, RadioTower, ShieldCheck } from "lucide-react"

import { Appearance } from "@/components/Common/Appearance"
import { Logo } from "@/components/Common/Logo"
import { Footer } from "./Footer"

interface AuthLayoutProps {
  children: React.ReactNode
}

export function AuthLayout({ children }: AuthLayoutProps) {
  return (
    <div className="grid min-h-svh lg:grid-cols-2">
      <div className="relative hidden overflow-hidden bg-muted px-12 dark:bg-zinc-900 lg:flex lg:items-center lg:justify-center">
        <div className="absolute -left-24 -top-24 size-80 rounded-full bg-primary/10 blur-3xl" />
        <div className="relative w-full max-w-lg space-y-10">
          <Logo variant="full" className="h-16" asLink={false} />
          <div className="space-y-3">
            <p className="text-3xl font-semibold leading-tight tracking-tight">
              连接工业现场数据，形成可追溯的数据治理闭环
            </p>
            <p className="max-w-md text-sm leading-6 text-muted-foreground">
              面向本地模拟环境，统一管理工业资产、历史文件与 OPC UA 时序数据。
            </p>
          </div>
          <div className="grid gap-3">
            <div className="flex items-center gap-3 rounded-xl border bg-background/70 p-4 shadow-sm">
              <ShieldCheck className="size-5 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-medium">分级资产与权限</p>
                <p className="text-xs text-muted-foreground">
                  工厂—产线—设备—测点与对象级授权
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-xl border bg-background/70 p-4 shadow-sm">
              <Database className="size-5 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-medium">历史数据治理</p>
                <p className="text-xs text-muted-foreground">
                  CSV/XLSX映射、质量校验、去重与批次追溯
                </p>
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-xl border bg-background/70 p-4 shadow-sm">
              <RadioTower className="size-5 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-medium">OPC UA模拟采集</p>
                <p className="text-xs text-muted-foreground">
                  订阅入库、断线恢复、最新值与趋势展示
                </p>
              </div>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            演示边界：合成数据与模拟设备，不连接真实产线，不执行PLC写控制。
          </p>
        </div>
      </div>
      <div className="flex flex-col gap-4 p-6 md:p-10">
        <div className="flex justify-end">
          <Appearance />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="w-full max-w-xs">{children}</div>
        </div>
        <Footer />
      </div>
    </div>
  )
}
