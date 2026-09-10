export function Footer() {
  const currentYear = new Date().getFullYear()

  return (
    <footer className="border-t px-6 py-4">
      <p className="text-center text-xs text-muted-foreground">
        工业数据运维平台 · 阶段 0—3 本地验证版本 · {currentYear}
      </p>
    </footer>
  )
}
