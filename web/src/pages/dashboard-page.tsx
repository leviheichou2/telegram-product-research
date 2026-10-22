import { useEffect, useState } from "react"
import { ArrowUpRight, Flame, Package, ShieldCheck, TrendingUp, Users } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { BlurFade } from "@/components/ui/blur-fade"
import { NumberTicker } from "@/components/ui/number-ticker"
import { SpotlightCard } from "@/components/reactbits/spotlight-card"
import { api, percent, type Overview, type TrendRow } from "@/lib/api"

function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed px-6 py-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-muted-foreground">{body}</p>
    </div>
  )
}

function ProductRows({
  rows,
  onOpenProduct,
  metric,
}: {
  rows: TrendRow[]
  onOpenProduct: (key: string) => void
  metric: "growth" | "opportunity" | "crowded"
}) {
  const max = Math.max(1, ...rows.map((row) => Math.abs(row.opportunity ?? 0)))
  return (
    <div className="divide-y">
      {rows.map((row) => (
        <div key={row.key} className="flex items-center gap-3 py-2.5">
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium">{row.product || row.key}</p>
            <p className="truncate text-[11px] text-muted-foreground">
              {[
                row.mentions != null ? `${row.mentions} posts` : "",
                row.sellers != null ? `${row.sellers} sellers` : "",
                row.exact_ads != null ? `${row.exact_ads} exact ads` : "",
                row.why?.[0] || "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          <div className="hidden w-28 sm:block">
            <Progress value={Math.round(((row.opportunity ?? 0) / max) * 100)} className="h-1.5" />
          </div>
          <div className="w-20 text-right text-xs font-semibold tabular-nums">
            {metric === "growth"
              ? percent(row.growth_rate)
              : metric === "crowded"
                ? `${row.sellers ?? 0} sellers`
                : Math.round(row.opportunity ?? 0)}
          </div>
          <Button size="sm" variant="ghost" onClick={() => onOpenProduct(row.key)}>
            Open
            <ArrowUpRight className="size-3.5" />
          </Button>
        </div>
      ))}
    </div>
  )
}

export function DashboardPage({ onOpenProduct }: { onOpenProduct: (key: string) => void }) {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    api
      .overview()
      .then(setOverview)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  const metrics = overview?.metrics || {}
  const number = (key: string) => Number(metrics[key] ?? 0)

  const trending = [...(overview?.trending || [])]
  const growing = [...trending]
    .filter((row) => (row.growth_rate ?? 0) > 0)
    .sort((a, b) => (b.growth_rate ?? 0) - (a.growth_rate ?? 0))
  const opportunities = [...(overview?.opportunities || [])].sort(
    (a, b) => (b.opportunity ?? 0) - (a.opportunity ?? 0),
  )
  // Crowded = many sellers, no growth: the opposite question to "what should I
  // buy", and the reason it gets its own list instead of a warning icon.
  const crowded = [...opportunities]
    .filter((row) => (row.sellers ?? 0) >= 3 && (row.growth_rate ?? 0) <= 0)
    .sort((a, b) => (b.sellers ?? 0) - (a.sellers ?? 0))
  const suppliers = overview?.suppliers?.top || []
  const sources = overview?.sources || []

  const kpis = [
    { label: "Products tracked", value: number("products_total"), hint: "offers in this scan", icon: Package },
    { label: "Names confirmed", value: number("products_identified"), hint: "identification evidence", icon: ShieldCheck },
    { label: "Suppliers", value: number("suppliers_total"), hint: "active in this scan", icon: Users },
    { label: "Exact ads", value: number("ads_collected"), hint: "verified creative + text", icon: TrendingUp },
  ]

  if (error) {
    return (
      <Card className="border-destructive/40 bg-destructive/5">
        <CardContent className="py-6 text-sm text-destructive">{error}</CardContent>
      </Card>
    )
  }

  if (overview === null) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-9 w-64" />
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} className="h-28 w-full" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  const empty = !metrics || number("products_total") === 0

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Overview
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">What deserves attention</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            {overview.scan?.start_date
              ? `Scan ${overview.scan.start_date} → ${overview.scan.end_date || "today"}`
              : overview.empty_state || "No scan selected yet."}
          </p>
        </div>
        <div className="flex gap-2">
          <Badge variant="outline">{number("scans_completed")} scans completed</Badge>
          {number("review_needed") > 0 ? (
            <Badge variant="outline" className="text-amber-700">
              {number("review_needed")} need review
            </Badge>
          ) : null}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {kpis.map((kpi, index) => {
          const Icon = kpi.icon
          return (
            <BlurFade key={kpi.label} delay={index * 0.06}>
              <SpotlightCard className="p-0">
                <div className="flex items-start justify-between gap-3 p-5 pb-3">
                  <p className="text-sm font-medium text-muted-foreground">{kpi.label}</p>
                  <Icon className="size-4 text-muted-foreground" />
                </div>
                <div className="px-5 pb-5">
                  <p className="text-3xl font-semibold tracking-tight">
                    <NumberTicker value={kpi.value} />
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{kpi.hint}</p>
                </div>
              </SpotlightCard>
            </BlurFade>
          )
        })}
      </div>

      {empty ? (
        <Card>
          <CardContent className="pt-6">
            <Empty
              title="No products in this scan yet"
              body="Run a Telegram scan, or pick a previous scan. Everything on this page is derived from real offers — nothing is shown until there is data behind it."
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Flame className="size-4 text-muted-foreground" />
                  Trending now
                </CardTitle>
                <Badge variant="outline">{trending.length}</Badge>
              </CardHeader>
              <CardContent>
                {trending.length ? (
                  <ProductRows rows={trending} onOpenProduct={onOpenProduct} metric="opportunity" />
                ) : (
                  <Empty title="No trend signal" body="Products need more than one post before a trend can be measured." />
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-base">
                  <ArrowUpRight className="size-4 text-muted-foreground" />
                  Growing fastest
                </CardTitle>
                <Badge variant="outline">{growing.length}</Badge>
              </CardHeader>
              <CardContent>
                {growing.length ? (
                  <ProductRows rows={growing.slice(0, 8)} onOpenProduct={onOpenProduct} metric="growth" />
                ) : (
                  <Empty
                    title="Nothing is accelerating"
                    body="No product posted more in the second half of this window than the first. That is a real answer, not a missing one."
                  />
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Opportunity vs. saturation</CardTitle>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="opportunity">
                <TabsList>
                  <TabsTrigger value="opportunity">Best opportunity</TabsTrigger>
                  <TabsTrigger value="crowded">Crowded</TabsTrigger>
                  <TabsTrigger value="categories">By category</TabsTrigger>
                </TabsList>
                <TabsContent value="opportunity" className="pt-2">
                  {opportunities.length ? (
                    <ProductRows
                      rows={opportunities.slice(0, 8)}
                      onOpenProduct={onOpenProduct}
                      metric="opportunity"
                    />
                  ) : (
                    <Empty title="No scored products" body="Opportunity needs prices, sellers and engagement to be scored." />
                  )}
                </TabsContent>
                <TabsContent value="crowded" className="pt-2">
                  {crowded.length ? (
                    <ProductRows
                      rows={crowded.slice(0, 8)}
                      onOpenProduct={onOpenProduct}
                      metric="crowded"
                    />
                  ) : (
                    <Empty
                      title="Nothing looks oversaturated"
                      body="No product in this scan combines three or more sellers with flat or falling demand."
                    />
                  )}
                </TabsContent>
                <TabsContent value="categories" className="pt-2">
                  {(overview.hot_by_category || []).length ? (
                    <div className="space-y-3">
                      {overview.hot_by_category.map((group) => (
                        <div key={group.category}>
                          <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                            {group.category || "Uncategorised"}
                          </p>
                          <ProductRows
                            rows={group.products}
                            onOpenProduct={onOpenProduct}
                            metric="opportunity"
                          />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty title="No categories yet" body="Categories come from the scanned channels." />
                  )}
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-base">Suppliers that matter</CardTitle>
                <Badge variant="outline">{suppliers.length}</Badge>
              </CardHeader>
              <CardContent className="space-y-2">
                {suppliers.length ? (
                  suppliers.slice(0, 8).map((row, index) => (
                    <div
                      key={`${row.supplier}-${index}`}
                      className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{row.supplier}</p>
                        <p className="truncate text-[11px] text-muted-foreground">
                          {[
                            row.posts != null ? `${row.posts} posts` : "",
                            row.product_count != null ? `${row.product_count} products` : "",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </div>
                      <span className="text-xs font-semibold tabular-nums">
                        {percent(row.growth_rate)}
                      </span>
                    </div>
                  ))
                ) : (
                  <Empty title="No suppliers yet" body="Suppliers appear once offers carry a seller name." />
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-base">Sources that matter</CardTitle>
                <Badge variant="outline">{sources.length}</Badge>
              </CardHeader>
              <CardContent className="space-y-2">
                {sources.length ? (
                  sources.slice(0, 8).map((row, index) => (
                    <div
                      key={`${row.source_key}-${index}`}
                      className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium">{row.title || row.source_key}</p>
                        <p className="truncate text-[11px] text-muted-foreground">
                          {[
                            row.category || "",
                            row.products_found != null ? `${row.products_found} products` : "",
                            row.ads_found ? `${row.ads_found} ads` : "",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </p>
                      </div>
                      <span className="text-xs font-semibold tabular-nums">
                        {row.source_score != null ? Math.round(row.source_score) : "—"}
                      </span>
                    </div>
                  ))
                ) : (
                  <Empty
                    title="No source leaderboard"
                    body="It is built from completed scans of this window."
                  />
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
