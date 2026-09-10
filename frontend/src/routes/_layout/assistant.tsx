import { createFileRoute } from "@tanstack/react-router"
import AssistantManager from "@/components/Assistant/AssistantManager"

export const Route = createFileRoute("/_layout/assistant")({
  component: AssistantManager,
  head: () => ({ meta: [{ title: "AI 数据助手 - 工业数据运维平台" }] }),
})
