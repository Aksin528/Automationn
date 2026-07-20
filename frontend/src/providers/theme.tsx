"use client"

import { ThemeProvider as NextThemesProvider } from "next-themes"

/**
 * Applies the user's light/dark/system theme preference to the whole app by
 * toggling the `dark` class on `<html>`. Wrap the root layout's body content
 * with this so every route (including logged-out pages) picks up the theme.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider attribute="class" defaultTheme="system" enableSystem>
      {children}
    </NextThemesProvider>
  )
}
