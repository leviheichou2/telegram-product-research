import { useEffect, useState } from "react"
import {
  Calendar,
  CheckCircle2,
  Loader2,
  MessageSquare,
  RefreshCw,
  Sparkles,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { ScanControls } from "@/components/scan-controls"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

type ScanRow = {
  scan_id: string
  name: string
  start_date: string
  end_date: string
  market: string
  status: string
  chat_count: number
  scanned_messages: number
  products_found: number
  completed_at: string | null
}

const STATUS_STYLE: Record<string, string> = {
  completed: "bg-emerald-50 text-emerald-700 border-emerald-200",
  running: "bg-sky-50 text-sky-700 border-sky-200",
  failed: "bg-red-50 text-red-700 border-red-200",
  cancelled: "bg-amber-50 text-amber-700 border-amber-200",
}

export function ScanPage() {
  const [scans, setScans] = useState<ScanRow[] | null>(null)
  const [state, setState] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [scanPayload, statusPayload] = await Promise.all([
          api.scans(),
          api.status(),
        ])
        if (cancelled) return
        setScans(scanPayload.scans || [])
        setState(statusPayload)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
    const timer = window.setInterval(load, 4000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const running = Boolean(state?.running)
  const scanned = Number(state?.scanned_messages ?? 0)
  const found = Number(state?.products_found ?? 0)
  const detected = Number(state?.products_detected ?? 0)
  const chatIndex = Number(state?.chat_index ?? 0)
  const chatTotal = Number(state?.chat_total ?? 0)
  const message = String(state?.message ?? "")
  const jobKind = String(state?.job_kind ?? "")
  const progress = chatTotal ? Math.round((chatIndex / chatTotal) * 100) : 0

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Research tools
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Scan</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            What the collector is doing right now, and every scan it has finished.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
          <RefreshCw className="size-3.5" />
          Refresh
        </Button>
      </div>

      {error ? (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      <ScanControls />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "Messages scanned", value: scanned, icon: MessageSquare },
          { label: "Products found", value: found, icon: CheckCircle2 },
          { label: "Names detected", value: detected, icon: Sparkles },
          { label: "Channels in this scan", value: chatTotal, icon: Calendar },
        ].map((kpi) => {
          const Icon = kpi.icon
          return (
            <Card key={kpi.label}>
              <CardContent className="flex items-center justify-between pt-4">
                <div>
                  <p className="text-xs text-muted-foreground">{kpi.label}</p>
                  <p className="mt-1 text-2xl font-semibold tabular-nums">{kpi.value}</p>
                </div>
                <Icon className="size-4 text-muted-foreground" />
              </CardContent>
            </Card>
          )
        })}
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-base">Current job</CardTitle>
          {running ? (
            <Badge variant="outline" className="gap-1.5">
              <Loader2 className="size-3 animate-spin" />
              {jobKind || "running"}
            </Badge>
          ) : (
            <Badge variant="outline">idle</Badge>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm">{message || "Ready."}</p>
          {running && chatTotal ? (
            <>
              <Progress value={progress} className="h-1.5" />
              <p className="text-[11px] text-muted-foreground">
                Channel {chatIndex} of {chatTotal} · {progress}%
              </p>
            </>
          ) : null}
          {state?.cancel_requested ? (
            <p className="text-[11px] text-amber-600">
              A stop was requested — the current message finishes first, then results are saved.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-base">Scan history</CardTitle>
          <Badge variant="outline">{scans?.length ?? 0}</Badge>
        </CardHeader>
        <CardContent>
          {scans === null ? (
            <div className="space-y-2">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : scans.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-foreground">
              No scans recorded yet.
            </p>
          ) : (
            <div className="space-y-1.5">
              {scans.map((scan) => (
                <div
                  key={scan.scan_id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {scan.name || `Scan ${scan.scan_id.slice(0, 8)}`}
                    </p>
                    <p className="truncate text-[11px] text-muted-foreground">
                      {[
                        scan.start_date ? `${scan.start_date} → ${scan.end_date || "today"}` : "",
                        scan.market,
                        `${scan.chat_count || 0} channels`,
                        `${scan.scanned_messages || 0} messages`,
                        `${scan.products_found || 0} products`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase",
                      STATUS_STYLE[scan.status] || "bg-muted text-muted-foreground",
                    )}
                  >
                    {scan.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
