import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Pencil } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { PlantsService, type UserPublic, UsersService } from "@/client"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

const formSchema = z
  .object({
    email: z.email({ message: "请输入有效的邮箱地址" }),
    full_name: z.string().optional(),
    password: z
      .string()
      .min(8, { message: "密码至少需要 8 个字符" })
      .optional()
      .or(z.literal("")),
    confirm_password: z.string().optional(),
    role: z.enum(["admin", "engineer", "observer"]),
    is_active: z.boolean().optional(),
  })
  .refine((data) => !data.password || data.password === data.confirm_password, {
    message: "两次输入的密码不一致",
    path: ["confirm_password"],
  })

type FormData = z.infer<typeof formSchema>

interface EditUserProps {
  user: UserPublic
  onSuccess: () => void
}

const EditUser = ({ user, onSuccess }: EditUserProps) => {
  const [isOpen, setIsOpen] = useState(false)
  const [plantIds, setPlantIds] = useState<string[]>([])
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const form = useForm<FormData>({
    resolver: zodResolver(formSchema),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      email: user.email,
      full_name: user.full_name ?? undefined,
      role: user.role ?? (user.is_superuser ? "admin" : "observer"),
      is_active: user.is_active,
    },
  })

  const plantsQuery = useQuery({
    queryKey: ["admin", "plants"],
    queryFn: async () =>
      (await PlantsService.readPlants({ query: { limit: 100 } })).data,
    enabled: isOpen,
  })
  const accessQuery = useQuery({
    queryKey: ["admin", "plant-access", user.id],
    queryFn: async () =>
      (
        await UsersService.readUserPlantAccess({
          path: { user_id: user.id },
        })
      ).data,
    enabled: isOpen,
  })

  useEffect(() => {
    if (accessQuery.data) setPlantIds(accessQuery.data.plant_ids)
  }, [accessQuery.data])

  const mutation = useMutation({
    mutationFn: ({
      data,
      assignedPlantIds,
    }: {
      data: FormData
      assignedPlantIds: string[]
    }) =>
      Promise.all([
        UsersService.updateUser({ path: { user_id: user.id }, body: data }),
        UsersService.replaceUserPlantAccess({
          path: { user_id: user.id },
          body: { plant_ids: assignedPlantIds },
        }),
      ]),
    onSuccess: () => {
      showSuccessToast("用户信息更新成功")
      setIsOpen(false)
      onSuccess()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const onSubmit = (data: FormData) => {
    // exclude confirm_password from submission data and remove password if empty
    const { confirm_password: _, ...submitData } = data
    if (!submitData.password) {
      delete submitData.password
    }
    mutation.mutate({ data: submitData, assignedPlantIds: plantIds })
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuItem
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        <Pencil />
        编辑用户
      </DropdownMenuItem>
      <DialogContent className="sm:max-w-md">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <DialogHeader>
              <DialogTitle>编辑用户</DialogTitle>
              <DialogDescription>
                修改用户资料、角色和工厂访问范围。
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      邮箱 <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      <Input
                        placeholder="请输入邮箱"
                        type="email"
                        {...field}
                        required
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="full_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>姓名</FormLabel>
                    <FormControl>
                      <Input placeholder="请输入姓名" type="text" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>重置密码</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="留空表示不修改"
                        type="password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="confirm_password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>确认密码</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="再次输入新密码"
                        type="password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="role"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>角色</FormLabel>
                    <Select value={field.value} onValueChange={field.onChange}>
                      <FormControl>
                        <SelectTrigger className="w-full bg-background">
                          <SelectValue placeholder="选择角色" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="observer">观察者</SelectItem>
                        <SelectItem value="engineer">工程师</SelectItem>
                        <SelectItem value="admin">管理员</SelectItem>
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="grid gap-2">
                <FormLabel>授权工厂</FormLabel>
                <div className="max-h-36 space-y-2 overflow-y-auto rounded-md border p-3">
                  {(plantsQuery.data?.data ?? []).map((plant) => (
                    <div
                      key={plant.id}
                      className="flex items-center gap-2 text-sm"
                    >
                      <Checkbox
                        id={`plant-access-${plant.id}`}
                        checked={plantIds.includes(plant.id)}
                        onCheckedChange={(checked) =>
                          setPlantIds((current) =>
                            checked
                              ? [...new Set([...current, plant.id])]
                              : current.filter((id) => id !== plant.id),
                          )
                        }
                      />
                      <Label
                        htmlFor={`plant-access-${plant.id}`}
                        className="font-normal"
                      >
                        {plant.code} · {plant.name}
                      </Label>
                    </div>
                  ))}
                  {plantsQuery.data?.data.length === 0 && (
                    <p className="text-muted-foreground text-sm">
                      暂无可授权工厂
                    </p>
                  )}
                </div>
              </div>

              <FormField
                control={form.control}
                name="is_active"
                render={({ field }) => (
                  <FormItem className="flex items-center gap-3 space-y-0">
                    <FormControl>
                      <Checkbox
                        checked={field.value}
                        onCheckedChange={field.onChange}
                      />
                    </FormControl>
                    <FormLabel className="font-normal">启用账号</FormLabel>
                  </FormItem>
                )}
              />
            </div>

            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  取消
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                保存
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default EditUser
