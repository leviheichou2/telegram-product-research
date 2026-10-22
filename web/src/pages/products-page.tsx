import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { RefreshCw, Search } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { api, money, type Product } from "@/lib/api"
import { ScanControls } from "@/components/scan-controls"

type Filter = "all" | "identified" | "review" | "researched"
type Sort = "priority" | "suppliers" | "newest" | "price" | "title"

const PAGE_SIZE = 50

/** The same filters and ordering the old dashboard had on this page. */
function applyFiltersAndSort(
  products: Product[],
  query: string,
  filter: Filter,
  sort: Sort,
): Product[] {
  const needle = query.trim().toLowerCase()
  let rows = products.filter((product) => {
    if (needle) {
      const haystack = [
        product.product || "",
        product.description || "",
        ...(product.offers || []).map((offer) => offer.supplier || ""),
      ]
        .join(" ")
        .toLowerCase()
      if (!haystack.includes(needle)) return false
    }
    if (filter === "identified") {
      return !product.candidate_only && Boolean(product.identification_method)
    }
    if (filter === "review") {
      return product.candidate_only || !product.identification_method
    }
    if (filter === "researched") {
      return Number(product.research_coverage || 0) > 0
    }
    return true
  })

  rows = rows.sort((a, b) => {
    if (sort === "priority") {
      const scoreA = a.candidate_only ? -1 : (a.score || 0)
      const scoreB = b.candidate_only ? -1 : (b.score || 0)
      return scoreB - scoreA
    }
    if (sort === "suppliers") {
      return (b.sellers || 0) - (a.sellers || 0)
    }
    if (sort === "newest") {
      return (b.posts || 0) - (a.posts || 0)
    }
    if (sort === "price") {
      const priceA = a.lowest_price != null ? a.lowest_price : Infinity
      const priceB = b.lowest_price != null ? b.lowest_price : Infinity
      return priceA - priceB
    }
    if (sort === "title") {
      return (a.product || "").localeCompare(b.product || "")
    }
    return 0
  })

  return rows
}

export function ProductsPage({ onOpenProduct }: { onOpenProduct: (key: string) => void }) {
  const [products, setProducts] = useState<Product[] | null>(null)
  const [query, setQuery] = useState("")
  const [filter, setFilter] = useState<Filter>("all")
  const [sort, setSort] = useState<Sort>("priority")
  const [page, setPage] = useState(0)
  const [error, setError] = useState("")
  const [scanning, setScanning] = useState(false)
  const wasRunning = useRef(false)
  const loading = useRef(false)
  const pendingReload = useRef(false)

  const load = useCallback(async () => {
    // One request at a time: the results are heavy while a scan is running.
    if (loading.current) return false
    loading.current = true
    try {
      const payload = await api.results()
      setProducts(payload.products || [])
      setError("")
      return true
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return false
    } finally {
      loading.current = false
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // Rows appear while the scan runs, and the table is refreshed once it ends.
  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const status = await api.status()
        if (cancelled) return
        const isRunning = Boolean(status.running)
        if (isRunning) {
          setScanning(true)
          wasRunning.current = true
          await load()
          return
        }
        if (wasRunning.current) {
          // The scan just finished: pull the finished results.
          setScanning(false)
          wasRunning.current = false
          pendingReload.current = true
        }
        if (pendingReload.current) {
          const done = await load()
          if (done) pendingReload.current = false
        }
      } catch {
        /* the server may be restarting; the next tick retries */
      }
    }
    void poll()
    const timer = window.setInterval(poll, 4000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [load])

  const filtered = useMemo(
    () => applyFiltersAndSort(products || [], query, filter, sort),
    [products, query, filter, sort],
  )

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const safePage = Math.min(page, pageCount - 1)
  const visible = filtered.slice(safePage * PAGE_SIZE, safePage * PAGE_SIZE + PAGE_SIZE)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Research tools
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {products === null ? "Loading…" : `${filtered.length} products`}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            Scan your channels for offer posts, then review the potential winners below.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {scanning ? (
            <Badge variant="outline" className="gap-1.5">
              <RefreshCw className="size-3 animate-spin" />
              scan running — results updating
            </Badge>
          ) : null}
          <Button size="sm" variant="outline" onClick={() => void load()}>
            <RefreshCw className="size-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      <ScanControls />

      <Card>
        <CardContent className="space-y-3 pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative w-full max-w-xs">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value)
                  setPage(0)
                }}
                placeholder="Search products, suppliers…"
                className="pl-8"
              />
            </div>
            <select
              value={filter}
              onChange={(event) => {
                setFilter(event.target.value as Filter)
                setPage(0)
              }}
              aria-label="Filter products"
              className="h-8 rounded-lg border bg-background px-2 text-sm"
            >
              <option value="all">All products</option>
              <option value="identified">Identified</option>
              <option value="review">Review offers</option>
              <option value="researched">Researched</option>
            </select>
            <select
              value={sort}
              onChange={(event) => {
                setSort(event.target.value as Sort)
                setPage(0)
              }}
              aria-label="Sort products"
              className="h-8 rounded-lg border bg-background px-2 text-sm"
            >
              <option value="priority">Priority</option>
              <option value="suppliers">Supplier count</option>
              <option value="newest">Most posts</option>
              <option value="price">Lowest listed price</option>
              <option value="title">Product title</option>
            </select>
            <span className="text-[11px] text-muted-foreground">
              {filtered.length} product{filtered.length === 1 ? "" : "s"}
            </span>
          </div>

          {products === null ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {products.length === 0
                ? "Run a scan above to see product opportunities here."
                : "Nothing matches that search."}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Potential</TableHead>
                  <TableHead className="text-right">Priority</TableHead>
                  <TableHead className="text-right">Suppliers</TableHead>
                  <TableHead className="text-right">Lowest price</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((row) => (
                  <TableRow key={row.key}>
                    <TableCell className="max-w-[320px]">
                      <div className="truncate font-medium">{row.product || row.key}</div>
                      <div className="truncate text-[11px] text-muted-foreground">{row.key}</div>
                    </TableCell>
                    <TableCell>
                      {row.candidate_only ? (
                        <Badge variant="outline" className="text-[10px] text-amber-600">
                          needs review
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="text-[10px]">
                          identified
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-medium">
                          {row.opportunity?.label || row.status_label || "—"}
                        </span>
                        {row.opportunity?.score != null ? (
                          <span className="text-[11px] tabular-nums text-muted-foreground">
                            {row.opportunity.score}
                          </span>
                        ) : null}
                      </div>
                      {row.opportunity?.reasons?.length ? (
                        <div className="truncate text-[10px] text-muted-foreground">
                          {row.opportunity.reasons[0]}
                        </div>
                      ) : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{row.score ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.sellers ?? 0}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {money(row.lowest_price, row.price_currency, row.price_unit)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="ghost" onClick={() => onOpenProduct(row.key)}>
                        Open
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {pageCount > 1 ? (
            <div className="flex items-center justify-end gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={safePage === 0}
                onClick={() => setPage(safePage - 1)}
              >
                Previous
              </Button>
              <span className="text-[11px] text-muted-foreground">
                Page {safePage + 1} of {pageCount}
              </span>
              <Button
                size="sm"
                variant="outline"
                disabled={safePage >= pageCount - 1}
                onClick={() => setPage(safePage + 1)}
              >
                Next
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  )
}
