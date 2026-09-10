import { expect, test } from "@playwright/test"

// Explicit paid acceptance only. Normal local runs and CI never call a provider.
test.describe("Real provider acceptance on synthetic data", () => {
  test.describe.configure({ retries: 0 })
  test.skip(
    process.env.AI_LIVE_ACCEPTANCE !== "1",
    "Requires explicit paid opt-in",
  )

  test("Real document answer and read-only tool reach the browser", async ({
    page,
  }) => {
    test.setTimeout(120_000)
    await page.goto("/assistant")
    const token = await page.evaluate(() =>
      localStorage.getItem("access_token"),
    )
    const code = `AI_LIVE_${Date.now()}`
    const created = await page.request.post("/api/v1/plants/", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        code,
        name: `AI真实联调合成工厂 ${code}`,
        location: "合成验收数据",
      },
    })
    expect(created.ok()).toBeTruthy()
    const plant = await created.json()
    await page.reload()
    await page.getByLabel("查询范围").click()
    await page.getByRole("option", { name: plant.name, exact: true }).click()
    await page.getByLabel("标题", { exact: true }).fill("合成质量码验收说明")
    await page
      .getByLabel("正文（10—30,000 字符）")
      .fill(
        "本说明仅用于合成数据验收。Good表示正常采样质量，Bad表示坏质量。坏质量不能作为正常工艺值；必须核对时间戳，不能因为页面有值就认为仍然新鲜。",
      )
    if (process.env.AI_LIVE_EMBEDDING === "1") {
      await page.getByRole("checkbox").last().check()
    }
    await page.getByRole("button", { name: "保存文档" }).click()
    await expect(
      page.getByText("文档已保存，后续查询使用新版本。"),
    ).toBeVisible()
    await page.getByRole("checkbox").first().check()

    for (const [question, kind] of [
      ["根据合成质量码验收说明，Bad质量码能作为正常工艺值吗？", "document"],
      ["请查询当前工厂最近三个导入批次。", "tool"],
    ]) {
      await page.getByLabel("你想了解什么？").fill(question)
      const pending = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/assistant/ask") &&
          response.request().method() === "POST",
        { timeout: 90_000 },
      )
      await page.getByRole("button", { name: "向 AI 提问" }).click()
      const response = await pending
      expect(response.ok()).toBeTruthy()
      const answer = await response.json()
      expect(answer.model).not.toMatch(/stub|test/i)
      expect(answer.prompt_tokens).toBeGreaterThan(0)
      if (kind === "document" && process.env.AI_LIVE_EMBEDDING === "1") {
        expect(answer.retrieval_mode).toBe("hybrid")
      }
      expect(
        answer.evidence.some((item: { kind: string }) => item.kind === kind),
      ).toBeTruthy()
      await expect(page.getByText(answer.answer, { exact: true })).toBeVisible()
      if (kind === "tool") {
        expect(
          answer.evidence.some(
            (item: { title: string; data: { batches?: unknown[] } }) =>
              item.title === "list_import_batches" &&
              item.data.batches?.length === 0,
          ),
        ).toBeTruthy()
      }
    }
    if (process.env.AI_LIVE_SCREENSHOT) {
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.screenshot({
        path: process.env.AI_LIVE_SCREENSHOT,
        fullPage: true,
      })
    }
    // Keep the clearly named synthetic scope and its audit as acceptance evidence.
    // No pre-existing business scope or documents are sent to the provider.
  })
})
