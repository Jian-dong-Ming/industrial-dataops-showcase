import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Download, Pause, Play, RotateCcw } from "lucide-react"
import { useEffect, useState } from "react"

import { DemoService, type TeachingScenario } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

export const Route = createFileRoute("/_layout/demo")({
  component: Demo,
  head: () => ({ meta: [{ title: "场景回放 - 工业数据运维平台" }] }),
})

function SignalChart({
  scenario,
  code,
  cursor,
}: {
  scenario: TeachingScenario
  code: string
  cursor: number
}) {
  const point = scenario.points.find((item) => item.code === code)!
  const values = scenario.frames.map((frame) => frame.values[code])
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const padding = Math.max((maximum - minimum) * 0.12, 0.01)
  const low = minimum - padding
  const high = maximum + padding
  const x = (seconds: number) => 48 + (seconds / 180) * 392
  const y = (value: number) => 136 - ((value - low) / (high - low)) * 112
  const segments: string[] = []
  let segment = ""
  for (const frame of scenario.frames) {
    if (frame.bad_quality_codes.includes(code)) {
      if (segment) segments.push(segment)
      segment = ""
    } else {
      segment += `${x(frame.elapsed_seconds)},${y(frame.values[code])} `
    }
  }
  if (segment) segments.push(segment)
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm">
          {point.name}{" "}
          <span className="font-normal text-muted-foreground">
            {point.unit}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <svg
          viewBox="0 0 460 172"
          role="img"
          aria-label={`${point.name}离线合成趋势`}
          className="w-full text-primary"
        >
          <title>{point.name}：横轴为回放秒数，坏质量数据断开绘制</title>
          <rect
            x={x(60)}
            y="15"
            width={x(120) - x(60)}
            height="121"
            fill="currentColor"
            opacity="0.07"
          />
          {[low, (low + high) / 2, high].map((value) => (
            <g key={value}>
              <line
                x1="48"
                x2="440"
                y1={y(value)}
                y2={y(value)}
                stroke="currentColor"
                opacity="0.15"
              />
              <text
                x="42"
                y={y(value) + 3}
                textAnchor="end"
                fontSize="10"
                className="fill-muted-foreground"
              >
                {value.toFixed(1)}
              </text>
            </g>
          ))}
          {[0, 60, 120, 180].map((seconds) => (
            <text
              key={seconds}
              x={x(seconds)}
              y="157"
              textAnchor="middle"
              fontSize="10"
              className="fill-muted-foreground"
            >
              {seconds}s
            </text>
          ))}
          {segments.map((points) => (
            <polyline
              key={points.slice(0, 35)}
              points={points}
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            />
          ))}
          <line
            x1={x(scenario.frames[cursor].elapsed_seconds)}
            x2={x(scenario.frames[cursor].elapsed_seconds)}
            y1="15"
            y2="136"
            stroke="currentColor"
            strokeDasharray="4 3"
          />
          {scenario.frames
            .filter((frame) => frame.bad_quality_codes.includes(code))
            .map((frame) => (
              <circle
                key={frame.elapsed_seconds}
                cx={x(frame.elapsed_seconds)}
                cy={y(frame.values[code])}
                r="2.5"
                className="fill-destructive"
              />
            ))}
        </svg>
        <p className="text-xs text-muted-foreground">
          阴影：脚本扰动区间；红点：坏质量（不连入有效趋势）。各图纵轴独立。
        </p>
      </CardContent>
    </Card>
  )
}

function Demo() {
  const query = useQuery({
    queryKey: ["teaching-scenario"],
    queryFn: async () => (await DemoService.readScenario()).data,
    staleTime: Infinity,
  })
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [line, setLine] = useState(1)
  const [downloadError, setDownloadError] = useState(false)
  const scenario = query.data
  useEffect(() => {
    if (!playing || !scenario) return
    const timer = window.setInterval(
      () =>
        setCursor((value) => Math.min(value + 1, scenario.frames.length - 1)),
      250,
    )
    return () => window.clearInterval(timer)
  }, [playing, scenario])
  useEffect(() => {
    if (scenario && cursor === scenario.frames.length - 1) setPlaying(false)
  }, [cursor, scenario])
  if (query.isPending) return <p role="status">正在读取离线教学场景…</p>
  if (query.isError || !scenario)
    return (
      <Alert variant="destructive">
        <AlertTitle>场景暂不可用</AlertTitle>
        <AlertDescription>
          请检查服务连接后重试。
          <Button variant="outline" onClick={() => query.refetch()}>
            重新加载
          </Button>
        </AlertDescription>
      </Alert>
    )
  const frame = scenario.frames[cursor]
  const points = scenario.points.filter((point) => point.line === line)
  const prefix = line === 1 ? "" : "L2_"
  const seek = (seconds: number) => {
    setPlaying(false)
    setCursor(seconds / 2)
  }
  const download = async () => {
    setDownloadError(false)
    try {
      const response = await DemoService.downloadScenario({
        responseType: "blob",
      })
      const url = URL.createObjectURL(
        new Blob([response.data as BlobPart], {
          type: "text/csv;charset=utf-8",
        }),
      )
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = "synthetic-cooling-loop-v1.csv"
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      setDownloadError(true)
    }
  }
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">场景回放</h1>
          <p className="mt-1 text-muted-foreground">{scenario.title}</p>
        </div>
        <Badge variant="secondary">离线合成 · {scenario.version}</Badge>
      </div>
      <Alert>
        <AlertTitle>回放不是当前生产状态</AlertTitle>
        <AlertDescription>
          {scenario.source}。{scenario.warning}
        </AlertDescription>
      </Alert>
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">01 · 正常基线 / 0–60秒</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>两线均围绕各自基线波动；先确认单位、时间与采集质量。</p>
            <Button variant="outline" onClick={() => seek(30)}>
              查看正常片段
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              02 · 注入扰动 / 60–120秒
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>
              一号线流量下降、压力与出口温度上升；90–100秒流量另有坏质量。二号线无此注入。
            </p>
            <Button variant="outline" onClick={() => seek(90)}>
              查看异常片段
            </Button>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              03 · 恢复观察 / 120–180秒
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p>
              脚本逐步撤销扰动，160秒后回到基线波动；恢复趋势不等于设备经人工验收。
            </p>
            <Button variant="outline" onClick={() => seek(170)}>
              查看恢复片段
            </Button>
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardContent className="space-y-4 pt-5">
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={() => {
                if (cursor === scenario.frames.length - 1) setCursor(0)
                setPlaying(!playing)
              }}
            >
              {playing ? <Pause /> : <Play />}
              {playing ? "暂停回放" : "8倍速回放"}
            </Button>
            <Button
              variant="outline"
              aria-label="回到起点"
              onClick={() => seek(0)}
            >
              <RotateCcw />
            </Button>
            <strong aria-live="off">
              {frame.elapsed_seconds} / 180 秒 · {frame.phase}
            </strong>
            <span className="text-sm text-muted-foreground">
              固定样本时间：
              {new Date(frame.timestamp).toLocaleString("zh-CN", {
                timeZone: "Asia/Shanghai",
                hour12: false,
              })}
              （北京时间）
            </span>
          </div>
          <label className="block text-sm" htmlFor="replay-position">
            回放位置
          </label>
          <input
            id="replay-position"
            className="w-full accent-primary"
            type="range"
            min="0"
            max="90"
            value={cursor}
            onChange={(event) => {
              setPlaying(false)
              setCursor(Number(event.target.value))
            }}
          />
          <fieldset className="flex gap-2">
            <legend className="sr-only">回放产线</legend>
            {[1, 2].map((value) => (
              <Button
                key={value}
                variant={line === value ? "default" : "outline"}
                aria-pressed={line === value}
                onClick={() => setLine(value)}
              >
                {value}号线{value === 2 ? "（对照）" : "（注入侧）"}
              </Button>
            ))}
          </fieldset>
        </CardContent>
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {[
          "PUMP_FLOW",
          "PUMP_PRESSURE",
          "PUMP_OUTLET_TEMP",
          "PUMP_INLET_TEMP",
        ].map((code) => (
          <SignalChart
            key={prefix + code}
            scenario={scenario}
            code={prefix + code}
            cursor={cursor}
          />
        ))}
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {line}号线测点字典与回放值 · {points.length}个通道
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table aria-label="回放测点字典">
              <TableHeader>
                <TableRow>
                  <TableHead>设备 / 测点</TableHead>
                  <TableHead>编码</TableHead>
                  <TableHead>回放值</TableHead>
                  <TableHead>采集质量</TableHead>
                  <TableHead>演示用途与边界</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {points.map((point) => (
                  <TableRow key={point.code}>
                    <TableCell>
                      <div>{point.name}</div>
                      <div className="text-xs text-muted-foreground">
                        {point.device}
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      {point.code}
                    </TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {frame.values[point.code].toFixed(3)} {point.unit}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          frame.bad_quality_codes.includes(point.code)
                            ? "destructive"
                            : "secondary"
                        }
                      >
                        {frame.bad_quality_codes.includes(point.code)
                          ? "坏质量，不作有效值"
                          : "质量良好"}
                      </Badge>
                    </TableCell>
                    <TableCell className="min-w-64 whitespace-normal text-xs text-muted-foreground">
                      {point.purpose}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">从回放到项目操作</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p>
            24个测点 × 91个时刻 = 2,184行窄表CSV，固定种子 {scenario.seed}
            ，包含5条坏质量记录。下载不会写库或启动采集。导入时选择对应教学工厂并核对测点编码；质量码必须映射，才能识别坏质量。不要将两条产线的同名测点混在一起。
          </p>
          <div className="flex flex-wrap gap-3">
            <Button variant="outline" onClick={download}>
              <Download />
              下载合成CSV
            </Button>
            <Button variant="outline" asChild>
              <Link to="/data-governance">前往数据治理</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to="/acquisition">查看实际采集状态</Link>
            </Button>
            <Button variant="outline" asChild>
              <Link to="/assistant">查询业务说明</Link>
            </Button>
          </div>
          {downloadError && (
            <p role="alert" className="text-destructive">
              下载失败，请重试或检查登录状态。
            </p>
          )}
          <details>
            <summary className="cursor-pointer text-muted-foreground">
              复现校验与限制
            </summary>
            <p className="mt-2 break-all font-mono text-xs">
              CSV SHA-256：{scenario.csv_sha256}
            </p>
            <p className="mt-2">
              本回放仅展示脚本设定的联动现象，不提供堵塞诊断、预测模型或自动控制。资产管理和实时采集页仍以数据库中的实际配置为准。
            </p>
          </details>
        </CardContent>
      </Card>
    </div>
  )
}
