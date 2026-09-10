import { writeFileSync } from "node:fs"
import { expect, test } from "@playwright/test"

test("real business answers reach the deployed browser", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.AI_LIVE_ACCEPTANCE !== "1",
    "Explicit paid synthetic acceptance only",
  )
  test.setTimeout(150_000)
  await page.goto("/")
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const suffix = Date.now()
  const plantResponse = await request.post("/api/v1/plants/", {
    headers,
    data: {
      code: `AI_BUSINESS_${suffix}`,
      name: `AI业务验收演示工厂 ${suffix}`,
      location: "合成演示，不是生产数据",
    },
  })
  expect(plantResponse.ok()).toBeTruthy()
  const plant = await plantResponse.json()
  const lineResponse = await request.post("/api/v1/production-lines/", {
    headers,
    data: {
      plant_id: plant.id,
      code: "DEMO",
      name: "演示产线",
      process_type: "continuous",
    },
  })
  expect(lineResponse.ok()).toBeTruthy()
  const line = await lineResponse.json()
  const deviceResponse = await request.post("/api/v1/devices/", {
    headers,
    data: {
      production_line_id: line.id,
      code: "DEMO",
      name: "演示设备",
      device_type: "simulator",
    },
  })
  expect(deviceResponse.ok()).toBeTruthy()
  const device = await deviceResponse.json()
  const tagResponse = await request.post("/api/v1/tags/", {
    headers,
    data: {
      device_id: device.id,
      code: "DEMO_TEMP",
      name: "演示温度",
      unit: "℃",
      data_type: "float",
      min_value: 0,
      max_value: 1000,
    },
  })
  expect(tagResponse.ok()).toBeTruthy()
  const previewResponse = await request.post(
    `/api/v1/imports/preview?plant_id=${plant.id}`,
    {
      headers,
      multipart: {
        file: {
          name: "ai-business-demo.csv",
          mimeType: "text/csv",
          buffer: Buffer.from(
            [
              "时间,测点编码,值,质量码",
              "2026-09-01T00:00:00Z,DEMO_TEMP,100,Good",
              "2026-09-01T00:00:01Z,DEMO_TEMP,101,Bad",
              "2026-09-01T00:00:02Z,DEMO_TEMP,2000,Good",
            ].join("\n"),
          ),
        },
      },
    },
  )
  expect(previewResponse.ok()).toBeTruthy()
  const preview = await previewResponse.json()
  const processedResponse = await request.post(
    `/api/v1/imports/${preview.batch.id}/process`,
    { headers, data: preview.suggested_mapping },
  )
  expect(processedResponse.ok()).toBeTruthy()
  let batch = await processedResponse.json()
  await expect
    .poll(
      async () => {
        batch = await (
          await request.get(`/api/v1/imports/${preview.batch.id}`, { headers })
        ).json()
        return batch.status
      },
      { timeout: 20_000 },
    )
    .toBe("completed")
  expect([
    batch.total_rows,
    batch.accepted_rows,
    batch.rejected_rows,
    batch.issue_count,
  ]).toEqual([3, 2, 1, 2])

  await page.getByRole("link", { name: "数据治理", exact: true }).click()
  await page.getByLabel("所属工厂").selectOption(plant.id)
  await page.getByRole("link", { name: "AI 数据助手", exact: true }).click()
  await page.getByLabel("查询范围").click()
  await page.getByRole("option", { name: plant.name, exact: true }).click()
  await page.getByLabel("标题", { exact: true }).fill("合成业务验收规程")
  await page
    .getByLabel("正文（10—30,000 字符）")
    .fill(
      "仅供本软件合成演示，不是生产标准：人工复核等待时间为95秒。质量码Bad不能代表正常工艺值，应先检查采集质量。",
    )
  await page.getByRole("checkbox").last().check()
  await page.getByRole("button", { name: "保存文档" }).click()
  await expect(page.getByText("文档已保存，后续查询使用新版本。")).toBeVisible()
  await page.getByRole("checkbox").first().check()
  const records = []
  for (const [question, expectedText] of [
    ["DEMO_TEMP历史文件导入中最新一条的值和质量是什么？", "101"],
    [
      "最近一批导入有多少成功、失败和警告？请查看问题明细并给出排查建议。",
      "排查",
    ],
    ["根据合成业务验收规程，人工复核要等待多少秒？", "95"],
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
    expect(answer.status).toBe("answered")
    expect(answer.answer).toContain(expectedText)
    expect(answer.citation_ids.length).toBeGreaterThan(0)
    await expect(page.getByText(answer.answer, { exact: true })).toBeVisible()
    records.push({ question, answer })
  }
  if (process.env.AI_BUSINESS_UI_REPORT)
    writeFileSync(
      process.env.AI_BUSINESS_UI_REPORT,
      JSON.stringify({ plant, batch, records }, null, 2),
      { flag: "wx" },
    )
  if (process.env.AI_LIVE_SCREENSHOT)
    await page.screenshot({
      path: process.env.AI_LIVE_SCREENSHOT,
      fullPage: true,
    })
  // Keep this clearly named synthetic scope for the user's manual acceptance.
})
