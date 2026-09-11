import assert from "node:assert/strict"
import { test } from "node:test"
import {
  formatAge,
  freshnessThresholdMs,
  timePosition,
} from "../src/lib/telemetry"

test("freshness threshold agrees with the assistant and respects slow sampling", () => {
  assert.equal(freshnessThresholdMs(1000), 60_000)
  assert.equal(freshnessThresholdMs(30_000), 90_000)
})
test("age display distinguishes seconds, hours, days and invalid clocks", () => {
  assert.equal(formatAge(1000), "1.0 秒")
  assert.equal(formatAge(3_600_000), "1 小时")
  assert.equal(formatAge(90_000_000), "1 天 1 小时")
  assert.equal(formatAge(-10000), "时间超前，请检查时钟")
  assert.equal(formatAge(Number.NaN), "时间无效")
})
test("trend x axis uses elapsed time, not sample ordinal", () => {
  assert.equal(timePosition(10, 0, 100), 0.1)
  assert.equal(timePosition(50, 50, 50), 0.5)
})
