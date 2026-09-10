import { expect, test } from "@playwright/test"

const taskName = "OPC UA 模拟采集任务"
const secondTaskName = "OPC UA 二号线模拟采集任务"

test.beforeEach(async ({ page }) => {
  await page.goto("/acquisition")
})

test("Acquisition page exposes simulator safety boundary and task status", async ({
  page,
}) => {
  await expect(
    page.getByRole("heading", { name: "OPC UA 实时采集" }),
  ).toBeVisible()
  await expect(page.getByText("当前为教学验证环境")).toBeVisible()
  const row = page.getByRole("row").filter({ hasText: taskName })
  await expect(row).toBeVisible()
  await expect(row.getByText(/已停止|连接中|已连接|重连中|异常/)).toBeVisible()
})

test("Task data views preserve explicit selection and scope trend requests", async ({
  page,
}) => {
  const tasksResponse = page.waitForResponse((response) =>
    /\/acquisition\/tasks\?/.test(response.url()),
  )
  await page.reload()
  const tasks = (await (await tasksResponse).json()).data
  const expectedTask = tasks.find(
    (task: { name: string }) => task.name === secondTaskName,
  )
  expect(expectedTask).toBeTruthy()
  const mutations: string[] = []
  const sampleRequests: string[] = []
  page.on("request", (request) => {
    if (/\/acquisition\/tasks\/[^/]+\/(start|stop)/.test(request.url())) {
      mutations.push(request.url())
    }
    if (/\/acquisition\/tags\/[^/]+\/samples/.test(request.url())) {
      sampleRequests.push(request.url())
    }
  })
  await page
    .getByRole("button", { name: `查看${taskName}实时值`, exact: true })
    .click()
  const taskSelector = page.getByRole("combobox", { name: "最新值采集任务" })
  await expect(taskSelector).toContainText(taskName)
  await taskSelector.click()
  await page.getByRole("option", { name: new RegExp(secondTaskName) }).click()
  await expect(taskSelector).toContainText(secondTaskName)
  await expect(page.getByText("L2_FURNACE_TEMP", { exact: true })).toBeVisible()
  // Observe a real polling cycle: hidden trends must not query history.
  await page.waitForResponse((response) =>
    /\/acquisition\/tasks\?/.test(response.url()),
  )
  expect(sampleRequests).toEqual([])
  const trendRequest = page.waitForRequest((request) =>
    /\/acquisition\/tags\/[^/]+\/samples/.test(request.url()),
  )
  await page.getByRole("tab", { name: "实时趋势" }).click()
  await expect(
    page.getByRole("combobox", { name: "趋势任务测点" }),
  ).toContainText(secondTaskName)
  const request = await trendRequest
  expect(new URL(request.url()).searchParams.get("task_id")).toBe(
    expectedTask.id,
  )
  await page.getByRole("tab", { name: "实时值" }).click()
  await expect(taskSelector).toContainText(secondTaskName)
  // Viewing another task must never start or stop it.
  expect(mutations).toEqual([])
})

for (const scenario of [
  { name: taskName, code: "FURNACE_TEMP" },
  { name: secondTaskName, code: "L2_FURNACE_TEMP" },
]) {
  test(`Administrator can browse simulator nodes and run ${scenario.name}`, async ({
    page,
  }) => {
    const taskName = scenario.name
    // A newer, stopped task must not determine which task the operator sees
    // after starting another one. Only reorder real responses; do not fake data.
    await page.route("**/api/v1/acquisition/tasks?*", async (route) => {
      const response = await route.fetch()
      const body = await response.json()
      body.data.sort(
        (left: { name: string }, right: { name: string }) =>
          Number(left.name === taskName) - Number(right.name === taskName),
      )
      await route.fulfill({ response, json: body })
    })
    await page.reload()
    const row = page.getByRole("row").filter({ hasText: taskName })
    await expect(row).toBeVisible()
    const stopButton = row.getByRole("button", { name: `停止${taskName}` })
    const wasRunning = await stopButton.isVisible()
    try {
      if (await stopButton.isVisible()) {
        await stopButton.click()
        await expect(page.getByText("采集任务已停止")).toBeVisible()
      }

      const editButton = row.getByRole("button", { name: `编辑${taskName}` })
      await expect(editButton).toBeEnabled()
      await editButton.click()
      await page.getByRole("button", { name: "浏览节点" }).click()
      await expect(page.getByText(/发现 \d+ 个变量节点/)).toBeVisible()
      await expect(
        page.getByText("炉温", { exact: true }).first(),
      ).toBeVisible()
      await page.getByRole("button", { name: "取消" }).click()

      const startedAt = Date.now()
      await row.getByRole("button", { name: `启动${taskName}` }).click()
      await expect(page.getByText("采集任务正在启动")).toBeVisible()
      await expect(row.getByText("已连接")).toBeVisible({ timeout: 20_000 })

      await page.getByRole("tab", { name: "实时值" }).click()
      await expect(page.getByText("全测点最新值")).toBeVisible()
      await expect(page.getByRole("combobox")).toContainText(taskName)
      const latestRow = page.getByRole("row").filter({
        has: page.getByText(scenario.code, { exact: true }),
      })
      await expect(latestRow).toBeVisible({ timeout: 20_000 })
      await expect(latestRow.getByText("正常")).toBeVisible({ timeout: 20_000 })
      await expect
        .poll(
          async () => {
            const response = await page.waitForResponse((response) =>
              response.url().includes("/latest-values"),
            )
            const sample = (await response.json()).data.find(
              (item: { tag_code: string }) => item.tag_code === scenario.code,
            )
            return sample?.source_timestamp
              ? Date.parse(sample.source_timestamp)
              : 0
          },
          { timeout: 20_000 },
        )
        .toBeGreaterThanOrEqual(startedAt)

      await page.getByRole("tab", { name: "实时趋势" }).click()
      await expect(page.getByRole("combobox")).toContainText(taskName)
      await expect(
        page.getByRole("img", { name: "测点实时趋势图" }),
      ).toBeVisible({
        timeout: 20_000,
      })

      await page.getByRole("tab", { name: "采集任务" }).click()
      await row.getByRole("button", { name: `停止${taskName}` }).click()
      await expect(page.getByText("采集任务已停止")).toBeVisible()
    } finally {
      // Preserve the operator's original state, including on assertion failure.
      await page.goto("/acquisition")
      const restoredRow = page.getByRole("row").filter({ hasText: taskName })
      await expect(restoredRow).toBeVisible()
      const start = restoredRow.getByRole("button", { name: `启动${taskName}` })
      const stop = restoredRow.getByRole("button", { name: `停止${taskName}` })
      if (wasRunning && (await start.isVisible())) {
        await start.click()
        await expect(restoredRow.getByText("已连接")).toBeVisible({
          timeout: 20_000,
        })
      } else if (!wasRunning && (await stop.isVisible())) {
        await stop.click()
        await expect(page.getByText("采集任务已停止")).toBeVisible()
      }
    }
  })
}
