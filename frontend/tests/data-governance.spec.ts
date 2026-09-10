import { expect, test } from "@playwright/test"

test("administrator can preview, validate and import a CSV batch", async ({
  page,
  request,
}) => {
  await page.goto("/")
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const suffix = Date.now().toString().slice(-8)
  const plantResponse = await request.post("/api/v1/plants/", {
    headers,
    data: {
      code: `P_E2E_${suffix}`,
      name: "数据治理测试工厂",
      location: "隔离测试环境",
    },
  })
  expect(plantResponse.ok()).toBeTruthy()
  const plant = await plantResponse.json()
  const lineResponse = await request.post("/api/v1/production-lines/", {
    headers,
    data: {
      plant_id: plant.id,
      code: `L_E2E_${suffix}`,
      name: "数据治理测试产线",
      process_type: "continuous",
    },
  })
  const line = await lineResponse.json()
  const deviceCode = `D_E2E_${suffix}`
  const deviceResponse = await request.post("/api/v1/devices/", {
    headers,
    data: {
      production_line_id: line.id,
      code: deviceCode,
      name: "数据治理测试设备",
      device_type: "simulator",
    },
  })
  const device = await deviceResponse.json()
  await request.post("/api/v1/tags/", {
    headers,
    data: {
      device_id: device.id,
      code: "TEMP_E2E",
      name: "导入温度",
      data_type: "float",
      unit: "℃",
      min_value: 0,
      max_value: 1000,
      sampling_interval_ms: 1000,
    },
  })

  await page.goto("/data-governance")
  await expect(
    page.getByRole("heading", { name: "工业数据导入与治理" }),
  ).toBeVisible()
  await page.getByLabel("所属工厂").selectOption(plant.id)
  await page.getByLabel("CSV / XLSX 文件（最大 50 MB）").setInputFiles({
    name: `quality-${suffix}.csv`,
    mimeType: "text/csv",
    buffer: Buffer.from(
      [
        "timestamp,device_code,tag_code,value,quality",
        `2026-08-22T10:00:00+08:00,${deviceCode},TEMP_E2E,120,Good`,
        `2026-08-22T10:00:01+08:00,${deviceCode},TEMP_E2E,1200,Good`,
      ].join("\n"),
    ),
  })
  await page.getByRole("button", { name: "上传并预览" }).click()
  await expect(page.getByText("确认字段映射", { exact: true })).toBeVisible()
  await expect(page.getByText("TEMP_E2E").first()).toBeVisible()
  await page.getByRole("button", { name: "执行质量校验并入库" }).click()
  await expect(
    page.getByText("导入任务已提交，可离开页面后查看进度与结果"),
  ).toBeVisible()
  // Completion must survive navigation/reload; it is not a browser-owned task.
  await page.reload()
  await page.getByLabel("所属工厂").selectOption(plant.id)
  const row = page.getByRole("row").filter({ hasText: `quality-${suffix}.csv` })
  await expect(row).toContainText("已完成", { timeout: 20_000 })
  await expect(row).toContainText("1")
  await row.getByRole("button", { name: "查看" }).click()
  await expect(page.getByText("超出测点范围", { exact: true })).toBeVisible()

  // A permanent mapping error must be visible; retry must create a new job,
  // not silently mutate the historical failed attempt or claim success.
  const failedPreview = await request.post(
    `/api/v1/imports/preview?plant_id=${plant.id}`,
    {
      headers,
      multipart: {
        file: {
          name: `retry-${suffix}.csv`,
          mimeType: "text/csv",
          buffer: Buffer.from(
            "timestamp,tag_code,value\n2026-08-22T10:01:00+08:00,TEMP_E2E,121\n",
          ),
        },
      },
    },
  )
  expect(failedPreview.ok()).toBeTruthy()
  const failed = await failedPreview.json()
  const batchUrl = `/api/v1/imports/${failed.batch.id}`
  const enqueued = await request.post(`${batchUrl}/process`, {
    headers,
    data: { ...failed.suggested_mapping, value_column: "missing_column" },
  })
  expect(enqueued.status()).toBe(202)
  await expect
    .poll(
      async () =>
        (await (await request.get(batchUrl, { headers })).json()).status,
      { timeout: 20_000 },
    )
    .toBe("failed")
  const oldJob = await (
    await request.get(`${batchUrl}/job`, { headers })
  ).json()
  await page.reload()
  await page.getByLabel("所属工厂").selectOption(plant.id)
  const failedRow = page
    .getByRole("row")
    .filter({ hasText: `retry-${suffix}.csv` })
  await failedRow.getByRole("button", { name: "查看" }).click()
  await expect(page.getByText("映射列不存在：missing_column")).toBeVisible()
  const retryResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith(`${batchUrl}/process`) &&
      response.request().method() === "POST",
  )
  await page.getByRole("button", { name: "按原映射重试" }).click()
  expect((await retryResponse).status()).toBe(202)
  await expect
    .poll(
      async () =>
        (await (await request.get(`${batchUrl}/job`, { headers })).json()).id,
      { timeout: 20_000 },
    )
    .not.toBe(oldJob.id)
  await expect(page.getByRole("region", { name: "导入任务进度" })).toBeVisible()
})
