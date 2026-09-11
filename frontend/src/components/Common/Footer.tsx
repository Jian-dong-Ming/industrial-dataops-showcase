export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t px-6 py-4">
      <p className="text-center text-xs text-muted-foreground">
        工业数据运维平台 · 个人项目 · 非生产控制系统 · {currentYear}
      </p>
    </footer>
  )
}
