import { type FullConfig, request } from "@playwright/test"

export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0]?.use.baseURL
  if (!baseURL) throw new Error("Browser tests require an explicit target URL")
  const client = await request.newContext({ baseURL, timeout: 15_000 })
  try {
    const response = await client.get("/api/v1/utils/browser-test-safety/")
    const safety = response.ok() ? await response.json() : null
    if (safety?.browser_tests_allowed !== true) {
      throw new Error(
        "Refusing browser tests: target is not an isolated test instance. " +
          "Use a dedicated *_test database with BROWSER_TEST_MODE=true. " +
          "Never point write-capable tests at the daily demo or business database.",
      )
    }
  } finally {
    await client.dispose()
  }
}
