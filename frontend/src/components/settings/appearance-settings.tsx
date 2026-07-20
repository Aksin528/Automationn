"use client"

import { useTheme } from "next-themes"
import { useEffect, useState } from "react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

/** Lets the user pick light, dark, or system theme. Applies to every page. */
export function AppearanceSettings() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  return (
    <div className="grid gap-4">
      <div className="grid gap-1.5">
        <span className="text-sm font-medium text-muted-foreground">Theme</span>
        <Select
          value={mounted ? theme : undefined}
          onValueChange={setTheme}
          disabled={!mounted}
        >
          <SelectTrigger className="max-w-lg">
            <SelectValue placeholder="System" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="light">Light</SelectItem>
            <SelectItem value="dark">Dark</SelectItem>
            <SelectItem value="system">System</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}
