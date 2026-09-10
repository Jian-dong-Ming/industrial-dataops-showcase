import { expect, test } from "@playwright/test"

test("retention preview is persisted, plant-scoped and explicitly non-destructive", async ({
  page,
  request,
}) => {
  await page.goto("/")
  const token = await page.evaluate(() => localStorage.getItem("access_token"))
  const headers = { Authorization: `Bearer ${token}` }
  const plants: string[] = []
  for (const label of ["A", "B"]) {
    const response = await request.post("/api/v1/plants/", {
      headers,
      data: {
        code: `RET_${label}_${Date.now()}`,
        name: `保留预览测试${label}`,
      },
    })
    expect(response.ok()).toBeTruthy()
    plants.push((await response.json()).id)
  }
  await page.goto("/data-governance")
  await page.getByLabel("所属工厂").selectOption(plants[0])
  await expect(page.getByRole("region", { name: "全库容量" })).toBeVisible()
  await page.getByLabel("预览数据来源").selectOption("file")
  await page.getByLabel("计划保留最近天数").fill("0")
  const preview = page.getByRole("button", { name: "预览更早的数据（不删除）" })
  await expect(preview).toBeDisabled()
  await page.getByLabel("计划保留最近天数").fill("7")
  await preview.click()
  const history = page.getByRole("table", { name: "数据保留预览记录" })
  await expect(history.getByRole("row")).toHaveCount(2)
  await expect(history).toContainText("仅预览，未删除")
  await expect(history).toContainText("历史文件样本")
  await page.reload()
  await page.getByLabel("所属工厂").selectOption(plants[0])
  await expect(history).toContainText("仅预览，未删除")
  await page.getByLabel("所属工厂").selectOption(plants[1])
  await expect(history).toContainText("当前工厂暂无预览记录")
  await expect(history).not.toContainText("仅预览，未删除")
})
