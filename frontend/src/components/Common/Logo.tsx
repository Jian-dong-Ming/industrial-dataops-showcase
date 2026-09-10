import { Link } from "@tanstack/react-router"
import { Factory } from "lucide-react"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const icon = (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-sm">
      <Factory className="size-5" />
    </span>
  )

  const content = (
    <span
      className={cn(
        "flex items-center gap-3 text-foreground",
        variant === "full" && "text-xl font-semibold",
        className,
      )}
    >
      {icon}
      {variant !== "icon" && (
        <span
          className={cn(
            "whitespace-nowrap leading-tight",
            variant === "responsive" &&
              "text-base font-semibold group-data-[collapsible=icon]:hidden",
          )}
        >
          工业数据运维平台
        </span>
      )}
    </span>
  )

  if (!asLink) return content
  return <Link to="/">{content}</Link>
}
