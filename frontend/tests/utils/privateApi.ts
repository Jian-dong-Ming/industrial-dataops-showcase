// Note: the `PrivateService` is only available when generating the client
// for local environments
import { PrivateService } from "../../src/client"
import { client } from "../../src/client/client.gen"

client.setConfig({
  // Must target the same instance checked by Playwright's safety gate.
  // VITE_API_URL from a developer's .env can point at the daily business server.
  baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
})

export const createUser = async ({
  email,
  password,
}: {
  email: string
  password: string
}) => {
  const response = await PrivateService.createUser({
    body: {
      email,
      password,
      is_verified: true,
      full_name: "Test User",
    },
  })
  return response.data
}
