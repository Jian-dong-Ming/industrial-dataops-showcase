import { Link } from "@tanstack/react-router"
import { Button } from "@/components/ui/button"

const ErrorComponent = () => {
  return (
    <div
      className="flex min-h-screen items-center justify-center flex-col p-4"
      data-testid="error-component"
    >
      <div className="flex items-center z-10">
        <div className="flex flex-col ml-4 items-center justify-center p-4">
          <span className="text-6xl md:text-8xl font-bold leading-none mb-4">
            出错了
          </span>
          <span className="text-2xl font-bold mb-2">页面加载失败</span>
        </div>
      </div>

      <p className="text-lg text-muted-foreground mb-4 text-center z-10">
        页面未能正确加载，不代表业务操作或模型问答已经执行。请重新加载；若重复出现，请反馈访问路径。
      </p>
      <Button className="mb-3" onClick={() => window.location.reload()}>
        重新加载当前页
      </Button>
      <Link to="/">
        <Button>返回工作台</Button>
      </Link>
    </div>
  )
}

export default ErrorComponent
