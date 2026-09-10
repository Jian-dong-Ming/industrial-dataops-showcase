import { expect, type Page, test } from "@playwright/test"

const uniqueCode = (prefix: string) =>
  `${prefix}_${Math.random().toString(36).slice(2, 10).toUpperCase()}`

async function createPlant(page: Page) {
  const code = uniqueCode("PLANT")
  const name = `Synthetic Plant ${code}`
  await page.getByRole("button", { name: "新增工厂" }).click()
  await page.getByLabel("编码").fill(code)
  await page.getByLabel("名称").fill(name)
  await page.getByLabel("位置").fill("Test Zone")
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("工厂创建成功")).toBeVisible()
  await expect(page.getByRole("row").filter({ hasText: code })).toBeVisible()
  return { code, name }
}

test.beforeEach(async ({ page }) => {
  await page.goto("/assets")
})

test("Assets page exposes the four-level industrial hierarchy", async ({
  page,
}) => {
  await expect(
    page.getByRole("heading", { name: "工业资产管理" }),
  ).toBeVisible()
  await expect(page.getByRole("tab", { name: /工厂/ })).toBeVisible()
  await expect(page.getByRole("tab", { name: /产线/ })).toBeVisible()
  await expect(page.getByRole("tab", { name: /设备/ })).toBeVisible()
  await expect(page.getByRole("tab", { name: /测点/ })).toBeVisible()
})

test("Administrator can create, edit, disable, and re-enable a plant", async ({
  page,
}) => {
  const plant = await createPlant(page)
  const row = page.getByRole("row").filter({ hasText: plant.code })
  await row.getByRole("button", { name: `编辑${plant.name}` }).click()
  await page.getByLabel("名称").fill(`${plant.name} Updated`)
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("工厂更新成功")).toBeVisible()

  const updatedRow = page.getByRole("row").filter({ hasText: plant.code })
  await updatedRow
    .getByRole("button", { name: new RegExp(`禁用${plant.name}`) })
    .click()
  await expect(page.getByText("资产已禁用")).toBeVisible()
  await expect(updatedRow.getByText("禁用")).toBeVisible()

  await updatedRow
    .getByRole("button", { name: new RegExp(`启用${plant.name}`) })
    .click()
  await expect(page.getByText("资产已重新启用")).toBeVisible()
  await expect(updatedRow.getByText("启用")).toBeVisible()
})

test("Administrator can create line, device, and tag under a plant", async ({
  page,
}) => {
  const plant = await createPlant(page)

  const lineCode = uniqueCode("LINE")
  await page.getByRole("tab", { name: /产线/ }).click()
  await page.getByRole("button", { name: "新增产线" }).click()
  await page.getByLabel("所属工厂").click()
  await page
    .getByRole("option", { name: `${plant.code} · ${plant.name}` })
    .click()
  await page.getByLabel("编码").fill(lineCode)
  await page.getByLabel("名称").fill("Synthetic Line")
  await page.getByLabel("工艺类型").fill("continuous")
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("产线创建成功")).toBeVisible()

  const deviceCode = uniqueCode("DEVICE")
  await page.getByRole("tab", { name: /设备/ }).click()
  await page.getByRole("button", { name: "新增设备" }).click()
  await page.getByLabel("所属产线").click()
  await page
    .getByRole("option", { name: `${lineCode} · Synthetic Line` })
    .click()
  await page.getByLabel("编码").fill(deviceCode)
  await page.getByLabel("名称").fill("Synthetic Furnace")
  await page.getByLabel("设备类型").fill("furnace")
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("设备创建成功")).toBeVisible()

  const tagCode = uniqueCode("TAG")
  await page.getByRole("tab", { name: /测点/ }).click()
  await page.getByRole("button", { name: "新增测点" }).click()
  await page.getByLabel("所属设备").click()
  await page
    .getByRole("option", { name: `${deviceCode} · Synthetic Furnace` })
    .click()
  await page.getByLabel("编码").fill(tagCode)
  await page.getByLabel("名称").fill("Outlet Temperature")
  await page.getByLabel("单位").fill("degC")
  await page.getByLabel("最小值").fill("0")
  await page.getByLabel("最大值").fill("1500")
  await page.getByRole("button", { name: "保存" }).click()
  await expect(page.getByText("测点创建成功")).toBeVisible()
  await expect(page.getByRole("row").filter({ hasText: tagCode })).toBeVisible()
})

test("Plant code is normalized to uppercase in the form", async ({ page }) => {
  await page.getByRole("button", { name: "新增工厂" }).click()
  await page.getByLabel("编码").fill("plant_lowercase")
  await expect(page.getByLabel("编码")).toHaveValue("PLANT_LOWERCASE")
  await page.getByRole("button", { name: "取消" }).click()
  await expect(page.getByRole("dialog")).not.toBeVisible()
})
