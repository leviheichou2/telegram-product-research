import { type ReactNode, useEffect, useState } from "react"
import {
  BarChart3,
  ChevronDown,
  LayoutGrid,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  Sparkles,
  Tag,
  Truck,
  ImageIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { DotPattern } from "@/components/ui/dot-pattern"
import { Separator } from "@/components/ui/separator"
import {
  MetaAdsDialog,
  TelegramDialog,
} from "@/components/connection-dialog"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

export type PageId =
  | "overview"
  | "products"
  | "product"
  | "scan"
  | "image-search"
  | "suppliers"
  | "reports"
  | "settings"

type NavItem = { id: PageId; label: string; icon: typeof LayoutGrid; title?: string }

const OVERVIEW: NavItem = { id: "overview", label: "Overview", icon: LayoutGrid }
const RESEARCH: NavItem[] = [
  { id: "products", label: "Product search", icon: Search, title: "Search the scanned products" },
  { id: "image-search", label: "Image search", icon: ImageIcon, title: "Find a product by photo" },
  { id: "scan", label: "Scan", icon: Tag, title: "Run and follow Telegram scans" },
]
const SUPPLIERS: NavItem = { id: "suppliers", label: "Suppliers", icon: Truck }
const SYSTEM: NavItem[] = [
  { id: "reports", label: "Reports", icon: BarChart3 },
  { id: "settings", label: "Settings", icon: SettingsIcon },
]

function ConnectionDot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  return (
    <span
      className={cn(
        "size-1.5 shrink-0 rounded-full",
        ok ? "bg-emerald-500" : warn ? "bg-amber-500" : "bg-muted-foreground/40",
      )}
    />
  )
}

export function AppShell({
  page,
  onNavigate,
  scanLabel,
  children,
}: {
  page: PageId
  onNavigate: (page: PageId) => void
  scanLabel: string
  children: ReactNode
}) {
  const [researchOpen, setResearchOpen] = useState(true)
  const [telegram, setTelegram] = useState<{
    needs_login: boolean
    running: boolean
    chats_loaded: boolean
  }>({ needs_login: false, running: false, chats_loaded: false })
  const [metaConfigured, setMetaConfigured] = useState(false)
  const [telegramConfigured, setTelegramConfigured] = useState(false)
  const [telegramOpen, setTelegramOpen] = useState(false)
  const [metaOpen, setMetaOpen] = useState(false)
  const [refreshing, setRefreshing] = useState(false)

  async function refreshTelegram() {
    setRefreshing(true)
    try {
      await api.reloadChats()
    } catch {
      /* the connection bar shows the real state on the next poll */
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const [status, settings] = await Promise.all([
          api.status(),
          api.settings().catch(() => ({}) as Record<string, unknown>),
        ])
        if (cancelled) return
        setTelegram({
          needs_login: Boolean(status.needs_login),
          running: Boolean(status.running),
          chats_loaded: Boolean(status.chats_loaded),
        })
        setTelegramConfigured(Boolean(settings.telegram_configured))
        setMetaConfigured(Boolean(settings.meta_configured))
      } catch {
        /* the server may still be starting; the next tick retries */
      }
    }
    void poll()
    const timer = window.setInterval(poll, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  function navItem(item: NavItem) {
    const Icon = item.icon
    return (
      <Button
        key={item.id}
        variant={page === item.id ? "secondary" : "ghost"}
        className={cn("justify-start gap-2", page === item.id && "font-semibold")}
        onClick={() => onNavigate(item.id)}
        title={item.title}
      >
        <Icon className="size-4" />
        {item.label}
      </Button>
    )
  }

  const telegramLabel = telegram.needs_login
    ? "Telegram: sign in needed"
    : telegram.running
      ? "Telegram: working…"
      : telegram.chats_loaded
        ? "Telegram: connected"
        : telegramConfigured
          ? "Telegram: not loaded"
          : "Telegram not connected"

  return (
    <div className="relative min-h-screen bg-background text-foreground">
      <DotPattern
        className={cn(
          "pointer-events-none absolute inset-0 opacity-[0.35]",
          "[mask-image:radial-gradient(520px_circle_at_20%_0%,black,transparent)]",
        )}
      />
      <div className="relative mx-auto flex min-h-screen w-full max-w-[1500px]">
        {/* The sidebar is sticky: it keeps its own scroll and never slides away
            with the page content. */}
        <aside className="sticky top-0 hidden h-screen w-64 shrink-0 self-start flex-col border-r bg-background/80 px-3 py-4 backdrop-blur md:flex">
          <div className="flex items-center gap-2 px-2 pb-4">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Sparkles className="size-4" />
            </div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold leading-tight">Product Scout</p>
              <p className="truncate text-xs text-muted-foreground">{scanLabel || "No scan selected"}</p>
            </div>
          </div>
          <Separator />

          <nav className="mt-3 flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
            {navItem(OVERVIEW)}

            <p className="px-2 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Research
            </p>
            <Button
              variant="ghost"
              className="justify-start gap-2"
              onClick={() => setResearchOpen((open) => !open)}
              aria-expanded={researchOpen}
            >
              <Search className="size-4" />
              Research tools
              <ChevronDown
                className={cn(
                  "ml-auto size-3.5 transition-transform",
                  !researchOpen && "-rotate-90",
                )}
              />
            </Button>
            {researchOpen ? (
              <div className="ml-4 flex flex-col gap-1 border-l pl-2">
                {RESEARCH.map(navItem)}
              </div>
            ) : null}
            {navItem(SUPPLIERS)}

            <p className="px-2 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              System
            </p>
            {SYSTEM.map(navItem)}
          </nav>

          <div className="mt-3 shrink-0 space-y-2 rounded-xl border bg-muted/40 p-3">
            <p className="text-xs font-medium">Vision engine</p>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              DINOv2 embeddings retrieved with FAISS, corroborated by OCR, brand and model text.
            </p>
            <Badge variant="outline" className="text-[10px]">
              Local · CPU
            </Badge>
          </div>
        </aside>

        <main className="min-w-0 flex-1 px-4 py-5 md:px-8">
          {/* Connection bar: the same three controls the old dashboard had,
              grouped on the right. Settings lives in the sidebar. */}
          <div className="mb-4 flex flex-wrap items-center justify-end gap-2 border-b pb-3">
            {!telegramConfigured ? (
              <p className="mr-auto text-[11px] text-amber-600">
                Telegram API keys missing — connect Telegram to scan your channels
              </p>
            ) : null}
            <Button
              size="sm"
              variant="outline"
              onClick={() => void refreshTelegram()}
              disabled={refreshing}
              title="Reload your Telegram chats"
            >
              <RefreshCw className={cn("size-3.5", refreshing && "animate-spin")} />
              Refresh
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setTelegramOpen(true)}
              title="Telegram credentials"
            >
              <ConnectionDot
                ok={telegram.chats_loaded && !telegram.needs_login}
                warn={telegram.needs_login || telegram.running}
              />
              {telegramLabel}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setMetaOpen(true)}
              title="Meta Ad Library country and connection test"
            >
              <ConnectionDot ok={metaConfigured} />
              {metaConfigured ? "Meta Ad Library ready" : "Meta Ad Library unavailable"}
            </Button>
          </div>

          <header className="mb-5 flex flex-wrap items-center justify-between gap-3 md:hidden">
            <p className="text-sm font-semibold">Product Scout</p>
            <div className="flex flex-wrap gap-1">
              {[OVERVIEW, ...RESEARCH, SUPPLIERS, ...SYSTEM].map((item) => (
                <Button
                  key={item.id}
                  size="sm"
                  variant={page === item.id ? "secondary" : "ghost"}
                  onClick={() => onNavigate(item.id)}
                >
                  {item.label}
                </Button>
              ))}
            </div>
          </header>
          {children}
        </main>
      </div>

      <TelegramDialog open={telegramOpen} onOpenChange={setTelegramOpen} />
      <MetaAdsDialog open={metaOpen} onOpenChange={setMetaOpen} />
    </div>
  )
}
