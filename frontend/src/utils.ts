import { AxiosError } from "axios"

const errorTranslations: Record<string, string> = {
  "Incorrect email or password": "邮箱或密码错误",
  "The user with this email already exists in the system": "该邮箱已注册",
  "Invalid token": "重置链接无效或已过期",
  "New password cannot be the same as the current one":
    "新密码不能与当前密码相同",
  "Not enough permissions": "当前账号没有执行此操作的权限",
  "Plant not found": "未找到该工厂",
  "Production line not found": "未找到该产线",
  "Device not found": "未找到该设备",
  "Tag not found": "未找到该测点",
  "Plant code already exists": "工厂编码已存在",
  "Production line code already exists in this plant":
    "该工厂下已存在相同的产线编码",
  "Device code already exists on this production line":
    "该产线下已存在相同的设备编码",
  "Tag code already exists on this device": "该设备下已存在相同的测点编码",
  "Cannot add a production line to an inactive plant":
    "不能在已禁用的工厂下新增产线",
  "Cannot add a device to an inactive production line":
    "不能在已禁用的产线下新增设备",
  "Cannot add a tag to an inactive device": "不能在已禁用的设备下新增测点",
  "Acquisition task not found": "未找到该采集任务",
  "Cannot add an acquisition task to an inactive plant":
    "不能在已禁用的工厂下新增采集任务",
  "The task plant is inactive": "采集任务所属工厂已禁用",
  "Mapped tag device or production line is inactive":
    "映射测点所属的设备或产线已禁用",
  "Mapped tag asset hierarchy is inactive":
    "映射测点或其所属设备、产线已禁用，无法启动采集",
  "No enabled nodes are configured": "采集任务没有配置可用节点",
  "Stop the acquisition task before editing it": "请先停止采集任务再修改配置",
  "A tag can only be mapped once": "同一测点在一个任务中只能映射一次",
  "A NodeId can only be mapped once":
    "同一 OPC UA 节点在一个任务中只能映射一次",
  "Every mapped tag must belong to the task plant":
    "所有映射测点必须属于采集任务所选工厂",
  "Unable to browse the OPC UA endpoint":
    "无法连接或浏览 OPC UA 端点，请检查模拟器和地址",
  "Task name, tag mapping, or NodeId mapping conflicts with an existing acquisition task":
    "任务名称、测点映射或节点映射与已有任务冲突",
}

function translateErrorMessage(message: string): string {
  return errorTranslations[message] ?? message
}

function extractErrorMessage(err: Error): string {
  if (err instanceof AxiosError) {
    const errDetail = (err.response?.data as any)?.detail
    if (Array.isArray(errDetail) && errDetail.length > 0) {
      return translateErrorMessage(errDetail[0].msg)
    }
    if (typeof errDetail === "string") {
      return translateErrorMessage(errDetail)
    }
    return translateErrorMessage(err.message)
  }
  return "系统暂时无法完成请求，请稍后重试。"
}

export const handleError = function (this: (msg: string) => void, err: Error) {
  const errorMessage = extractErrorMessage(err)
  this(errorMessage)
}

export const getInitials = (name: string): string => {
  return name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase()
}
