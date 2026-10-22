import { useEffect, useState } from "react"
import {
  BadgeCheck,
  BarChart3,
  Download,
  Loader2,
  Package,
  TrendingUp,
  Users,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api, type Overview, type TrendRow } from "@/lib/api"

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

export function ReportsPage() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [scans, setScans] = useState<ScanRow[]>([])
  const [meta, setMeta] = useState<{
    configured: boolean
    products_checked: number
    active_ads_found: number
    failed_products?: number
    running: boolean
    progress_checked?: number
    progress_total?: number
    message?: string
  } | null>(null)
  const [identification, setIdentification] = useState<Record<string, unknown>>({})
  const [error, setError] = useState("")
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    api.overview().then(setOverview).catch((err) => setError(err instanceof Error ? err.message : String(err)))
    api.scans().then((payload) => setScans(payload.scans || [])).catch(() => {})
    api.metaAdsSummary().then((payload) => setMeta(payload.summary)).catch(() => {})
    api.identificationSummary().then((payload) => setIdentification(payload.summary || {})).catch(() => {})
  }, [])

  async function downloadCsv() {
    setExporting(true)
    setError("")
    try {
      const blob = await api.exportCsv()
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = "telegram-product-research.csv"
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  const metrics = overview?.metrics || {}
  const number = (key: string) => Number(metrics[key] ?? 0)

  const kpis = [
    { label: "Products tracked", value: number("products_total"), hint: "offers in this scan", icon: Package },
    { label: "Names confirmed", value: number("products_identified"), hint: "identification evidence", icon: BadgeCheck },
    { label: "Suppliers", value: number("suppliers_total"), hint: "active in this scan", icon: Users },
    { label: "Exact ads", value: number("ads_collected"), hint: "verified creative + text", icon: TrendingUp },
  ]

  const trending: TrendRow[] = overview?.trending || []
  const opportunities: TrendRow[] = overview?.opportunities || []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            System
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Reports</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {overview?.scan?.start_date
              ? `Scan ${overview.scan.start_date} → ${overview.scan.end_date || "today"}`
              : "No scan selected yet."}
          </p>
        </div>
        <Button onClick={() => void downloadCsv()} disabled={exporting}>
          {exporting ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : (
            <Download className="mr-2 size-4" />
          )}
          Export CSV
        </Button>
      </div>

      {error ? (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi) => {
          const Icon = kpi.icon
          return (
            <Card key={kpi.label}>
              <CardContent className="flex items-center justify-between pt-4">
                <div>
                  <p className="text-xs text-muted-foreground">{kpi.label}</p>
                  <p className="mt-1 text-2xl font-semibold tabular-nums">{kpi.value}</p>
                  <p className="text-[11px] text-muted-foreground">{kpi.hint}</p>
                </div>
                <Icon className="size-4 text-muted-foreground" />
              </CardContent>
            </Card>
          )
        })}
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">Meta ad library</CardTitle>
            <Badge variant="outline">
              {meta?.running ? "running" : meta?.configured ? "ready" : "not configured"}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-2">
            {meta?.running && meta.progress_total ? (
              <>
                <Progress
                  value={Math.round(
                    ((meta.progress_checked || 0) / meta.progress_total) * 100,
                  )}
                  className="h-1.5"
                />
                <p className="text-[11px] text-muted-foreground">
                  {meta.progress_checked || 0} of {meta.progress_total} products checked
                </p>
              </>
            ) : null}
            <p className="text-[12px] text-muted-foreground">
              {meta?.products_checked ?? 0} products looked up ·{" "}
              {meta?.active_ads_found ?? 0} active ads found
              {meta?.failed_products ? ` · ${meta.failed_products} failed` : ""}
            </p>
            {meta?.message ? (
              <p className="text-[11px] text-muted-foreground">{meta.message}</p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">Photo identification</CardTitle>
            <Badge variant="outline">
              {identification.running ? "running" : "idle"}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-[12px] text-muted-foreground">
              {Number(identification.checked ?? 0)} of {Number(identification.total ?? 0)} photos
              checked · {Number(identification.identified ?? 0)} named ·{" "}
              {Number(identification.unreadable ?? 0)} unreadable
            </p>
            {Number(identification.total ?? 0) ? (
              <Progress
                value={Math.round(
                  (Number(identification.checked ?? 0) / Number(identification.total ?? 0)) * 100,
                )}
                className="h-1.5"
              />
            ) : null}
            {identification.message ? (
              <p className="text-[11px] text-muted-foreground">{String(identification.message)}</p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scan history</CardTitle>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="scans">
            <TabsList>
              <TabsTrigger value="scans">Scans</TabsTrigger>
              <TabsTrigger value="trending">Trending</TabsTrigger>
              <TabsTrigger value="opportunities">Opportunities</TabsTrigger>
            </TabsList>
            <TabsContent value="scans" className="pt-3">
              {scans.length === 0 ? (
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
                            `${scan.start_date} → ${scan.end_date || "today"}`,
                            `${scan.chat_count || 0} channels`,
                            `${scan.scanned_messages || 0} messages`,
                            `${scan.products_found || 0} products`,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </div>
                      <Badge variant="outline" className="shrink-0 text-[10px] uppercase">
                        {scan.status}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </TabsContent>
            <TabsContent value="trending" className="pt-3">
              <RankedRows rows={trending} />
            </TabsContent>
            <TabsContent value="opportunities" className="pt-3">
              <RankedRows rows={opportunities} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>
    </div>
  )
}

function RankedRows({ rows }: { rows: TrendRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-dashed px-4 py-8 text-center">
        <BarChart3 className="mx-auto size-6 text-muted-foreground" />
        <p className="mt-2 text-sm font-medium">Nothing ranked yet</p>
        <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
          This list is built from real offers in the current scan.
        </p>
      </div>
    )
  }
  return (
    <div className="space-y-1.5">
      {rows.slice(0, 12).map((row) => (
        <div
          key={row.key}
          className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{row.product || row.key}</p>
            <p className="truncate text-[11px] text-muted-foreground">
              {[
                row.mentions != null ? `${row.mentions} posts` : "",
                row.sellers != null ? `${row.sellers} sellers` : "",
                row.why?.[0] || "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <span className="text-xs font-semibold tabular-nums">
            {Math.round(row.opportunity ?? 0)}
          </span>
        </div>
      ))}
    </div>
  )
}
