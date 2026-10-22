import { useEffect, useMemo, useState } from "react"
import { Loader2, Play, Square } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { api, type AppSettings, type Chat } from "@/lib/api"
import { cn } from "@/lib/utils"

function isoDate(value: Date) {
  return value.toISOString().slice(0, 10)
}

function defaultRange(days: number) {
  const end = new Date()
  const start = new Date()
  start.setDate(end.getDate() - Math.max(0, days - 1))
  return { start: isoDate(start), end: isoDate(end) }
}

/**
 * The scan form. It lives on the Product Search page, like the old dashboard,
 * and on the Scan page. Defaults come from the app configuration, so the
 * look-back window, market and category chosen in Settings apply here.
 */
export function ScanControls({ compact = false }: { compact?: boolean }) {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [markets, setMarkets] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [chats, setChats] = useState<Chat[]>([])
  const [range, setRange] = useState(() => defaultRange(2))
  const [market, setMarket] = useState("")
  const [category, setCategory] = useState("")
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [status, setStatus] = useState<Record<string, unknown> | null>(null)

  useEffect(() => {
    api
      .appSettings()
      .then((payload) => {
        setSettings(payload.settings)
        setMarkets(payload.markets || [])
        setCategories(payload.categories || [])
        setRange(defaultRange(payload.settings.scan_lookback_days || 2))
        setMarket(payload.settings.market_country || "")
        setCategory(payload.settings.scan_category || "")
      })
      .catch(() => {})
    api
      .chats()
      .then((payload) => setChats(payload.chats || []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const payload = await api.status()
        if (!cancelled) setStatus(payload)
      } catch {
        /* the server may still be starting; the next tick retries */
      }
    }
    void poll()
    const timer = window.setInterval(poll, 4000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const running = Boolean(status?.running)
  const chatIndex = Number(status?.chat_index ?? 0)
  const chatTotal = Number(status?.chat_total ?? 0)
  const message = String(status?.message ?? "")
  const progress = chatTotal ? Math.round((chatIndex / chatTotal) * 100) : 0

  const chosen = useMemo(() => chats.filter((chat) => selected.has(chat.id)), [chats, selected])

  function toggle(id: number) {
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function start() {
    setBusy(true)
    setError("")
    try {
      await api.startScan({
        start_date: range.start,
        end_date: range.end,
        market: market || undefined,
        category: category || undefined,
        chat_ids: Array.from(selected),
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function stop() {
    setError("")
    try {
      const response = await fetch("/api/scan/cancel", { method: "POST" })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const label =
    category && selected.size === 0
      ? `Scan ${category}`
      : `Scan ${selected.size} channel${selected.size === 1 ? "" : "s"}`

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-base">Scan settings</CardTitle>
        <Badge variant="outline">
          {settings ? `${settings.scan_lookback_days} day window` : "loading defaults"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {error ? (
          <p className="rounded-lg bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
            {error}
          </p>
        ) : null}

        <div
          className={
            compact
              ? "grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
              : "grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
          }
        >
          <label className="space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">From</span>
            <Input
              type="date"
              value={range.start}
              onChange={(event) => setRange({ ...range, start: event.target.value })}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">Through</span>
            <Input
              type="date"
              value={range.end}
              onChange={(event) => setRange({ ...range, end: event.target.value })}
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">Market</span>
            <select
              value={market}
              onChange={(event) => setMarket(event.target.value)}
              className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
            >
              {(markets.length ? markets : ["Algeria", "United States"]).map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">Category</span>
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
            >
              <option value="">All channels</option>
              {categories.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => void start()} disabled={running || busy}>
            {busy ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Play className="mr-2 size-4" />
            )}
            {label}
          </Button>
          {running ? (
            <Button variant="destructive" onClick={() => void stop()}>
              <Square className="mr-2 size-4" />
              Stop scan
            </Button>
          ) : null}
          <span className="text-[11px] text-muted-foreground">
            {category && selected.size === 0
              ? "The channels of the chosen category are read from the source directory."
              : "Tick the channels to read, or leave them all off and pick a category."}
          </span>
        </div>

        {running ? (
          <div className="space-y-1.5 rounded-lg border px-3 py-2">
            <Progress value={progress} className="h-1.5" />
            <p className="text-[11px] text-muted-foreground">
              {message || "Scanning…"} {chatTotal ? `· channel ${chatIndex} of ${chatTotal}` : ""}
            </p>
          </div>
        ) : null}

        <div className="rounded-lg border">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
            <p className="text-[11px] font-medium text-muted-foreground">
              Channels ({chosen.length} selected)
            </p>
            <div className="flex gap-1">
              <Button
                size="xs"
                variant="ghost"
                onClick={() => setSelected(new Set(chats.map((chat) => chat.id)))}
              >
                All
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setSelected(new Set())}>
                None
              </Button>
            </div>
          </div>
          <div className="flex max-h-52 flex-wrap gap-1.5 overflow-y-auto p-2">
            {chats.length === 0 ? (
              <p className="px-2 py-4 text-xs text-muted-foreground">
                Connect Telegram to list your channels and groups.
              </p>
            ) : (
              chats.map((chat) => (
                <button
                  key={chat.id}
                  type="button"
                  onClick={() => toggle(chat.id)}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[11px] text-muted-foreground transition-colors",
                    selected.has(chat.id) && "border-primary bg-primary/10 text-primary",
                  )}
                >
                  {chat.title}
                </button>
              ))
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
