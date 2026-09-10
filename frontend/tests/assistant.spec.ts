import { expect, type Page, test } from "@playwright/test"

async function setupPlant(page: Page) {
  await page.goto("/assistant")
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const code = `AI_UI_${Date.now()}_${Math.floor(Math.random() * 10000)}`
  const response = await page.request.post("/api/v1/plants/", {
    headers: { Authorization: `Bearer ${token}` },
    data: { code, name: `AI界面测试 ${code}`, location: "合成数据测试" },
  })
  expect(response.ok()).toBeTruthy()
  const plant = await response.json()
  await page.reload()
  await page.getByLabel("查询范围").click()
  await page.getByRole("option", { name: plant.name, exact: true }).click()
  return plant
}

test("AI assistant clearly separates missing provider and local search", async ({
  page,
}) => {
  await page.route("**/api/v1/assistant/status", (route) =>
    route.fulfill({
      json: {
        configured: false,
        model: "test",
        embedding_configured: false,
        read_only: true,
      },
    }),
  )
  await setupPlant(page)
  await expect(
    page.getByRole("heading", { name: "AI 数据治理助手" }),
  ).toBeVisible()
  await page.getByLabel("你想了解什么？").fill("质量码说明")
  await expect(page.getByRole("button", { name: "向 AI 提问" })).toBeDisabled()
  await expect(page.getByRole("button", { name: "仅本地检索" })).toBeEnabled()
  await page.getByRole("button", { name: "仅本地检索" }).click()
  await expect(
    page.getByText("没有找到足够相关的文档，请补充资料或换一种问法。"),
  ).toBeVisible()
})

test("Knowledge create edit search and delete invalidate old content", async ({
  page,
}) => {
  await setupPlant(page)
  await page.getByLabel("标题", { exact: true }).fill("质量码操作说明")
  await page
    .getByLabel("正文（10—30,000 字符）")
    .fill("质量码为Bad时不能将样本用作正常的工艺数据。")
  await page.getByRole("button", { name: "保存文档" }).click()
  await expect(page.getByText("文档已保存，后续查询使用新版本。")).toBeVisible()
  await page.getByLabel("你想了解什么？").fill("质量码")
  await page.getByRole("button", { name: "仅本地检索" }).click()
  await expect(page.getByText("本地检索结果 · 1 条")).toBeVisible()
  await page.getByRole("button", { name: "编辑", exact: true }).click()
  await page.getByLabel("标题", { exact: true }).fill("设备编码说明")
  await page
    .getByLabel("正文（10—30,000 字符）")
    .fill("设备编码必须使用英文大写字母、数字和下划线。")
  await page.getByRole("button", { name: "保存文档" }).click()
  await expect(page.getByText("版本 2 · 词法检索")).toBeVisible()
  await page.getByRole("button", { name: "仅本地检索" }).click()
  await expect(page.getByText("本地检索结果 · 0 条")).toBeVisible()
  page.once("dialog", (dialog) => dialog.accept())
  await page.getByRole("button", { name: "删除", exact: true }).click()
  await expect(
    page.getByText("暂无文档。工程师或管理员可添加操作说明。"),
  ).toBeVisible()
})

test("AI answer shows evidence and errors without rendering injected HTML", async ({
  page,
}) => {
  await page.route("**/api/v1/assistant/status", (route) =>
    route.fulfill({
      json: {
        configured: true,
        model: "ui-test-stub",
        embedding_configured: false,
        read_only: true,
      },
    }),
  )
  await page.route("**/api/v1/assistant/ask", (route) =>
    route.fulfill({
      json: {
        status: "answered",
        answer: "没有导入批次。<img src=x onerror=alert(1)>",
        citation_ids: ["tool:test:1"],
        evidence: [
          {
            id: "capability:test:trend",
            kind: "capability",
            title: "趋势查询能力",
            data: {
              trend_tool_blocked: true,
              reason: "不支持30天逐小时均值，不改查24小时。",
            },
          },
          {
            id: "tool:test:1",
            kind: "tool",
            title: "批次查询",
            data: { batches: [] },
          },
        ],
        run_id: "test",
        model: "ui-test-stub",
        retrieval_mode: "lexical",
        prompt_tokens: 12,
        completion_tokens: 8,
        duration_ms: 200,
        generated_at: new Date().toISOString(),
      },
    }),
  )
  await setupPlant(page)
  await page.getByLabel("你想了解什么？").fill("最近有哪些导入批次？")
  await page.getByRole("checkbox").first().check()
  await page.getByRole("button", { name: "向 AI 提问" }).click()
  await expect(
    page.getByText("没有导入批次。<img src=x onerror=alert(1)>"),
  ).toBeVisible()
  await expect(page.locator('img[src="x"]')).toHaveCount(0)
  await expect(page.getByText("查询证据 · 批次查询").first()).toBeVisible()
  await page.getByText("查看全部查询与检索证据（2）").click()
  await expect(page.getByText("能力边界 · 趋势查询能力")).toBeVisible()
  await page.screenshot({
    path: "test-results/assistant-desktop.png",
    fullPage: true,
  })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({
    path: "test-results/assistant-mobile.png",
    fullPage: true,
  })
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.route("**/api/v1/assistant/ask", (route) =>
    route.fulfill({
      status: 502,
      json: { detail: "模型超时，未执行业务写入" },
    }),
  )
  await page.getByRole("button", { name: "向 AI 提问" }).click()
  await expect(page.getByRole("alert")).toContainText("模型超时")
})
