import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Pencil, Plus, Power, PowerOff } from "lucide-react"
import { type FormEvent, useState } from "react"

import {
  type DevicePublic,
  DevicesService,
  type PlantPublic,
  PlantsService,
  type ProductionLinePublic,
  ProductionLinesService,
  type TagPublic,
  TagsService,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { handleError } from "@/utils"

type AssetKind = "plant" | "line" | "device" | "tag"
type AssetRow = PlantPublic | ProductionLinePublic | DevicePublic | TagPublic

type FormValues = {
  code: string
  name: string
  parentId: string
  detail: string
  unit: string
  minValue: string
  maxValue: string
  samplingInterval: string
  dataType: "float" | "integer" | "boolean" | "string"
}

const emptyValues: FormValues = {
  code: "",
  name: "",
  parentId: "",
  detail: "",
  unit: "",
  minValue: "",
  maxValue: "",
  samplingInterval: "1000",
  dataType: "float",
}

const kindLabels: Record<AssetKind, string> = {
  plant: "工厂",
  line: "产线",
  device: "设备",
  tag: "测点",
}

function getAssetDetail(kind: AssetKind, row: AssetRow): string {
  switch (kind) {
    case "plant":
      return (row as PlantPublic).location ?? ""
    case "line":
      return (row as ProductionLinePublic).process_type
    case "device":
      return (row as DevicePublic).device_type
    case "tag":
      return (row as TagPublic).unit ?? ""
  }
}

function isAssetActive(kind: AssetKind, row: AssetRow): boolean {
  return kind === "tag"
    ? (row as TagPublic).is_enabled !== false
    : (row as PlantPublic | ProductionLinePublic | DevicePublic).status ===
        "active"
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={label}>{label}</Label>
      <Select value={value || undefined} onValueChange={onChange} required>
        <SelectTrigger id={label} className="w-full bg-background">
          <SelectValue placeholder="请选择" />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function CreateAssetDialog({
  kind,
  parentOptions,
  onCreate,
  disabled = false,
}: {
  kind: AssetKind
  parentOptions?: { value: string; label: string }[]
  onCreate: (values: FormValues) => Promise<unknown>
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [values, setValues] = useState<FormValues>(emptyValues)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const mutation = useMutation({
    mutationFn: () => onCreate(values),
    onSuccess: () => {
      showSuccessToast(`${kindLabels[kind]}创建成功`)
      setValues(emptyValues)
      setOpen(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["assets"] }),
  })

  const update = (field: keyof FormValues, value: string) => {
    setValues((current) => ({ ...current, [field]: value }))
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    mutation.mutate()
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button disabled={disabled}>
          <Plus className="mr-2 size-4" />
          新增{kindLabels[kind]}
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>新增{kindLabels[kind]}</DialogTitle>
          <DialogDescription>
            编码创建后不可修改，只允许大写字母、数字、下划线和连字符。
          </DialogDescription>
        </DialogHeader>
        <form className="grid gap-4" onSubmit={submit}>
          {kind !== "plant" && (
            <SelectField
              label={
                kind === "line"
                  ? "所属工厂"
                  : kind === "device"
                    ? "所属产线"
                    : "所属设备"
              }
              value={values.parentId}
              onChange={(value) => update("parentId", value)}
              options={parentOptions ?? []}
            />
          )}
          <div className="grid grid-cols-2 gap-4">
            <div className="grid gap-2">
              <Label htmlFor={`${kind}-code`}>编码</Label>
              <Input
                id={`${kind}-code`}
                value={values.code}
                onChange={(event) =>
                  update("code", event.target.value.toUpperCase())
                }
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor={`${kind}-name`}>名称</Label>
              <Input
                id={`${kind}-name`}
                value={values.name}
                onChange={(event) => update("name", event.target.value)}
                required
              />
            </div>
          </div>
          {kind !== "tag" && (
            <div className="grid gap-2">
              <Label htmlFor={`${kind}-detail`}>
                {kind === "plant"
                  ? "位置"
                  : kind === "line"
                    ? "工艺类型"
                    : "设备类型"}
              </Label>
              <Input
                id={`${kind}-detail`}
                value={values.detail}
                onChange={(event) => update("detail", event.target.value)}
                required={kind !== "plant"}
              />
            </div>
          )}
          {kind === "tag" && (
            <>
              <div className="grid grid-cols-2 gap-4">
                <SelectField
                  label="数据类型"
                  value={values.dataType}
                  onChange={(value) => update("dataType", value)}
                  options={[
                    { value: "float", label: "浮点数" },
                    { value: "integer", label: "整数" },
                    { value: "boolean", label: "布尔值" },
                    { value: "string", label: "字符串" },
                  ]}
                />
                <div className="grid gap-2">
                  <Label htmlFor="tag-unit">单位</Label>
                  <Input
                    id="tag-unit"
                    value={values.unit}
                    onChange={(event) => update("unit", event.target.value)}
                  />
                </div>
              </div>
              <div className="grid grid-cols-3 gap-4">
                <Input
                  aria-label="最小值"
                  type="number"
                  placeholder="最小值"
                  value={values.minValue}
                  onChange={(event) => update("minValue", event.target.value)}
                />
                <Input
                  aria-label="最大值"
                  type="number"
                  placeholder="最大值"
                  value={values.maxValue}
                  onChange={(event) => update("maxValue", event.target.value)}
                />
                <Input
                  aria-label="采样周期"
                  type="number"
                  min={100}
                  value={values.samplingInterval}
                  onChange={(event) =>
                    update("samplingInterval", event.target.value)
                  }
                  required
                />
              </div>
            </>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setOpen(false)}
            >
              取消
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "保存中…" : "保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function EditAssetDialog({
  kind,
  row,
  onSave,
}: {
  kind: AssetKind
  row: AssetRow
  onSave: (name: string, detail: string) => Promise<unknown>
}) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState(row.name)
  const initialDetail = getAssetDetail(kind, row)
  const [detail, setDetail] = useState(initialDetail)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const mutation = useMutation({
    mutationFn: () => onSave(name, detail),
    onSuccess: () => {
      showSuccessToast(`${kindLabels[kind]}更新成功`)
      setOpen(false)
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["assets"] }),
  })

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="ghost" aria-label={`编辑${row.name}`}>
          <Pencil className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>编辑{kindLabels[kind]}</DialogTitle>
          <DialogDescription>编码 {row.code} 保持不变。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor={`edit-name-${row.id}`}>名称</Label>
            <Input
              id={`edit-name-${row.id}`}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor={`edit-detail-${row.id}`}>
              {kind === "plant"
                ? "位置"
                : kind === "line"
                  ? "工艺类型"
                  : kind === "device"
                    ? "设备类型"
                    : "单位"}
            </Label>
            <Input
              id={`edit-detail-${row.id}`}
              value={detail}
              onChange={(event) => setDetail(event.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            取消
          </Button>
          <Button
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AssetTable({
  kind,
  rows,
  canWrite,
  parentName,
  onSave,
  onToggle,
}: {
  kind: AssetKind
  rows: AssetRow[]
  canWrite: boolean
  parentName?: (row: AssetRow) => string
  onSave: (row: AssetRow, name: string, detail: string) => Promise<unknown>
  onToggle: (row: AssetRow, nextActive: boolean) => Promise<unknown>
}) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const toggleMutation = useMutation({
    mutationFn: ({ row, nextActive }: { row: AssetRow; nextActive: boolean }) =>
      onToggle(row, nextActive),
    onSuccess: (_data, variables) =>
      showSuccessToast(variables.nextActive ? "资产已重新启用" : "资产已禁用"),
    onError: handleError.bind(showErrorToast),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["assets"] }),
  })

  if (rows.length === 0) {
    return (
      <div className="rounded-md border p-10 text-center text-muted-foreground">
        暂无数据
      </div>
    )
  }
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>编码</TableHead>
            <TableHead>名称</TableHead>
            {parentName && <TableHead>所属对象</TableHead>}
            <TableHead>类型/单位</TableHead>
            <TableHead>状态</TableHead>
            {canWrite && <TableHead className="text-right">操作</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const detail = getAssetDetail(kind, row)
            const active = isAssetActive(kind, row)
            return (
              <TableRow
                key={row.id}
                className={active ? undefined : "opacity-70"}
              >
                <TableCell className="font-mono text-xs">{row.code}</TableCell>
                <TableCell className="font-medium">{row.name}</TableCell>
                {parentName && <TableCell>{parentName(row)}</TableCell>}
                <TableCell>{detail || "—"}</TableCell>
                <TableCell>
                  <Badge
                    variant="outline"
                    className={
                      active
                        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                        : "border-slate-500/30 bg-slate-500/10 text-slate-600 dark:text-slate-300"
                    }
                  >
                    {active ? "启用" : "禁用"}
                  </Badge>
                </TableCell>
                {canWrite && (
                  <TableCell className="text-right">
                    <EditAssetDialog
                      kind={kind}
                      row={row}
                      onSave={(name, value) => onSave(row, name, value)}
                    />
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`${active ? "禁用" : "启用"}${row.name}`}
                      title={active ? "禁用资产" : "重新启用资产"}
                      disabled={toggleMutation.isPending}
                      onClick={() =>
                        toggleMutation.mutate({ row, nextActive: !active })
                      }
                    >
                      {active ? (
                        <PowerOff className="size-4" />
                      ) : (
                        <Power className="size-4 text-emerald-600 dark:text-emerald-400" />
                      )}
                    </Button>
                  </TableCell>
                )}
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

export default function AssetManager() {
  const { user } = useAuth()
  const isAdmin = Boolean(user?.is_superuser || user?.role === "admin")
  const isEngineer = user?.role === "engineer"
  const canManageNested = isAdmin || isEngineer

  const plantsQuery = useQuery({
    queryKey: ["assets", "plants"],
    queryFn: async () =>
      (await PlantsService.readPlants({ query: { limit: 100 } })).data,
  })
  const linesQuery = useQuery({
    queryKey: ["assets", "lines"],
    queryFn: async () =>
      (
        await ProductionLinesService.linesReadProductionLines({
          query: { limit: 100 },
        })
      ).data,
  })
  const devicesQuery = useQuery({
    queryKey: ["assets", "devices"],
    queryFn: async () =>
      (await DevicesService.readDevices({ query: { limit: 100 } })).data,
  })
  const tagsQuery = useQuery({
    queryKey: ["assets", "tags"],
    queryFn: async () =>
      (await TagsService.readTags({ query: { limit: 100 } })).data,
  })

  const plants = plantsQuery.data?.data ?? []
  const lines = linesQuery.data?.data ?? []
  const devices = devicesQuery.data?.data ?? []
  const tags = tagsQuery.data?.data ?? []
  const plantNames = new Map(plants.map((row) => [row.id, row.name]))
  const lineNames = new Map(lines.map((row) => [row.id, row.name]))
  const deviceNames = new Map(devices.map((row) => [row.id, row.name]))

  const loading =
    plantsQuery.isLoading ||
    linesQuery.isLoading ||
    devicesQuery.isLoading ||
    tagsQuery.isLoading
  if (loading)
    return (
      <div className="py-12 text-center text-muted-foreground">
        正在加载资产数据…
      </div>
    )

  return (
    <Tabs defaultValue="plants" className="space-y-4">
      <TabsList>
        <TabsTrigger value="plants">工厂 {plants.length}</TabsTrigger>
        <TabsTrigger value="lines">产线 {lines.length}</TabsTrigger>
        <TabsTrigger value="devices">设备 {devices.length}</TabsTrigger>
        <TabsTrigger value="tags">测点 {tags.length}</TabsTrigger>
      </TabsList>
      <TabsContent value="plants" className="space-y-4">
        <div className="flex justify-end">
          <CreateAssetDialog
            kind="plant"
            disabled={!isAdmin}
            onCreate={(v) =>
              PlantsService.createPlant({
                body: {
                  code: v.code,
                  name: v.name,
                  location: v.detail || null,
                },
              })
            }
          />
        </div>
        <AssetTable
          kind="plant"
          rows={plants}
          canWrite={isAdmin}
          onSave={(row, name, detail) =>
            PlantsService.updatePlant({
              path: { plant_id: row.id },
              body: { name, location: detail || null },
            })
          }
          onToggle={(row, nextActive) =>
            nextActive
              ? PlantsService.updatePlant({
                  path: { plant_id: row.id },
                  body: { status: "active" },
                })
              : PlantsService.disablePlant({ path: { plant_id: row.id } })
          }
        />
      </TabsContent>
      <TabsContent value="lines" className="space-y-4">
        <div className="flex justify-end">
          <CreateAssetDialog
            kind="line"
            disabled={
              !canManageNested ||
              !plants.some((plant) => isAssetActive("plant", plant))
            }
            parentOptions={plants
              .filter((p) => isAssetActive("plant", p))
              .map((p) => ({
                value: p.id,
                label: `${p.code} · ${p.name}`,
              }))}
            onCreate={(v) =>
              ProductionLinesService.linesCreateProductionLine({
                body: {
                  code: v.code,
                  name: v.name,
                  plant_id: v.parentId,
                  process_type: v.detail,
                },
              })
            }
          />
        </div>
        <AssetTable
          kind="line"
          rows={lines}
          canWrite={canManageNested}
          parentName={(row) =>
            "plant_id" in row
              ? (plantNames.get(row.plant_id) ?? "未知工厂")
              : ""
          }
          onSave={(row, name, detail) =>
            ProductionLinesService.linesUpdateProductionLine({
              path: { production_line_id: row.id },
              body: { name, process_type: detail },
            })
          }
          onToggle={(row, nextActive) =>
            nextActive
              ? ProductionLinesService.linesUpdateProductionLine({
                  path: { production_line_id: row.id },
                  body: { status: "active" },
                })
              : ProductionLinesService.linesDisableProductionLine({
                  path: { production_line_id: row.id },
                })
          }
        />
      </TabsContent>
      <TabsContent value="devices" className="space-y-4">
        <div className="flex justify-end">
          <CreateAssetDialog
            kind="device"
            disabled={
              !canManageNested ||
              !lines.some((line) => isAssetActive("line", line))
            }
            parentOptions={lines
              .filter((line) => isAssetActive("line", line))
              .map((line) => ({
                value: line.id,
                label: `${line.code} · ${line.name}`,
              }))}
            onCreate={(v) =>
              DevicesService.createDevice({
                body: {
                  code: v.code,
                  name: v.name,
                  production_line_id: v.parentId,
                  device_type: v.detail,
                },
              })
            }
          />
        </div>
        <AssetTable
          kind="device"
          rows={devices}
          canWrite={canManageNested}
          parentName={(row) =>
            "production_line_id" in row
              ? (lineNames.get(row.production_line_id) ?? "未知产线")
              : ""
          }
          onSave={(row, name, detail) =>
            DevicesService.updateDevice({
              path: { device_id: row.id },
              body: { name, device_type: detail },
            })
          }
          onToggle={(row, nextActive) =>
            nextActive
              ? DevicesService.updateDevice({
                  path: { device_id: row.id },
                  body: { status: "active" },
                })
              : DevicesService.disableDevice({ path: { device_id: row.id } })
          }
        />
      </TabsContent>
      <TabsContent value="tags" className="space-y-4">
        <div className="flex justify-end">
          <CreateAssetDialog
            kind="tag"
            disabled={
              !canManageNested ||
              !devices.some((device) => isAssetActive("device", device))
            }
            parentOptions={devices
              .filter((device) => isAssetActive("device", device))
              .map((device) => ({
                value: device.id,
                label: `${device.code} · ${device.name}`,
              }))}
            onCreate={(v) =>
              TagsService.createTag({
                body: {
                  code: v.code,
                  name: v.name,
                  device_id: v.parentId,
                  data_type: v.dataType,
                  unit: v.unit || null,
                  min_value: v.minValue ? Number(v.minValue) : null,
                  max_value: v.maxValue ? Number(v.maxValue) : null,
                  sampling_interval_ms: Number(v.samplingInterval),
                },
              })
            }
          />
        </div>
        <AssetTable
          kind="tag"
          rows={tags}
          canWrite={canManageNested}
          parentName={(row) =>
            "device_id" in row
              ? (deviceNames.get(row.device_id) ?? "未知设备")
              : ""
          }
          onSave={(row, name, detail) =>
            TagsService.updateTag({
              path: { tag_id: row.id },
              body: { name, unit: detail || null },
            })
          }
          onToggle={(row, nextActive) =>
            nextActive
              ? TagsService.updateTag({
                  path: { tag_id: row.id },
                  body: { is_enabled: true },
                })
              : TagsService.disableTag({ path: { tag_id: row.id } })
          }
        />
      </TabsContent>
    </Tabs>
  )
}
