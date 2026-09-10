import { queryOptions } from "@tanstack/react-query"
import { PlantsService } from "@/client"

// A query key must always identify the same response shape and request parameters.
// Consumers select from this shared page object; never cache a bare array here.
export const plantListOptions = () =>
  queryOptions({
    queryKey: ["plants", "list", { limit: 100 }],
    queryFn: async () =>
      (await PlantsService.readPlants({ query: { limit: 100 } })).data,
  })
