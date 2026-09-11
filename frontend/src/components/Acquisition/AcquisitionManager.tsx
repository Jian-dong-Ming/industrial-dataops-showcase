import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Activity,
  Cable,
  CircleAlert,
  Gauge,
  Pencil,
  Play,
  Plus,
  RadioTower,
  RefreshCw,
  Search,
  Square,
} from "lucide-react"
import { type FormEvent, useEffect, useMemo, useState } from "react"

import {
  AcquisitionService,
  type AcquisitionTaskPublic,
  DevicesService,
  type LatestTagValuePublic,
  type OpcUaBrowseNodePublic,
  type PlantPublic,
  PlantsService,
  ProductionLinesService,
  type TagPublic,
  type TagSamplePublic,
  TagsService,
} from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { formatAge, freshnessThresholdMs, timePosition } from "@/lib/telemetry"
import { handleError } from "@/utils"

const SIMULATOR_ENDPOINT =
  "opc.tcp://opcua-simulator:4840/industrial-dataops/server/"

type AssetData = {
  plants: PlantPublic[]
  tagsByPlant: Map<string, TagPublic[]>
}

type NodeMapping = {
  nodeId: string
  tagId: string
  label: string
}

type TaskForm = {
  name: string
  plantId: string
  endpointUrl: string
  publishingIntervalMs: string
  batchSize: string
  reconnectDelaySeconds: string
  maxReconnectDelaySeconds: string
  mappings: NodeMapping[]
}

const emptyTaskForm: TaskForm = {
  name: "OPC UA 模拟采集任务",
  plantId: "",
  endpointUrl: SIMULATOR_ENDPOINT,
  publishingIntervalMs: "1000",
  batchSize: "50",
  reconnectDelaySeconds: "1",
  maxReconnectDelaySeconds: "30",
  mappings: [],
}

const statusText: Record<string, string> = {
  stopped: "已停止",
  connecting: "连接中",
  connected: "已连接",
  reconnecting: "重连中",
  error: "异常",
}

const statusClass: Record<string, string> = {
  stopped:
    "border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300",
  connecting: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-300",
  connected:
    "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  reconnecting:
    "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
  error: "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—"
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value))
}

function formatValue(sample: LatestTagValuePublic): string {
  if (sample.numeric_value !== null && sample.numeric_value !== undefined) {
    return new Intl.NumberFormat("zh-CN", {
      maximumFractionDigits: 3,
    }).format(sample.numeric_value)
  }
  if (sample.value === null || sample.value === undefined) return "—"
  if (typeof sample.value === "string") return sample.value
  if (typeof sample.value === "boolean") return sample.value ? "是" : "否"
  return JSON.stringify(sample.value)
}

function sampleState(
  sample: LatestTagValuePublic,
  generatedAt: string | undefined,
): { label: string; className: string; ageText: string } {
  if (!sample.source_timestamp) {
    return {
      label: "暂无数据",
      className:
        "border-slate-500/30 bg-slate-500/10 text-slate-700 dark:text-slate-300",
      ageText: "—",
    }
  }
  const referenceTime = generatedAt
    ? new Date(generatedAt).getTime()
    : Date.now()
  const ageMs = referenceTime - new Date(sample.source_timestamp).getTime()
  const ageText = formatAge(ageMs)
  if (!Number.isFinite(ageMs) || ageMs < -5000) {
    return {
      label: "时间异常",
      className: "border-amber-500/30 text-amber-700 dark:text-amber-300",
      ageText,
    }
  }
  if (sample.is_good === false) {
    return {
      label: "质量异常",
      className:
        "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
      ageText,
    }
  }
  const staleAfterMs = freshnessThresholdMs(sample.sampling_interval_ms)
  if (ageMs > staleAfterMs) {
    return {
      label: "数据滞后",
      className:
        "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
      ageText,
    }
  }
  return {
    label: "质量良好",
    className:
      "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    ageText,
  }
}

function TaskDialog({
  task,
  assetData,
  canWrite,
}: {
  task?: AcquisitionTaskPublic
  assetData: AssetData
  canWrite: boolean
}) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<TaskForm>(emptyTaskForm)
  const [browseNodes, setBrowseNodes] = useState<OpcUaBrowseNodePublic[]>([])
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  useEffect(() => {
    if (!open) return
    setForm(
      task
        ? {
            name: task.name,
            plantId: task.plant_id,
            endpointUrl: task.endpoint_url,
            publishingIntervalMs: String(task.publishing_interval_ms ?? 1000),
            batchSize: String(task.batch_size ?? 50),
            reconnectDelaySeconds: String(task.reconnect_delay_seconds ?? 1),
            maxReconnectDelaySeconds: String(
              task.max_reconnect_delay_seconds ?? 30,
            ),
            mappings: (task.nodes ?? []).map((node) => ({
              nodeId: node.node_id,
              tagId: node.tag_id,
              label: node.tag_name,
            })),
          }
        : emptyTaskForm,
    )
    setBrowseNodes([])
  }, [open, task])

  const tags = assetData.tagsByPlant.get(form.plantId) ?? []
  const update = <K extends keyof TaskForm>(key: K, value: TaskForm[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const browseMutation = useMutation({
    mutationFn: async () =>
      (
        await AcquisitionService.browseEndpoint({
          body: {
            plant_id: form.plantId,
            endpoint_url: form.endpointUrl,
            root_node_id: "i=85",
            max_depth: 5,
            max_nodes: 500,
          },
        })
      ).data,
    onSuccess: (result) => {
      setBrowseNodes(result.nodes)
      showSuccessToast(`发现 ${result.nodes.length} 个变量节点`)
    },
    onError: handleError.bind(showErrorToast),
  })

  const saveMutation = useMutation({
    mutationFn: async () => {
      const body = {
        name: form.name.trim(),
        endpoint_url: form.endpointUrl.trim(),
        publishing_interval_ms: Number(form.publishingIntervalMs),
        batch_size: Number(form.batchSize),
        reconnect_delay_seconds: Number(form.reconnectDelaySeconds),
        max_reconnect_delay_seconds: Number(form.maxReconnectDelaySeconds),
        nodes: form.mappings.map((mapping) => ({
          node_id: mapping.nodeId,
          tag_id: mapping.tagId,
          is_enabled: true,
        })),
      }
      if (task) {
        return AcquisitionService.updateTask({
          path: { task_id: task.id },
          body,
        })
      }
      return AcquisitionService.createTask({
        body: { ...body, plant_id: form.plantId },
      })
    },
    onSuccess: () => {
      showSuccessToast(task ? "采集任务已更新" : "采集任务已创建")
      setOpen(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["acquisition", "tasks"] }),
  })

  const addMapping = (node: OpcUaBrowseNodePublic) => {
    if (form.mappings.some((mapping) => mapping.nodeId === node.node_id)) return
    const usedTagIds = new Set(form.mappings.map((mapping) => mapping.tagId))
    const availableTag = tags.find((tag) => !usedTagIds.has(tag.id))
    update("mappings", [
      ...form.mappings,
      {
        nodeId: node.node_id,
        tagId: availableTag?.id ?? "",
        label: node.display_name,
      },
    ])
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!form.mappings.length || form.mappings.some((item) => !item.tagId)) {
      showErrorToast("至少添加一组完整的变量节点与平台测点映射")
      return
    }
    saveMutation.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {task ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={!canWrite || task.desired_state === "running"}
            aria-label={`编辑${task.name}`}
          >
            <Pencil className="size-4" />
          </Button>
        ) : (
          <Button disabled={!canWrite || assetData.plants.length === 0}>
            <Plus className="mr-2 size-4" />
            新建采集任务
          </Button>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{task ? "编辑采集任务" : "新建采集任务"}</DialogTitle>
          <DialogDescription>
            OPC UA 节点必须映射到阶段 1 已建立的测点，运行中的任务不能修改。
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-5" onSubmit={submit}>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="grid gap-2">
              <Label htmlFor="task-name">任务名称</Label>
              <Input
                id="task-name"
                value={form.name}
                onChange={(event) => update("name", event.target.value)}
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="task-plant">所属工厂</Label>
              <Select
                value={form.plantId || undefined}
                onValueChange={(value) => {
                  update("plantId", value)
                  update("mappings", [])
                }}
                disabled={Boolean(task)}
                required
              >
                <SelectTrigger id="task-plant" className="w-full bg-background">
                  <SelectValue placeholder="请选择工厂" />
                </SelectTrigger>
                <SelectContent>
                  {assetData.plants.map((plant) => (
                    <SelectItem key={plant.id} value={plant.id}>
                      {plant.code} · {plant.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="endpoint-url">OPC UA 端点</Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id="endpoint-url"
                value={form.endpointUrl}
                onChange={(event) => update("endpointUrl", event.target.value)}
                placeholder={SIMULATOR_ENDPOINT}
                required
              />
              <Button
                type="button"
                variant="outline"
                disabled={
                  !form.plantId || !form.endpointUrl || browseMutation.isPending
                }
                onClick={() => browseMutation.mutate()}
              >
                <RefreshCw
                  className={`mr-2 size-4 ${browseMutation.isPending ? "animate-spin" : ""}`}
                />
                浏览节点
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              Docker 内置模拟器使用默认地址；真实设备地址必须加入后端允许列表。
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <NumberField
              label="发布周期（毫秒）"
              value={form.publishingIntervalMs}
              min={100}
              onChange={(value) => update("publishingIntervalMs", value)}
            />
            <NumberField
              label="批量写入条数"
              value={form.batchSize}
              min={1}
              onChange={(value) => update("batchSize", value)}
            />
            <NumberField
              label="初始重连（秒）"
              value={form.reconnectDelaySeconds}
              min={1}
              onChange={(value) => update("reconnectDelaySeconds", value)}
            />
            <NumberField
              label="最大重连（秒）"
              value={form.maxReconnectDelaySeconds}
              min={1}
              onChange={(value) => update("maxReconnectDelaySeconds", value)}
            />
          </div>

          {browseNodes.length > 0 && (
            <div className="grid gap-2">
              <Label>已发现变量节点</Label>
              <div className="max-h-44 overflow-y-auto rounded-lg border">
                {browseNodes.map((node) => {
                  const added = form.mappings.some(
                    (mapping) => mapping.nodeId === node.node_id,
                  )
                  return (
                    <div
                      key={node.node_id}
                      className="flex items-center justify-between gap-3 border-b px-3 py-2 last:border-0"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium">
                          {node.display_name}
                        </p>
                        <p className="truncate font-mono text-xs text-muted-foreground">
                          {node.node_id} · {node.data_type ?? "未知类型"}
                        </p>
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={added}
                        onClick={() => addMapping(node)}
                      >
                        {added ? "已添加" : "添加"}
                      </Button>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div className="grid gap-2">
            <Label>节点与测点映射（{form.mappings.length}）</Label>
            {form.mappings.length === 0 ? (
              <div className="rounded-lg border border-dashed p-5 text-center text-sm text-muted-foreground">
                先选择工厂并浏览端点，然后添加需要采集的变量节点。
              </div>
            ) : (
              <div className="grid gap-2">
                {form.mappings.map((mapping, index) => (
                  <div
                    key={mapping.nodeId}
                    className="grid items-center gap-2 rounded-lg border p-3 md:grid-cols-[1fr_1fr_auto]"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {mapping.label}
                      </p>
                      <p className="truncate font-mono text-xs text-muted-foreground">
                        {mapping.nodeId}
                      </p>
                    </div>
                    <Select
                      value={mapping.tagId || undefined}
                      onValueChange={(value) =>
                        update(
                          "mappings",
                          form.mappings.map((item, itemIndex) =>
                            itemIndex === index
                              ? { ...item, tagId: value }
                              : item,
                          ),
                        )
                      }
                      required
                    >
                      <SelectTrigger className="w-full bg-background">
                        <SelectValue placeholder="映射到平台测点" />
                      </SelectTrigger>
                      <SelectContent>
                        {tags.map((tag) => (
                          <SelectItem
                            key={tag.id}
                            value={tag.id}
                            disabled={form.mappings.some(
                              (item, itemIndex) =>
                                itemIndex !== index && item.tagId === tag.id,
                            )}
                          >
                            {tag.code} · {tag.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        update(
                          "mappings",
                          form.mappings.filter(
                            (_, itemIndex) => itemIndex !== index,
                          ),
                        )
                      }
                    >
                      移除
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              取消
            </Button>
            <Button type="submit" disabled={saveMutation.isPending}>
              {saveMutation.isPending ? "保存中…" : "保存任务"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function NumberField({
  label,
  value,
  min,
  onChange,
}: {
  label: string
  value: string
  min: number
  onChange: (value: string) => void
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <Input
        type="number"
        min={min}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required
      />
    </div>
  )
}

function TrendChart({ samples }: { samples: TagSamplePublic[] }) {
  const numeric = samples
    .filter(
      (sample): sample is TagSamplePublic & { numeric_value: number } =>
        sample.numeric_value !== null &&
        Number.isFinite(sample.numeric_value) &&
        Number.isFinite(Date.parse(sample.source_timestamp)),
    )
    .sort(
      (a, b) => Date.parse(a.source_timestamp) - Date.parse(b.source_timestamp),
    )
  if (numeric.length < 2) {
    return (
      <div className="flex h-72 items-center justify-center rounded-xl border border-dashed text-sm text-muted-foreground">
        至少需要两个数值样本才能绘制趋势。
      </div>
    )
  }
  const values = numeric.map((sample) => sample.numeric_value)
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const width = 900
  const height = 280
  const padding = 32
  const firstTime = Date.parse(numeric[0].source_timestamp)
  const lastTime = Date.parse(numeric[numeric.length - 1].source_timestamp)
  const points = numeric
    .map((sample) => {
      const x =
        padding +
        timePosition(Date.parse(sample.source_timestamp), firstTime, lastTime) *
          (width - padding * 2)
      const y =
        height -
        padding -
        ((sample.numeric_value - min) / span) * (height - padding * 2)
      return `${x},${y}`
    })
    .join(" ")

  return (
    <div className="rounded-xl border bg-card p-3">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-72 w-full"
        role="img"
        aria-label="测点历史样本趋势图，横轴为采样时间"
      >
        <title>测点历史样本趋势图</title>
        <line
          x1={padding}
          y1={padding}
          x2={padding}
          y2={height - padding}
          className="stroke-border"
        />
        <line
          x1={padding}
          y1={height - padding}
          x2={width - padding}
          y2={height - padding}
          className="stroke-border"
        />
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinejoin="round"
          points={points}
          className="text-primary"
        />
        {numeric.map((sample, index) => {
          if (sample.is_good) return null
          const [x, y] = points.split(" ")[index].split(",")
          return (
            <circle
              key={sample.id}
              cx={x}
              cy={y}
              r="5"
              className="fill-red-500"
            />
          )
        })}
        <text
          x={padding + 4}
          y={padding - 8}
          className="fill-muted-foreground text-xs"
        >
          最大 {max.toFixed(2)}
        </text>
        <text
          x={padding + 4}
          y={height - padding - 8}
          className="fill-muted-foreground text-xs"
        >
          最小 {min.toFixed(2)}
        </text>
      </svg>
      <div className="flex justify-between gap-4 text-xs text-muted-foreground">
        <span>{formatTime(numeric[0].source_timestamp)}</span>
        <span>{formatTime(numeric[numeric.length - 1].source_timestamp)}</span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        横轴按实际采样时间显示；红点表示坏质量，不等同于工艺故障。仅展示当前返回的最近样本，不代表完整时间段。
      </p>
    </div>
  )
}

export default function AcquisitionManager() {
  const { user } = useAuth()
  const canWrite = Boolean(
    user?.is_superuser || user?.role === "admin" || user?.role === "engineer",
  )
  const [trendTagId, setTrendTagId] = useState("")
  const [trendTaskId, setTrendTaskId] = useState("")
  const [latestTaskId, setLatestTaskId] = useState("")
  const [latestSearch, setLatestSearch] = useState("")
  const [activeTab, setActiveTab] = useState("tasks")
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const tasksQuery = useQuery({
    queryKey: ["acquisition", "tasks"],
    queryFn: async () =>
      (await AcquisitionService.readTasks({ query: { limit: 100 } })).data,
    refetchInterval: 2000,
  })
  const assetsQuery = useQuery({
    queryKey: ["acquisition", "assets"],
    queryFn: async (): Promise<AssetData> => {
      const [plantsResult, linesResult, devicesResult, tagsResult] =
        await Promise.all([
          PlantsService.readPlants({ query: { limit: 100 } }),
          ProductionLinesService.linesReadProductionLines({
            query: { limit: 500 },
          }),
          DevicesService.readDevices({ query: { limit: 1000 } }),
          TagsService.readTags({ query: { limit: 2000 } }),
        ])
      const plants = plantsResult.data.data.filter(
        (plant) => plant.status === "active",
      )
      const linePlant = new Map(
        linesResult.data.data
          .filter((line) => line.status === "active")
          .map((line) => [line.id, line.plant_id]),
      )
      const devicePlant = new Map(
        devicesResult.data.data
          .filter((device) => device.status === "active")
          .map((device) => [
            device.id,
            linePlant.get(device.production_line_id),
          ]),
      )
      const tagsByPlant = new Map<string, TagPublic[]>()
      for (const tag of tagsResult.data.data.filter(
        (item) => item.is_enabled,
      )) {
        const plantId = devicePlant.get(tag.device_id)
        if (!plantId) continue
        tagsByPlant.set(plantId, [...(tagsByPlant.get(plantId) ?? []), tag])
      }
      return { plants, tagsByPlant }
    },
  })
  const samplesQuery = useQuery({
    queryKey: ["acquisition", "samples", trendTaskId, trendTagId],
    queryFn: async () =>
      (
        await AcquisitionService.readTagSamples({
          path: { tag_id: trendTagId },
          query: { limit: 300, task_id: trendTaskId },
        })
      ).data,
    enabled: activeTab === "trend" && Boolean(trendTagId && trendTaskId),
    refetchInterval: 2000,
  })
  const latestValuesQuery = useQuery({
    queryKey: ["acquisition", "latest-values", latestTaskId],
    queryFn: async () =>
      (
        await AcquisitionService.readLatestTaskValues({
          path: { task_id: latestTaskId },
        })
      ).data,
    enabled: activeTab === "latest" && Boolean(latestTaskId),
    refetchInterval: 2000,
  })

  const tasks = tasksQuery.data?.data ?? []
  const assetData = assetsQuery.data ?? { plants: [], tagsByPlant: new Map() }
  const trendNodes = useMemo(
    () =>
      tasks.flatMap((task) =>
        (task.nodes ?? [])
          .filter((node) => node.is_enabled !== false)
          .map((node) => ({ task, node })),
      ),
    [tasks],
  )
  useEffect(() => {
    if (
      !trendNodes.some(
        ({ task, node }) =>
          task.id === trendTaskId && node.tag_id === trendTagId,
      )
    ) {
      const selectedTask =
        tasks.find((task) => task.id === trendTaskId) ?? tasks[0]
      setTrendTaskId(selectedTask?.id ?? "")
      setTrendTagId(
        selectedTask?.nodes?.find((node) => node.is_enabled !== false)
          ?.tag_id ?? "",
      )
    }
  }, [trendNodes, trendTagId, trendTaskId, tasks])
  useEffect(() => {
    if (tasks.length === 0) {
      setLatestTaskId("")
      return
    }
    if (!tasks.some((task) => task.id === latestTaskId)) {
      setLatestTaskId(tasks[0].id)
    }
  }, [latestTaskId, tasks])

  function selectTask(task: AcquisitionTaskPublic) {
    setLatestTaskId(task.id)
    setLatestSearch("")
    const nodes = (task.nodes ?? []).filter((node) => node.is_enabled !== false)
    setTrendTaskId(task.id)
    setTrendTagId(
      nodes.find((node) => node.tag_id === trendTagId)?.tag_id ??
        nodes[0]?.tag_id ??
        "",
    )
  }

  const latestValues = latestValuesQuery.data?.data ?? []
  const normalizedSearch = latestSearch.trim().toLocaleLowerCase("zh-CN")
  const filteredLatestValues = latestValues.filter((sample) =>
    `${sample.tag_code} ${sample.tag_name} ${sample.node_id}`
      .toLocaleLowerCase("zh-CN")
      .includes(normalizedSearch),
  )

  const controlMutation = useMutation({
    mutationFn: ({
      task,
      start,
    }: {
      task: AcquisitionTaskPublic
      start: boolean
    }) =>
      start
        ? AcquisitionService.startTask({ path: { task_id: task.id } })
        : AcquisitionService.stopTask({ path: { task_id: task.id } }),
    onSuccess: (_, variables) => {
      if (variables.start) selectTask(variables.task)
      showSuccessToast(variables.start ? "采集任务正在启动" : "采集任务已停止")
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["acquisition", "tasks"] }),
  })

  const totals = tasks.reduce(
    (result, task) => ({
      running: result.running + Number(task.desired_state === "running"),
      written: result.written + task.samples_written,
      errors: result.errors + task.error_count,
      dropped: result.dropped + task.dropped_count,
    }),
    { running: 0, written: 0, errors: 0, dropped: 0 },
  )

  if (tasksQuery.isLoading || assetsQuery.isLoading) {
    return (
      <div className="py-12 text-center text-muted-foreground">
        正在加载采集配置…
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Alert>
        <CircleAlert className="size-4" />
        <AlertTitle>当前为教学验证环境</AlertTitle>
        <AlertDescription>
          内置模拟器未启用证书、签名和加密；接入真实 PLC
          前必须补做网络、账户、证书与现场联调。
        </AlertDescription>
      </Alert>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard icon={RadioTower} label="任务总数" value={tasks.length} />
        <MetricCard icon={Cable} label="期望运行" value={totals.running} />
        <MetricCard icon={Gauge} label="累计写入" value={totals.written} />
        <MetricCard
          icon={CircleAlert}
          label="累计错误 / 丢弃"
          value={`${totals.errors} / ${totals.dropped}`}
        />
      </div>

      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="space-y-4"
      >
        <TabsList>
          <TabsTrigger value="tasks">采集任务</TabsTrigger>
          <TabsTrigger value="latest">实时值</TabsTrigger>
          <TabsTrigger value="trend">实时趋势</TabsTrigger>
        </TabsList>
        <TabsContent value="tasks" className="space-y-4">
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-muted-foreground">
              状态每 2 秒刷新；“期望运行”与“实际连接”是两个不同状态。
            </p>
            <TaskDialog assetData={assetData} canWrite={canWrite} />
          </div>
          {tasks.length === 0 ? (
            <div className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">
              尚未创建采集任务。请先在资产管理中建立工厂、设备和测点。
            </div>
          ) : (
            <div className="overflow-x-auto rounded-xl border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>任务</TableHead>
                    <TableHead>连接状态</TableHead>
                    <TableHead>节点</TableHead>
                    <TableHead>写入 / 接收</TableHead>
                    <TableHead>重连 / 错误 / 丢弃</TableHead>
                    <TableHead>最后样本</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tasks.map((task) => (
                    <TableRow key={task.id}>
                      <TableCell>
                        <p className="font-medium">{task.name}</p>
                        <p className="max-w-64 truncate font-mono text-xs text-muted-foreground">
                          {task.endpoint_url}
                        </p>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={statusClass[task.connection_state]}
                        >
                          {statusText[task.connection_state]}
                        </Badge>
                      </TableCell>
                      <TableCell>{task.nodes?.length ?? 0}</TableCell>
                      <TableCell>
                        {task.samples_written} / {task.samples_received}
                      </TableCell>
                      <TableCell>
                        {task.reconnect_count} / {task.error_count} /{" "}
                        {task.dropped_count}
                      </TableCell>
                      <TableCell>{formatTime(task.last_sample_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`查看${task.name}实时值`}
                          onClick={() => {
                            selectTask(task)
                            setActiveTab("latest")
                          }}
                        >
                          <Gauge className="size-4" />
                        </Button>
                        <TaskDialog
                          task={task}
                          assetData={assetData}
                          canWrite={canWrite}
                        />
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!canWrite || controlMutation.isPending}
                          aria-label={
                            task.desired_state === "running"
                              ? `停止${task.name}`
                              : `启动${task.name}`
                          }
                          onClick={() =>
                            controlMutation.mutate({
                              task,
                              start: task.desired_state !== "running",
                            })
                          }
                        >
                          {task.desired_state === "running" ? (
                            <Square className="size-4 text-red-600" />
                          ) : (
                            <Play className="size-4 text-emerald-600" />
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
          {!canWrite && (
            <p className="text-sm text-muted-foreground">
              当前观察者账号仅可查看，不能新建、编辑、启动或停止采集任务。
            </p>
          )}
        </TabsContent>
        <TabsContent value="latest" className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Gauge className="size-5 text-primary" />
                全测点最新值
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <Select
                  value={latestTaskId || undefined}
                  onValueChange={(id) => {
                    const task = tasks.find((item) => item.id === id)
                    if (task) selectTask(task)
                  }}
                >
                  <SelectTrigger
                    aria-label="最新值采集任务"
                    className="w-full bg-background"
                  >
                    <SelectValue placeholder="请选择采集任务" />
                  </SelectTrigger>
                  <SelectContent>
                    {tasks.map((task) => (
                      <SelectItem key={task.id} value={task.id}>
                        {task.name} · {statusText[task.connection_state]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    value={latestSearch}
                    onChange={(event) => setLatestSearch(event.target.value)}
                    className="pl-9"
                    placeholder="按测点编码、名称或 OPC UA NodeId 筛选"
                  />
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                每 2 秒查询一次数据库；并不代表每个测点都有新样本。超过采样周期
                3 倍且至少 60
                秒未更新，标记为“数据滞后”，与助手使用相同规则。质量良好不等于工艺合格。
              </p>
              {tasks.find((task) => task.id === latestTaskId)?.desired_state ===
                "stopped" && (
                <Alert>
                  <CircleAlert />
                  <AlertTitle>采集已停止，以下为最后保存的历史值</AlertTitle>
                  <AlertDescription>
                    刷新页面不会恢复采集，也不会产生新数据。请结合数据时间判断，不能作为当前设备状态。
                  </AlertDescription>
                </Alert>
              )}
              {tasks.find((task) => task.id === latestTaskId)?.endpoint_url ===
                SIMULATOR_ENDPOINT && (
                <p className="text-sm text-muted-foreground">
                  数据来源：内置 OPC UA
                  模拟器。数值为合成演示信号，不是真实产线数据。
                </p>
              )}
              {tasks.length === 0 ? (
                <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                  尚未创建采集任务。
                </div>
              ) : latestValuesQuery.isLoading ? (
                <div className="p-8 text-center text-sm text-muted-foreground">
                  正在读取最新值…
                </div>
              ) : filteredLatestValues.length === 0 ? (
                <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
                  {latestValues.length === 0
                    ? "该任务没有启用的测点映射。"
                    : "没有符合筛选条件的测点。"}
                </div>
              ) : (
                <div className="max-h-[32rem] overflow-auto rounded-xl border">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-background">
                      <TableRow>
                        <TableHead>测点</TableHead>
                        <TableHead>最新记录值</TableHead>
                        <TableHead>数据状态</TableHead>
                        <TableHead>数据时间</TableHead>
                        <TableHead>距采样时间</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredLatestValues.map((sample) => {
                        const state = sampleState(
                          sample,
                          latestValuesQuery.data?.generated_at,
                        )
                        return (
                          <TableRow key={sample.tag_id}>
                            <TableCell>
                              <p className="font-medium">{sample.tag_name}</p>
                              <p className="font-mono text-xs text-muted-foreground">
                                {sample.tag_code}
                              </p>
                            </TableCell>
                            <TableCell>
                              <span className="font-semibold tabular-nums">
                                {formatValue(sample)}
                              </span>
                              {sample.unit && (
                                <span className="ml-1 text-xs text-muted-foreground">
                                  {sample.unit}
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              <Badge
                                variant="outline"
                                className={state.className}
                              >
                                {state.label}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              {formatTime(sample.source_timestamp)}
                            </TableCell>
                            <TableCell className="tabular-nums">
                              {state.ageText}
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="trend" className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Activity className="size-5 text-primary" />
                最近 300 个样本
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="max-w-xl">
                <Select
                  value={
                    trendTagId ? `${trendTaskId}:${trendTagId}` : undefined
                  }
                  onValueChange={(value) => {
                    const selected = trendNodes.find(
                      ({ task, node }) => `${task.id}:${node.tag_id}` === value,
                    )
                    if (!selected) return
                    setTrendTaskId(selected.task.id)
                    setTrendTagId(selected.node.tag_id)
                    setLatestTaskId(selected.task.id)
                    setLatestSearch("")
                  }}
                >
                  <SelectTrigger
                    aria-label="趋势任务测点"
                    className="w-full bg-background"
                  >
                    <SelectValue placeholder="请选择任务测点" />
                  </SelectTrigger>
                  <SelectContent>
                    {trendNodes.map(({ task, node }) => (
                      <SelectItem
                        key={`${task.id}-${node.id}`}
                        value={`${task.id}:${node.tag_id}`}
                      >
                        {task.name} · {node.tag_code} · {node.tag_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <TrendChart samples={samplesQuery.data?.data ?? []} />
              <SampleSummary samples={samplesQuery.data?.data ?? []} />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof RadioTower
  label: string
  value: number | string
}) {
  return (
    <Card className="bg-card/70">
      <CardContent className="flex items-center justify-between p-5">
        <div>
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
        </div>
        <Icon className="size-6 text-primary" />
      </CardContent>
    </Card>
  )
}

function SampleSummary({ samples }: { samples: TagSamplePublic[] }) {
  const latest = samples[samples.length - 1]
  const badCount = samples.filter((sample) => !sample.is_good).length
  return (
    <div className="grid gap-3 text-sm sm:grid-cols-3">
      <div className="rounded-lg bg-muted/50 p-3">
        <p className="text-muted-foreground">最新数值</p>
        <p className="mt-1 font-medium tabular-nums">
          {latest?.numeric_value ?? "—"}
        </p>
      </div>
      <div className="rounded-lg bg-muted/50 p-3">
        <p className="text-muted-foreground">最新时间</p>
        <p className="mt-1 font-medium">
          {formatTime(latest?.source_timestamp)}
        </p>
      </div>
      <div className="rounded-lg bg-muted/50 p-3">
        <p className="text-muted-foreground">已显示样本中的坏质量</p>
        <p className="mt-1 font-medium tabular-nums">{badCount}</p>
      </div>
    </div>
  )
}
