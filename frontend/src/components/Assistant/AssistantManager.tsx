import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { AxiosError } from "axios"
import { BookOpen, Bot, FileSearch, Loader2, ShieldCheck } from "lucide-react"
import { type FormEvent, useEffect, useState } from "react"
import {
  AiAssistantService,
  type Answer,
  type DocumentPublic,
  type Evidence,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { plantListOptions } from "@/hooks/plantQueries"
import useAuth from "@/hooks/useAuth"

const textAreaClass =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm leading-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
function errorMessage(error: unknown) {
  if (error instanceof AxiosError) {
    const detail = error.response?.data?.detail
    if (typeof detail === "string") return detail
    if (error.response?.status === 422)
      return "输入未通过校验，请检查必填项、长度和格式。"
    if (error.response?.status === 401) return "登录已失效，请重新登录。"
    if (error.response?.status === 403)
      return "没有当前操作或工厂的访问权限，请联系管理员。"
    if (error.response?.status === 429)
      return "请求过于频繁，请等待一分钟后重试。"
    if (!error.response)
      return "无法连接本地服务，请检查项目是否运行；这不代表模型回答失败。"
  }
  return "请求未完成，请检查网络或输入后重试。"
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  return (
    <div className="space-y-2">
      {evidence.map((item) => (
        <details key={item.id} className="rounded-lg border p-3">
          <summary className="cursor-pointer break-words text-sm font-medium">
            {item.kind === "document"
              ? "文档依据"
              : item.kind === "capability"
                ? "能力边界"
                : "查询证据"}{" "}
            ·{" "}
            {(
              {
                find_tags: "测点查找",
                latest_value: "最新采样",
                list_import_batches: "导入批次",
                inspect_import_batch: "批次问题",
                asset_overview: "资产分配",
                acquisition_status: "采集状态",
                tag_trend: "趋势统计",
              } as Record<string, string>
            )[item.title] ?? item.title}
          </summary>
          {item.kind === "tool" && item.title === "latest_value" && (
            <div className="my-3 rounded bg-muted p-3 text-sm leading-6">
              <p>
                测点：{String(item.data.name)}（{String(item.data.code)}）
              </p>
              {item.data.sample ? (
                <>
                  <p>
                    数值：
                    {String(
                      (item.data.sample as Record<string, unknown>).value,
                    )}{" "}
                    {String(item.data.unit ?? "")}
                  </p>
                  <p>
                    来源：
                    {(item.data.sample as Record<string, unknown>).source ===
                    "file"
                      ? "历史文件导入"
                      : "OPC UA 采集"}
                  </p>
                  <p>
                    采样时间：
                    {String(
                      (item.data.sample as Record<string, unknown>)
                        .source_timestamp,
                    )}
                  </p>
                  <p>
                    质量码：
                    {String(
                      (item.data.sample as Record<string, unknown>).status_code,
                    )}{" "}
                    ·{" "}
                    {(item.data.sample as Record<string, unknown>).stale
                      ? "已过期"
                      : "未超过演示过期阈值"}
                  </p>
                </>
              ) : (
                <p>指定来源没有样本；不代表值为零。</p>
              )}
              <p className="text-xs text-muted-foreground">
                状态截至本次查询，并非自动刷新的监控画面；Good不等于工艺合格。
              </p>
            </div>
          )}
          {item.kind === "tool" && item.title === "inspect_import_batch" && (
            <div className="my-3 rounded bg-muted p-3 text-sm leading-6">
              <p>文件：{String(item.data.filename)}</p>
              <p>
                总行数 {String(item.data.total_rows)} · 接受{" "}
                {String(item.data.accepted_rows)} · 拒绝{" "}
                {String(item.data.rejected_rows)}
              </p>
              <p>
                重复行 {String(item.data.duplicate_rows)} · 警告行{" "}
                {String(item.data.warning_rows)} · 问题条数{" "}
                {String(item.data.issue_count)}
              </p>
              <p className="text-xs text-muted-foreground">
                重复行包含在拒绝行中；警告行可能已入库。不能把这些数字直接相加。
              </p>
              {Boolean(item.data.issues_truncated) && (
                <p className="text-destructive">
                  问题记录已截断，以下摘要不是全量问题分布。
                </p>
              )}
              <a className="underline" href="/data-governance">
                进入数据治理核对批次与导出问题明细
              </a>
            </div>
          )}
          <p className="my-2 break-all text-xs text-muted-foreground">
            {item.id}
          </p>
          <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-3 text-xs leading-5">
            {item.kind === "document"
              ? String(item.data.text ?? "")
              : JSON.stringify(item.data, null, 2)}
          </pre>
          {item.kind === "document" && (
            <p className="mt-2 text-xs text-muted-foreground">
              版本 {String(item.data.version)} · 字符范围{" "}
              {String(item.data.start)}—{String(item.data.end)}
            </p>
          )}
        </details>
      ))}
    </div>
  )
}

function PlantAssistant({
  plantId,
  configured,
}: {
  plantId: string
  configured: boolean
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const canWrite = Boolean(
    user?.is_superuser || user?.role === "admin" || user?.role === "engineer",
  )
  const [question, setQuestion] = useState("")
  const [consent, setConsent] = useState(false)
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [searchResults, setSearchResults] = useState<Evidence[] | null>(null)
  const [editing, setEditing] = useState<DocumentPublic | null>(null)
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [embedConsent, setEmbedConsent] = useState(false)
  const [notice, setNotice] = useState("")
  const docs = useQuery({
    queryKey: ["assistant-docs", plantId],
    queryFn: async () =>
      (
        await AiAssistantService.assistantListDocuments({
          query: { plant_id: plantId },
        })
      ).data ?? [],
  })
  const runs = useQuery({
    queryKey: ["assistant-runs", plantId],
    queryFn: async () =>
      (
        await AiAssistantService.assistantReadRuns({
          query: { plant_id: plantId },
        })
      ).data ?? [],
  })
  const ask = useMutation({
    mutationFn: async () =>
      (
        await AiAssistantService.assistantAsk({
          body: {
            plant_id: plantId,
            question,
            allow_external_processing: consent,
          },
        })
      ).data,
    onMutate: () => {
      setAnswer(null)
      setSearchResults(null)
    },
    onSuccess: (result) => setAnswer(result ?? null),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["assistant-runs", plantId] }),
    retry: false,
  })
  const search = useMutation({
    mutationFn: async () =>
      (
        await AiAssistantService.assistantSearchDocuments({
          query: { plant_id: plantId, query: question },
        })
      ).data,
    onSuccess: (result) => {
      setSearchResults(result ?? [])
      setAnswer(null)
    },
  })
  const save = useMutation({
    mutationFn: async () => {
      const body = {
        title,
        content,
        expected_version: editing?.version,
        allow_external_processing: embedConsent,
      }
      if (editing)
        return AiAssistantService.assistantUpdateDocument({
          path: { document_id: editing.id },
          query: { plant_id: plantId },
          body,
        })
      return AiAssistantService.assistantCreateDocument({
        query: { plant_id: plantId },
        body,
      })
    },
    onSuccess: () => {
      setTitle("")
      setContent("")
      setEditing(null)
      setNotice("文档已保存，后续查询使用新版本。")
      setAnswer(null)
      setSearchResults(null)
      queryClient.invalidateQueries({ queryKey: ["assistant-docs", plantId] })
    },
  })
  const remove = useMutation({
    mutationFn: (doc: DocumentPublic) =>
      AiAssistantService.assistantDeleteDocument({
        path: { document_id: doc.id },
        query: { plant_id: plantId, expected_version: doc.version },
      }),
    onSuccess: () => {
      setNotice("文档及分块已删除，不再用于后续检索。")
      setAnswer(null)
      setSearchResults(null)
      queryClient.invalidateQueries({ queryKey: ["assistant-docs", plantId] })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    ask.mutate()
  }
  return (
    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="min-w-0 space-y-6">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Bot className="size-5" /> 有依据地查询与解释
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="mb-4 rounded-lg bg-muted/50 p-3 text-sm leading-6">
              当前工厂有 {docs.data?.length ?? "…"}{" "}
              篇知识文档。可查询资产分配、采集状态、测点值、最近24小时内的有限样本统计和导入问题；不搜索互联网。追问时请带上测点编码或批次信息。
              {docs.data?.length === 0 &&
                " 当前范围没有操作文档，规则类问题可能无依据；请添加文档，或选择已有知识的教学工厂。业务数据查询仍可用。"}
            </p>
            <form onSubmit={submit} className="space-y-4">
              <Label htmlFor="assistant-question">你想了解什么？</Label>
              <textarea
                id="assistant-question"
                className={textAreaClass}
                rows={4}
                maxLength={2000}
                minLength={2}
                required
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="例如：最近导入了哪些批次？质量码异常该如何处理？"
                disabled={ask.isPending}
              />
              <div className="flex flex-wrap gap-2">
                {[
                  "当前工厂有哪些测点？",
                  "最近的导入批次有哪些？",
                  "质量码异常该如何处理？",
                  "当前工厂各设备的测点如何分配？",
                  "采集任务现在是否连接，最后心跳是什么时候？",
                ].map((example) => (
                  <Button
                    key={example}
                    size="sm"
                    variant="outline"
                    type="button"
                    disabled={ask.isPending}
                    onClick={() => setQuestion(example)}
                  >
                    {example}
                  </Button>
                ))}
              </div>
              <label className="flex items-start gap-2 rounded-lg bg-muted/50 p-3 text-sm leading-6">
                <input
                  type="checkbox"
                  className="mt-1.5"
                  checked={consent}
                  onChange={(event) => setConsent(event.target.checked)}
                />
                我确认本次问题及相关工厂数据、文档可发送至配置的外部模型服务，仅使用获准的模拟或脱敏资料。
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="submit"
                  disabled={
                    !configured ||
                    !consent ||
                    question.trim().length < 2 ||
                    ask.isPending
                  }
                >
                  {ask.isPending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Bot className="size-4" />
                  )}
                  {ask.isPending ? "正在检索与查询…" : "向 AI 提问"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    question.trim().length < 2 ||
                    search.isPending ||
                    ask.isPending
                  }
                  onClick={() => search.mutate()}
                >
                  <FileSearch className="size-4" />
                  仅本地检索
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                仅本地检索不调用外部模型、不产生模型费用。AI
                回答不代表工艺诊断结论，不会修改数据或控制设备。
              </p>
            </form>
            {(ask.error || search.error) && (
              <p
                role="alert"
                className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm"
              >
                {errorMessage(ask.error || search.error)}
              </p>
            )}
          </CardContent>
        </Card>
        {answer && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                回答结果
                <Badge variant="outline">
                  {
                    {
                      answered: "有依据回答",
                      no_answer: "依据不足",
                      clarification: "需要澄清",
                    }[answer.status]
                  }
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="whitespace-pre-wrap break-words leading-7">
                {answer.answer}
              </p>
              <p className="text-xs text-muted-foreground">
                {answer.model} ·{" "}
                {answer.retrieval_mode === "hybrid"
                  ? "混合检索"
                  : answer.retrieval_mode === "lexical_embedding_unavailable"
                    ? "向量服务暂不可用，已退回本地检索"
                    : "词法检索"}{" "}
                · {(answer.duration_ms / 1000).toFixed(1)} 秒 · 输入{" "}
                {answer.prompt_tokens} / 输出 {answer.completion_tokens} Token
              </p>
              <p className="text-xs text-muted-foreground">
                回答生成于{" "}
                {new Date(answer.generated_at).toLocaleString("zh-CN")}
                ，不是持续刷新的实时监控。
              </p>
              <EvidenceList
                evidence={answer.evidence.filter((item) =>
                  (answer.citation_ids ?? []).includes(item.id),
                )}
              />
              <details>
                <summary className="cursor-pointer text-sm text-muted-foreground">
                  查看全部查询与检索证据（{answer.evidence.length}）
                </summary>
                <div className="mt-3">
                  <EvidenceList evidence={answer.evidence} />
                </div>
              </details>
            </CardContent>
          </Card>
        )}
        {searchResults && (
          <Card>
            <CardHeader>
              <CardTitle>本地检索结果 · {searchResults.length} 条</CardTitle>
            </CardHeader>
            <CardContent>
              {searchResults.length ? (
                <EvidenceList evidence={searchResults} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  没有找到足够相关的文档，请补充资料或换一种问法。
                </p>
              )}
            </CardContent>
          </Card>
        )}
        <Card>
          <CardHeader>
            <CardTitle>我的最近调用</CardTitle>
          </CardHeader>
          <CardContent>
            {runs.isError && <p role="alert">{errorMessage(runs.error)}</p>}
            <p className="mb-3 text-xs text-muted-foreground">
              只记录状态、耗时、用量和证据编号，不保存问题原文。费用以模型服务账单为准。
            </p>
            <div className="space-y-2">
              {runs.data?.slice(0, 5).map((run) => (
                <div
                  key={run.id}
                  className="flex flex-wrap justify-between gap-2 border-b py-2 text-xs"
                >
                  <span>
                    {run.created_at
                      ? new Date(run.created_at).toLocaleString()
                      : "—"}
                  </span>
                  <span>
                    {(
                      {
                        answered: "已回答",
                        no_answer: "依据不足",
                        clarification: "待澄清",
                        error: "失败",
                        running: "进行中",
                      } as Record<string, string>
                    )[run.status ?? "running"] ?? run.status}{" "}
                    · {run.duration_ms} 毫秒 ·{" "}
                    {(run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0)}{" "}
                    Token
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
      <Card className="min-w-0">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BookOpen className="size-5" /> 当前工厂知识库
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <p className="text-sm leading-6 text-muted-foreground">
            知识文档用于解释规则和操作方法。测点实时值通过受控业务查询获取，不从文档推测。
          </p>
          {docs.isError && <p role="alert">{errorMessage(docs.error)}</p>}
          <section
            aria-label="知识文档列表"
            tabIndex={docs.data?.length ? 0 : undefined}
            className="max-h-[32rem] space-y-2 overflow-y-auto overscroll-contain pr-2"
          >
            {docs.data?.map((doc) => (
              <div key={doc.id} className="rounded-lg border p-3">
                <p className="break-words text-sm font-medium">{doc.title}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  版本 {doc.version} ·{" "}
                  {doc.embedding_model ? "已建向量" : "词法检索"}
                </p>
                <details className="mt-2 text-sm">
                  <summary className="cursor-pointer">查看内容</summary>
                  <p className="mt-2 max-h-60 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">
                    {doc.content}
                  </p>
                </details>
                {canWrite && (
                  <div className="mt-2 flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditing(doc)
                        setTitle(doc.title)
                        setContent(doc.content)
                        setNotice("")
                      }}
                    >
                      编辑
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={remove.isPending || save.isPending}
                      onClick={() => {
                        if (
                          window.confirm(
                            `删除“${doc.title}”及其分块？删除后无法撤销，请先备份原文。`,
                          )
                        )
                          remove.mutate(doc)
                      }}
                    >
                      删除
                    </Button>
                  </div>
                )}
              </div>
            ))}
            {docs.data?.length === 0 && (
              <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
                暂无文档。工程师或管理员可添加操作说明。
              </p>
            )}
          </section>
          {canWrite && (
            <form
              className="space-y-3 border-t pt-4"
              onSubmit={(event) => {
                event.preventDefault()
                setNotice("")
                save.mutate()
              }}
            >
              <h3 className="font-medium">
                {editing
                  ? `编辑文档 · 当前版本 ${editing.version}`
                  : "添加操作文档"}
              </h3>
              <Label htmlFor="document-file">
                读取本地文本（.md / .txt，不会自动发送）
              </Label>
              <Input
                id="document-file"
                type="file"
                accept=".md,.txt"
                onChange={async (event) => {
                  const file = event.target.files?.[0]
                  if (!file) return
                  if (file.size > 120000 || !/\.(md|txt)$/i.test(file.name)) {
                    setNotice("只支持不超过 120 KB 的 Markdown 或文本文件。")
                    return
                  }
                  const text = await file.text()
                  if (text.length > 30000) {
                    setNotice("文档最多 30,000 字符，请拆分后再添加。")
                    return
                  }
                  setTitle(file.name)
                  setContent(text)
                }}
              />
              <Label htmlFor="document-title">标题</Label>
              <Input
                id="document-title"
                required
                maxLength={200}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
              />
              <Label htmlFor="document-content">正文（10—30,000 字符）</Label>
              <textarea
                id="document-content"
                required
                minLength={10}
                maxLength={30000}
                rows={8}
                className={textAreaClass}
                value={content}
                onChange={(event) => setContent(event.target.value)}
              />
              <label className="flex items-start gap-2 text-xs leading-5 text-muted-foreground">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={embedConsent}
                  onChange={(event) => setEmbedConsent(event.target.checked)}
                />
                若已配置向量服务，我确认可将本文发送至该服务建立向量。未勾选时仅本地保存。
              </label>
              <div className="flex gap-2">
                <Button type="submit" disabled={save.isPending}>
                  {save.isPending ? "保存中…" : "保存文档"}
                </Button>
                {editing && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setEditing(null)
                      setTitle("")
                      setContent("")
                    }}
                  >
                    取消编辑
                  </Button>
                )}
              </div>
            </form>
          )}
          {notice && (
            <p role="status" className="text-sm text-muted-foreground">
              {notice}
            </p>
          )}
          {(save.error || remove.error) && (
            <p role="alert" className="text-sm text-destructive">
              {errorMessage(save.error || remove.error)}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export default function AssistantManager() {
  const [plantId, setPlantId] = useState("")
  const plants = useQuery({
    ...plantListOptions(),
    select: (page) => page?.data ?? [],
  })
  const status = useQuery({
    queryKey: ["assistant-status"],
    queryFn: async () =>
      (await AiAssistantService.assistantAssistantStatus()).data,
  })
  useEffect(() => {
    if (!plantId && plants.data?.[0]) setPlantId(plants.data[0].id)
  }, [plantId, plants.data])
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">AI 数据治理助手</h1>
          <p className="mt-2 text-muted-foreground">
            查询业务事实、查阅操作说明，每条回答保留证据。
          </p>
        </div>
        <Badge variant="outline" className="gap-1.5 py-2">
          <ShieldCheck className="size-4" />
          只读工具 · 工厂权限隔离
        </Badge>
      </div>
      <div className="flex flex-wrap items-center gap-4 rounded-xl border bg-muted/30 p-4">
        <div className="space-y-2">
          <Label htmlFor="assistant-plant">查询范围</Label>
          <Select value={plantId} onValueChange={setPlantId}>
            <SelectTrigger id="assistant-plant" className="w-64">
              <SelectValue placeholder="选择已授权工厂" />
            </SelectTrigger>
            <SelectContent>
              {plants.data?.map((plant) => (
                <SelectItem key={plant.id} value={plant.id}>
                  {plant.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="text-sm leading-6 text-muted-foreground">
          {status.isPending
            ? "正在检查模型配置…"
            : status.data?.configured
              ? `模型已配置：${status.data.model}（不代表连接已验证）`
              : "尚未配置 DeepSeek：仍可管理知识和执行本地检索"}
          <br />
          {status.data?.embedding_configured
            ? "语义向量服务已配置；未建立向量的文档仍使用词法检索"
            : "当前使用本地词法检索；未启用语义向量服务"}
        </div>
      </div>
      {(plants.isError || status.isError) && (
        <p role="alert">{errorMessage(plants.error || status.error)}</p>
      )}
      {plantId ? (
        <PlantAssistant
          key={plantId}
          plantId={plantId}
          configured={Boolean(status.data?.configured)}
        />
      ) : (
        <p className="text-muted-foreground">
          暂无可访问工厂，请先创建资产或联系管理员授权。
        </p>
      )}
    </div>
  )
}
