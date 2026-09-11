import { createHash } from "node:crypto"
import { readFile } from "node:fs/promises"
import { expect, test } from "@playwright/test"

test("offline replay exposes phases, quality, control line and exact download", async ({
  page,
}, testInfo) => {
  await page.goto("/demo")
  await expect(
    page.getByRole("heading", { name: "场景回放", exact: true }),
  ).toBeVisible()
  await expect(
    page.getByText("回放不是当前生产状态", { exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: "查看异常片段" }).click()
  const table = page.getByRole("table", { name: "回放测点字典" })
  await expect(table.getByRole("row")).toHaveCount(13)
  await expect(
    table.getByRole("row").filter({ hasText: "PUMP_FLOW" }),
  ).toContainText("坏质量，不作有效值")
  await page.getByRole("button", { name: "2号线（对照）" }).click()
  await expect(
    table.getByRole("row").filter({ hasText: "L2_PUMP_FLOW" }),
  ).toContainText("质量良好")
  await page.getByRole("button", { name: "1号线（注入侧）" }).click()
  await page.getByRole("button", { name: "查看恢复片段" }).click()
  await expect(
    table.getByRole("row").filter({ hasText: "PUMP_FLOW" }),
  ).toContainText("质量良好")
  const downloadPromise = page.waitForEvent("download")
  await page.getByRole("button", { name: "下载合成CSV" }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe("synthetic-cooling-loop-v1.csv")
  const downloaded = await readFile((await download.path())!)
  expect(downloaded.toString().trim().split("\n")).toHaveLength(2185)
  await page.getByText("复现校验与限制", { exact: true }).click()
  await expect(
    page.getByText(
      `CSV SHA-256：${createHash("sha256").update(downloaded).digest("hex")}`,
    ),
  ).toBeVisible()
  await page.getByRole("button", { name: "查看异常片段" }).click()
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({
    path: testInfo.outputPath("scenario-desktop.png"),
    fullPage: true,
  })
})

test("replay remains usable on mobile without document overflow", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto("/demo")
  await expect(page.getByRole("button", { name: "查看正常片段" })).toBeVisible()
  await page.getByRole("button", { name: "查看异常片段" }).click()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy()
  await page.screenshot({
    path: testInfo.outputPath("scenario-mobile.png"),
    fullPage: true,
  })
})
