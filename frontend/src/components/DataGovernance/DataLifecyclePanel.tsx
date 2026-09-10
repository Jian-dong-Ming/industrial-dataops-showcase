import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { DataLifecycleService } from "@/client"
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
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

function gib(value: number | null | undefined) {
  return value == null ? "暂无统计" : `${(value / 1024 ** 3).toFixed(2)} GiB`
}

function localTime(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  })
}

export default function DataLifecyclePanel({
  plantId,
  plantName,
  canWrite,
  isAdmin,
}: {
  plantId: string
  plantName: string
  canWrite: boolean
  isAdmin: boolean
}) {
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const [keepDays, setKeepDays] = useState("7")
  const [source, setSource] = useState<"opcua" | "file">("opcua")
  const [draftRate, setDraftRate] = useState("24")
  const [rate, setRate] = useState(24)
  const capacity = useQuery({
    queryKey: ["data-lifecycle-capacity", rate],
    queryFn: async () =>
      (
        await DataLifecycleService.lifecycleReadCapacity({
          query: { planning_rows_per_second: rate },
        })
      ).data,
    enabled: isAdmin,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  })
  const previews = useQuery({
    queryKey: ["retention-previews", plantId],
    queryFn: async () =>
      (
        await DataLifecycleService.lifecycleListRetentionPreviews({
          query: { plant_id: plantId },
        })
      ).data,
    enabled: Boolean(plantId),
    refetchOnWindowFocus: false,
  })
  const createPreview = useMutation({
    mutationFn: async () =>
      (
        await DataLifecycleService.lifecycleCreateRetentionPreview({
          body: {
            plant_id: plantId,
            source_type: source,
            keep_days: Number(keepDays),
          },
        })
      ).data,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["retention-previews", plantId],
      })
      showSuccessToast("范围预览已保存，未归档或删除数据")
    },
    onError: handleError.bind(showErrorToast),
  })
  const validDays =
    Number.isInteger(Number(keepDays)) &&
    Number(keepDays) >= 1 &&
    Number(keepDays) <= 3650
  const validRate =
    draftRate !== "" &&
    Number.isFinite(Number(draftRate)) &&
    Number(draftRate) >= 0 &&
    Number(draftRate) <= 1_000_000

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">容量与数据保留预览</CardTitle>
        <p className="text-sm text-muted-foreground">
          当前工厂：{plantName || "请先选择工厂"}
          。这里只做规划和范围核对，不会自动清理数据。
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        {isAdmin && (
          <section
            aria-label="全库容量"
            className="space-y-3 rounded-md border p-4"
          >
            <h3 className="font-medium">全库容量（管理员）</h3>
            {capacity.isPending && (
              <p className="text-sm">正在读取数据库统计…</p>
            )}
            {capacity.isError && (
              <p role="alert" className="text-sm text-destructive">
                容量读取失败，请稍后重试；未执行清理。
              </p>
            )}
            {capacity.data && (
              <>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                  <div>
                    数据库总占用
                    <p className="mt-1 text-xl font-semibold">
                      {gib(capacity.data.database_bytes)}
                    </p>
                  </div>
                  <div>
                    样本表含索引
                    <p className="mt-1 text-xl font-semibold">
                      {gib(capacity.data.sample_total_bytes)}
                    </p>
                  </div>
                  <div>
                    估计活跃样本数
                    <p className="mt-1 text-xl font-semibold">
                      {capacity.data.estimated_live_rows.toLocaleString()}
                    </p>
                  </div>
                  <div>
                    规划日增量（估算）
                    <p className="mt-1 text-xl font-semibold">
                      {gib(capacity.data.estimated_daily_bytes)}
                    </p>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  {capacity.data.note}
                </p>
                <p className="text-xs text-muted-foreground">
                  读取时间（北京时间）：{localTime(capacity.data.measured_at)}；
                  样本统计分析时间：
                  {capacity.data.last_analyze
                    ? localTime(capacity.data.last_analyze)
                    : "暂无记录，行数估计可能尚未更新"}
                </p>
              </>
            )}
            <div className="flex flex-wrap items-end gap-3">
              <div className="space-y-1">
                <Label htmlFor="planning-rate">
                  规划写入速率（条/秒，非实测）
                </Label>
                <Input
                  id="planning-rate"
                  type="number"
                  min="0"
                  max="1000000"
                  value={draftRate}
                  onChange={(e) => setDraftRate(e.target.value)}
                  className="max-w-48"
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={!validRate || capacity.isFetching}
                onClick={() => {
                  if (Number(draftRate) === rate) capacity.refetch()
                  else setRate(Number(draftRate))
                }}
              >
                更新估算
              </Button>
            </div>
          </section>
        )}
        <form
          className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault()
            if (validDays && canWrite && plantId) createPreview.mutate()
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="retention-source">预览数据来源</Label>
            <select
              id="retention-source"
              value={source}
              onChange={(e) => setSource(e.target.value as "opcua" | "file")}
              disabled={!canWrite}
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
            >
              <option value="opcua">OPC UA 采集样本</option>
              <option value="file">历史文件入库样本（不是原文件）</option>
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="retention-days">计划保留最近天数</Label>
            <Input
              id="retention-days"
              type="number"
              min="1"
              max="3650"
              step="1"
              value={keepDays}
              disabled={!canWrite}
              onChange={(e) => setKeepDays(e.target.value)}
            />
          </div>
          <Button
            type="submit"
            variant="outline"
            disabled={
              !canWrite || !plantId || !validDays || createPreview.isPending
            }
          >
            {createPreview.isPending
              ? "正在核对范围…"
              : "预览更早的数据（不删除）"}
          </Button>
        </form>
        <p className="text-sm text-muted-foreground">
          按源采样时间核对，不按上传或接收时间。保留天数只是本次规划参数，并未启用自动策略。归档验证、实际清理与明确确认流程尚未开放。
        </p>
        {!canWrite && (
          <p className="text-sm text-muted-foreground">
            观察者只能查看已有预览，不能创建规划记录。
          </p>
        )}
        {previews.isError && (
          <p role="alert" className="text-sm text-destructive">
            预览记录加载失败。
          </p>
        )}
        <div className="overflow-x-auto">
          <Table aria-label="数据保留预览记录">
            <TableHeader>
              <TableRow>
                <TableHead>创建时间（北京时间）</TableHead>
                <TableHead>来源</TableHead>
                <TableHead>保留天数</TableHead>
                <TableHead>预览截止时间（北京时间）</TableHead>
                <TableHead>候选行数</TableHead>
                <TableHead>扫描内测点数</TableHead>
                <TableHead>执行结果</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(previews.data ?? []).map((row) => (
                <TableRow key={row.id}>
                  <TableCell>{localTime(row.created_at)}</TableCell>
                  <TableCell>
                    {row.source_type === "file" ? "历史文件样本" : "OPC UA样本"}
                  </TableCell>
                  <TableCell>{row.keep_days}</TableCell>
                  <TableCell>{localTime(row.cutoff)} 之前</TableCell>
                  <TableCell>
                    {row.count_is_exact ? "" : "至少 "}
                    {row.matched_rows.toLocaleString()}
                    {!row.count_is_exact && (
                      <p className="text-xs text-muted-foreground">
                        达到扫描上限，非全量计数
                      </p>
                    )}
                  </TableCell>
                  <TableCell>{row.affected_tags_in_scan}</TableCell>
                  <TableCell>仅预览，未删除</TableCell>
                </TableRow>
              ))}
              {!previews.isPending && !previews.data?.length && (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center text-muted-foreground"
                  >
                    当前工厂暂无预览记录
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
        <p className="text-xs text-muted-foreground">
          只显示当前工厂最近20次预览。按当次范围内最大ID限制候选，不是不可变事务快照；实际归档和清理前仍需重新核验范围、权限和备份。
        </p>
      </CardContent>
    </Card>
  )
}
