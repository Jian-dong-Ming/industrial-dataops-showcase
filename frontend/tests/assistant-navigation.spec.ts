import { expect, test } from "@playwright/test"

test("governance and assistant navigation never share incompatible plant cache", async ({
  page,
}) => {
  const errors: string[] = []
  page.on("pageerror", (error) => errors.push(error.message))
  await page.goto("/data-governance")
  // Use the sidebar (not goto/reload): the regression requires one QueryClient.
  for (let i = 0; i < 3; i++) {
    await page.getByRole("link", { name: "数据治理", exact: true }).click()
    await expect(page.getByLabel("所属工厂", { exact: true })).toBeVisible()
    await expect(page.locator("#import-plant option")).not.toHaveCount(0)
    await page.getByRole("link", { name: "AI 数据助手", exact: true }).click()
    await expect(
      page.getByRole("heading", { name: "AI 数据治理助手" }),
    ).toBeVisible()
    await expect(page.getByLabel("查询范围")).toBeVisible()
    await expect(page.getByTestId("error-component")).toHaveCount(0)
  }
  expect(errors).toEqual([])
})

test("forbidden assistant request stays on page and preserves login", async ({
  page,
}) => {
  await page.route("**/api/v1/assistant/status", (route) =>
    route.fulfill({
      status: 403,
      json: { detail: "没有当前工厂权限" },
    }),
  )
  await page.goto("/assistant")
  await expect(page.getByRole("alert")).toContainText("没有当前工厂权限")
  await expect(page).toHaveURL(/\/assistant$/)
  expect(
    await page.evaluate(() => Boolean(localStorage.getItem("access_token"))),
  ).toBe(true)
})
