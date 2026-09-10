import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, FileCheck2, HardDrive, Trash2, Upload } from "lucide-react"
import { type FormEvent, useEffect, useState } from "react"

import {
  DataImportsService,
  type ImportBatchPublic,
  type ImportMappingInput,
  type ImportPreviewPublic,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { plantListOptions } from "@/hooks/plantQueries"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

import DataLifecyclePanel from "./DataLifecyclePanel"

const statusLabels: Record<ImportBatchPublic["status"], string> = {
  uploaded: "待处理",
  queued: "排队中",
  duplicate: "重复文件",
  processing: "处理中",
  completed: "已完成",
  failed: "失败",
}

const issueLabels: Record<string, string> = {
  missing_required: "必填项缺失",
  invalid_timestamp: "时间格式错误",
  invalid_value: "数值格式错误",
  out_of_range: "超出测点范围",
  unknown_tag: "未知测点",
  ambiguous_tag: "测点编码不唯一",
  duplicate_in_file: "文件内重复",
  duplicate_existing: "库内重复",
  bad_quality: "质量码异常",
}

const emptyMapping: ImportMappingInput = {
  layout: "long",
  timestamp_column: "",
  tag_code_column: null,
  value_column: null,
  device_code_column: null,
  quality_column: null,
  wide_columns: [],
}

function MappingSelect({
  label,
  value,
  columns,
  required = false,
  onChange,
}: {
  label: string
  value: string | null
  columns: string[]
  required?: boolean
  onChange: (value: string | null) => void
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <select
        className="h-9 rounded-md border border-input bg-background px-3 text-sm"
        value={value ?? ""}
        required={required}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">{required ? "请选择字段" : "不映射"}</option>
        {columns.map((column) => (
          <option key={column} value={column}>
            {column}
          </option>
        ))}
      </select>
    </div>
  )
}

function BatchStatus({ batch }: { batch: ImportBatchPublic }) {
  const variant =
    batch.status === "failed"
      ? "destructive"
      : batch.status === "completed"
        ? "default"
        : "secondary"
  return <Badge variant={variant}>{statusLabels[batch.status]}</Badge>
}

export default function DataGovernanceManager() {
  const { user } = useAuth()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const queryClient = useQueryClient()
  const [plantId, setPlantId] = useState("")
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<ImportPreviewPublic | null>(null)
  const [mapping, setMapping] = useState<ImportMappingInput>(emptyMapping)
  const [selectedBatchSnapshot, setSelectedBatch] =
    useState<ImportBatchPublic | null>(null)
  const canWrite = Boolean(
    user?.is_superuser || user?.role === "admin" || user?.role === "engineer",
  )

  const plantsQuery = useQuery(plantListOptions())
  const plants = plantsQuery.data?.data ?? []
  useEffect(() => {
    if (!plantId && plants[0]) setPlantId(plants[0].id)
  }, [plantId, plants])

  const batchesQuery = useQuery({
    queryKey: ["import-batches", plantId],
    queryFn: async () =>
      (
        await DataImportsService.importsReadImportBatches({
          query: { plant_id: plantId, limit: 100 },
        })
      ).data,
    enabled: Boolean(plantId),
    refetchInterval: (query) =>
      query.state.data?.data.some(
        (batch) => batch.status === "queued" || batch.status === "processing",
      )
        ? 2000
        : false,
  })
  const batches = batchesQuery.data?.data ?? []

  const selectedBatchQuery = useQuery({
    queryKey: ["import-batch-detail", selectedBatchSnapshot?.id],
    queryFn: async () =>
      (
        await DataImportsService.importsReadImportBatch({
          path: { batch_id: selectedBatchSnapshot!.id },
        })
      ).data,
    enabled: Boolean(selectedBatchSnapshot?.id),
    refetchInterval: (query) => {
      const status = query.state.data?.status ?? selectedBatchSnapshot?.status
      return status === "queued" || status === "processing" ? 2000 : false
    },
  })
  const selectedBatch = selectedBatchQuery.data ?? selectedBatchSnapshot
  const jobQuery = useQuery({
    queryKey: ["import-job", selectedBatch?.id, selectedBatch?.status],
    queryFn: async () =>
      (
        await DataImportsService.importsReadImportJob({
          path: { batch_id: selectedBatch!.id },
        })
      ).data,
    enabled: Boolean(selectedBatch?.id),
    refetchInterval: (query) =>
      ["queued", "running"].includes(query.state.data?.status ?? "")
        ? 2000
        : false,
  })

  const mappingOptionsQuery = useQuery({
    queryKey: ["import-mapping-options", plantId],
    queryFn: async () =>
      (
        await DataImportsService.importsReadMappingOptions({
          query: { plant_id: plantId },
        })
      ).data,
    enabled: Boolean(plantId),
  })
  const mappingOptions = mappingOptionsQuery.data ?? []

  const issuesQuery = useQuery({
    queryKey: ["import-issues", selectedBatch?.id],
    queryFn: async () =>
      (
        await DataImportsService.importsReadQualityIssues({
          path: { batch_id: selectedBatch!.id },
          query: { limit: 200 },
        })
      ).data,
    enabled: Boolean(selectedBatch?.issue_count),
  })

  const previewMutation = useMutation({
    mutationFn: async () => {
      if (!plantId || !sourceFile) throw new Error("请先选择工厂和文件")
      return (
        await DataImportsService.importsPreviewImport({
          query: { plant_id: plantId },
          body: { file: sourceFile },
        })
      ).data
    },
    onSuccess: (data) => {
      setPreview(data)
      setMapping(
        data.suggested_mapping ?? {
          ...emptyMapping,
          layout: data.detected_layout,
          timestamp_column: data.suggested_timestamp_column ?? "",
        },
      )
      setSelectedBatch(data.batch)
      if (data.batch.status === "duplicate") {
        showErrorToast("文件内容与已有批次完全相同，系统未重复保存或入库")
      } else {
        showSuccessToast("文件解析成功，请确认字段映射后执行导入")
      }
      queryClient.invalidateQueries({ queryKey: ["import-batches"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const processMutation = useMutation({
    mutationFn: async () => {
      if (!preview) throw new Error("没有待处理的导入批次")
      return (
        await DataImportsService.importsProcessImport({
          path: { batch_id: preview.batch.id },
          body: mapping,
        })
      ).data
    },
    onSuccess: (batch) => {
      setSelectedBatch(batch)
      queryClient.setQueryData(["import-batch-detail", batch.id], batch)
      setPreview(null)
      setSourceFile(null)
      showSuccessToast("导入任务已提交，可离开页面后查看进度与结果")
      queryClient.invalidateQueries({ queryKey: ["import-batches"] })
      queryClient.invalidateQueries({ queryKey: ["import-issues", batch.id] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const retryMutation = useMutation({
    mutationFn: async () => {
      if (!selectedBatch?.mapping_config)
        throw new Error("没有保存的映射，请重新上传并确认映射")
      return (
        await DataImportsService.importsProcessImport({
          path: { batch_id: selectedBatch.id },
          body: selectedBatch.mapping_config as ImportMappingInput,
        })
      ).data
    },
    onSuccess: (batch) => {
      setSelectedBatch(batch)
      queryClient.setQueryData(["import-batch-detail", batch.id], batch)
      queryClient.invalidateQueries({ queryKey: ["import-batches"] })
      queryClient.invalidateQueries({ queryKey: ["import-job", batch.id] })
      showSuccessToast("重试任务已提交；原失败记录保留")
    },
    onError: handleError.bind(showErrorToast),
  })

  const deleteSourceMutation = useMutation({
    mutationFn: (batchId: string) =>
      DataImportsService.importsDeleteSourceFile({
        path: { batch_id: batchId },
      }),
    onSuccess: () => {
      showSuccessToast("源文件已删除，批次记录和入库数据不受影响")
      setSelectedBatch((current) =>
        current ? { ...current, source_file_available: false } : current,
      )
      queryClient.invalidateQueries({ queryKey: ["import-batches"] })
      queryClient.invalidateQueries({ queryKey: ["import-batch-detail"] })
    },
    onError: handleError.bind(showErrorToast),
  })

  const submitPreview = (event: FormEvent) => {
    event.preventDefault()
    previewMutation.mutate()
  }

  const updateMapping = (
    field: keyof ImportMappingInput,
    value: string | null,
  ) => {
    setMapping((current) => ({ ...current, [field]: value }))
  }

  const changeLayout = (layout: ImportMappingInput["layout"]) => {
    setMapping((current) => ({
      ...current,
      layout,
      tag_code_column: layout === "long" ? "" : null,
      value_column: layout === "long" ? "" : null,
      device_code_column: null,
      wide_columns: [],
    }))
  }

  const updateWideColumn = (sourceColumn: string, optionValue: string) => {
    setMapping((current) => {
      const retained = (current.wide_columns ?? []).filter(
        (item) => item.source_column !== sourceColumn,
      )
      if (!optionValue) return { ...current, wide_columns: retained }
      const option = mappingOptions.find((item) => item.tag_id === optionValue)
      if (!option) return current
      return {
        ...current,
        wide_columns: [
          ...retained,
          {
            source_column: sourceColumn,
            tag_code: option.tag_code,
            device_code: option.device_code,
          },
        ],
      }
    })
  }

  const downloadIssues = async (batchId: string) => {
    const response = await fetch(`/api/v1/imports/${batchId}/issues.csv`, {
      headers: {
        Authorization: `Bearer ${localStorage.getItem("access_token")}`,
      },
    })
    if (!response.ok) {
      showErrorToast("质量问题文件下载失败")
      return
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = `import-${batchId}-quality-issues.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6">
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">可追溯批次</CardTitle>
          </CardHeader>
          <CardContent className="text-3xl font-semibold">
            {batches.length}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">已接收数据</CardTitle>
          </CardHeader>
          <CardContent className="text-3xl font-semibold">
            {batches
              .reduce((sum, batch) => sum + batch.accepted_rows, 0)
              .toLocaleString()}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">拒绝数据</CardTitle>
          </CardHeader>
          <CardContent className="text-3xl font-semibold text-destructive">
            {batches
              .reduce((sum, batch) => sum + batch.rejected_rows, 0)
              .toLocaleString()}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <Upload className="size-5" /> 文件导入与字段映射
          </CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="grid gap-4 md:grid-cols-[1fr_2fr_auto] md:items-end"
            onSubmit={submitPreview}
          >
            <div className="grid gap-2">
              <Label htmlFor="import-plant">所属工厂</Label>
              <select
                id="import-plant"
                className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                value={plantId}
                onChange={(event) => {
                  setPlantId(event.target.value)
                  setPreview(null)
                  setSelectedBatch(null)
                }}
              >
                {plants.map((plant) => (
                  <option key={plant.id} value={plant.id}>
                    {plant.code} · {plant.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="source-file">CSV / XLSX 文件（最大 50 MB）</Label>
              <Input
                id="source-file"
                type="file"
                accept=".csv,.xlsx"
                disabled={!canWrite}
                onChange={(event) =>
                  setSourceFile(event.target.files?.[0] ?? null)
                }
              />
            </div>
            <Button
              type="submit"
              disabled={
                !canWrite ||
                !plantId ||
                !sourceFile ||
                previewMutation.isPending
              }
            >
              {previewMutation.isPending ? "正在解析" : "上传并预览"}
            </Button>
          </form>
          {!canWrite && (
            <p className="mt-3 text-sm text-muted-foreground">
              观察者可查看批次与质量问题，但不能上传或处理文件。
            </p>
          )}
        </CardContent>
      </Card>

      {preview && preview.batch.status !== "duplicate" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">确认字段映射</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="rounded-md border bg-muted/30 p-4 text-sm">
              <p className="font-medium">先判断源文件结构</p>
              <p className="mt-1 text-muted-foreground">
                窄表是一行一个测点值；宽表是一行一个时间点、多个测量列。系统检测结果仅作提示，最终由你确认。
              </p>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <div className="grid gap-2">
                <Label>表结构 *</Label>
                <select
                  className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                  value={mapping.layout ?? "long"}
                  onChange={(event) =>
                    changeLayout(event.target.value as "long" | "wide")
                  }
                >
                  <option value="long">窄表：时间 + 测点编码 + 值</option>
                  <option value="wide">宽表：时间 + 多个测量列</option>
                </select>
              </div>
              <MappingSelect
                label="数据时间 *"
                value={mapping.timestamp_column}
                columns={preview.source_columns}
                required
                onChange={(value) =>
                  updateMapping("timestamp_column", value ?? "")
                }
              />
              <MappingSelect
                label="质量码"
                value={mapping.quality_column ?? null}
                columns={preview.source_columns}
                onChange={(value) => updateMapping("quality_column", value)}
              />
            </div>
            {mapping.layout === "long" ? (
              <div className="grid gap-4 md:grid-cols-3">
                <MappingSelect
                  label="测点编码 *"
                  value={mapping.tag_code_column ?? null}
                  columns={preview.source_columns}
                  required
                  onChange={(value) =>
                    updateMapping("tag_code_column", value ?? "")
                  }
                />
                <MappingSelect
                  label="测量值 *"
                  value={mapping.value_column ?? null}
                  columns={preview.source_columns}
                  required
                  onChange={(value) =>
                    updateMapping("value_column", value ?? "")
                  }
                />
                <MappingSelect
                  label="设备编码"
                  value={mapping.device_code_column ?? null}
                  columns={preview.source_columns}
                  onChange={(value) =>
                    updateMapping("device_code_column", value)
                  }
                />
              </div>
            ) : (
              <div className="space-y-3 rounded-md border p-4">
                <div>
                  <p className="font-medium">宽表测量列映射</p>
                  <p className="text-sm text-muted-foreground">
                    每个要入库的源列必须绑定到一个已启用的“设备 ·
                    测点”；不选择即跳过该列。
                  </p>
                </div>
                <div className="grid gap-3">
                  {preview.source_columns
                    .filter(
                      (column) =>
                        column !== mapping.timestamp_column &&
                        column !== mapping.quality_column,
                    )
                    .map((column) => {
                      const current = (mapping.wide_columns ?? []).find(
                        (item) => item.source_column === column,
                      )
                      const currentOption = mappingOptions.find(
                        (item) =>
                          item.tag_code === current?.tag_code &&
                          item.device_code === current?.device_code,
                      )
                      return (
                        <div
                          key={column}
                          className="grid items-center gap-2 md:grid-cols-[minmax(12rem,1fr)_2fr]"
                        >
                          <div className="truncate text-sm" title={column}>
                            {column}
                          </div>
                          <select
                            className="h-9 rounded-md border border-input bg-background px-3 text-sm"
                            value={currentOption?.tag_id ?? ""}
                            onChange={(event) =>
                              updateWideColumn(column, event.target.value)
                            }
                          >
                            <option value="">不导入此列</option>
                            {mappingOptions.map((option) => (
                              <option key={option.tag_id} value={option.tag_id}>
                                {option.device_code} · {option.device_name} /{" "}
                                {option.tag_code} · {option.tag_name}
                              </option>
                            ))}
                          </select>
                        </div>
                      )
                    })}
                </div>
                {!mappingOptions.length && (
                  <p className="text-sm text-destructive">
                    当前工厂没有可映射的启用测点，请先在资产管理中创建并启用设备与测点。
                  </p>
                )}
              </div>
            )}
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    {preview.source_columns.map((column) => (
                      <TableHead key={column}>{column}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.preview_rows.slice(0, 8).map((row, index) => (
                    <TableRow key={`${preview.batch.id}-${index}`}>
                      {preview.source_columns.map((column) => (
                        <TableCell key={column}>
                          {String(row[column] ?? "")}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setPreview(null)}>
                取消
              </Button>
              <Button
                disabled={
                  !mapping.timestamp_column ||
                  (mapping.layout === "long"
                    ? !mapping.tag_code_column || !mapping.value_column
                    : !(mapping.wide_columns ?? []).length) ||
                  processMutation.isPending
                }
                onClick={() => processMutation.mutate()}
              >
                <FileCheck2 className="mr-2 size-4" />
                {processMutation.isPending
                  ? "正在提交任务"
                  : "执行质量校验并入库"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">导入批次与质量结果</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>文件</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>测量记录数</TableHead>
                <TableHead>接收</TableHead>
                <TableHead>拒绝</TableHead>
                <TableHead>问题</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {batches.map((batch) => (
                <TableRow
                  key={batch.id}
                  className="cursor-pointer"
                  onClick={() => setSelectedBatch(batch)}
                >
                  <TableCell>
                    <div className="font-medium">{batch.original_filename}</div>
                    <div className="text-xs text-muted-foreground">
                      {(batch.file_size_bytes / 1024).toFixed(1)} KB ·{" "}
                      {batch.file_sha256.slice(0, 10)}
                    </div>
                  </TableCell>
                  <TableCell>
                    <BatchStatus batch={batch} />
                  </TableCell>
                  <TableCell>{batch.total_rows}</TableCell>
                  <TableCell>{batch.accepted_rows}</TableCell>
                  <TableCell
                    className={batch.rejected_rows ? "text-destructive" : ""}
                  >
                    {batch.rejected_rows}
                  </TableCell>
                  <TableCell>{batch.issue_count}</TableCell>
                  <TableCell>
                    {new Date(batch.created_at).toLocaleString("zh-CN")}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={(event) => {
                        event.stopPropagation()
                        setSelectedBatch(batch)
                      }}
                    >
                      查看
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {!batches.length && (
                <TableRow>
                  <TableCell
                    colSpan={8}
                    className="py-10 text-center text-muted-foreground"
                  >
                    当前工厂还没有导入批次
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {selectedBatch && (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-3">
            <div>
              <CardTitle className="text-lg">
                批次详情：{selectedBatch.original_filename}
              </CardTitle>
              <p className="mt-1 text-sm text-muted-foreground">
                批次 ID：{selectedBatch.id}
              </p>
            </div>
            <div className="flex gap-2">
              {canWrite &&
                selectedBatch.status === "failed" &&
                selectedBatch.source_file_available &&
                selectedBatch.mapping_config && (
                  <Button
                    variant="outline"
                    disabled={retryMutation.isPending}
                    onClick={() => retryMutation.mutate()}
                  >
                    按原映射重试
                  </Button>
                )}
              {selectedBatch.issue_count > 0 && (
                <Button
                  variant="outline"
                  onClick={() => downloadIssues(selectedBatch.id)}
                >
                  <Download className="mr-2 size-4" />
                  导出问题
                </Button>
              )}
              {canWrite &&
                selectedBatch.source_file_available &&
                selectedBatch.status !== "queued" &&
                selectedBatch.status !== "processing" && (
                  // Queued jobs own their source too; removing it would break recovery.
                  <Button
                    variant="outline"
                    onClick={() =>
                      deleteSourceMutation.mutate(selectedBatch.id)
                    }
                  >
                    <Trash2 className="mr-2 size-4" />
                    删除源文件
                  </Button>
                )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {jobQuery.data && (
              <section
                className="rounded-md border bg-muted/30 p-3 text-sm"
                aria-label="导入任务进度"
              >
                <p>
                  任务 ID：{jobQuery.data.id} · 执行次数{" "}
                  {jobQuery.data.attempts}
                </p>
                <p>
                  本次已扫描 {jobQuery.data.processed_rows}{" "}
                  条测量记录；处理中统计不是已提交数据，只有批次完成后才确认入库。
                </p>
                {jobQuery.data.status === "queued" && (
                  <p>
                    等待后台处理。长时间排队请检查 import-worker
                    是否运行；刷新或关闭页面不会取消任务。
                  </p>
                )}
                {jobQuery.data.status === "running" && (
                  <p>
                    后台处理中；工作进程异常退出后会在恢复等待期后重新处理，默认最多自动尝试
                    3 次。
                  </p>
                )}
                {jobQuery.data.heartbeat_at && (
                  <p>
                    最后进度时间：
                    {new Date(jobQuery.data.heartbeat_at).toLocaleString(
                      "zh-CN",
                    )}
                  </p>
                )}
              </section>
            )}
            {(jobQuery.isError || selectedBatchQuery.isError) && (
              <p className="text-destructive">
                进度查询失败，请刷新重试；这不代表后台任务已经取消。
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <BatchStatus batch={selectedBatch} />
              <Badge variant="outline">
                <HardDrive className="mr-1 size-3" />
                源文件{selectedBatch.source_file_available ? "保留" : "已清理"}
              </Badge>
              <Badge variant="outline">
                接收 {selectedBatch.accepted_rows}
              </Badge>
              <Badge variant="outline">
                拒绝 {selectedBatch.rejected_rows}
              </Badge>
              <Badge variant="outline">
                警告行 {selectedBatch.warning_rows}
              </Badge>
            </div>
            {selectedBatch.error_message && (
              <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {selectedBatch.error_message}
              </p>
            )}
            {issuesQuery.data?.data?.length ? (
              <div className="overflow-x-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>行号</TableHead>
                      <TableHead>级别</TableHead>
                      <TableHead>类型</TableHead>
                      <TableHead>字段</TableHead>
                      <TableHead>原始值</TableHead>
                      <TableHead>说明</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {issuesQuery.data.data.map((issue) => (
                      <TableRow key={issue.id}>
                        <TableCell>{issue.row_number}</TableCell>
                        <TableCell>
                          <Badge
                            variant={
                              issue.severity === "error"
                                ? "destructive"
                                : "secondary"
                            }
                          >
                            {issue.severity === "error" ? "错误" : "警告"}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {issueLabels[issue.issue_type] ?? issue.issue_type}
                        </TableCell>
                        <TableCell>{issue.field_name ?? "-"}</TableCell>
                        <TableCell className="max-w-48 truncate">
                          {issue.raw_value ?? "-"}
                        </TableCell>
                        <TableCell>{issue.message}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : selectedBatch.issue_count > 0 ? (
              <p className="text-sm text-muted-foreground">
                正在加载质量问题……
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                该批次没有质量问题。
              </p>
            )}
            {selectedBatch.issues_truncated && (
              <p className="text-sm text-amber-600">
                问题数量超过保存上限，界面和导出文件仅包含前{" "}
                {selectedBatch.stored_issue_count} 条。
              </p>
            )}
          </CardContent>
        </Card>
      )}
      <DataLifecyclePanel
        key={plantId}
        plantId={plantId}
        plantName={plants.find((plant) => plant.id === plantId)?.name ?? ""}
        canWrite={canWrite}
        isAdmin={Boolean(user?.is_superuser || user?.role === "admin")}
      />
    </div>
  )
}
