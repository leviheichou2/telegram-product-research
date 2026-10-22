import { useCallback, useEffect, useRef, useState } from "react"
import {
  BadgeCheck,
  Camera,
  CircleSlash,
  ImageOff,
  Loader2,
  ScanLine,
  ShieldCheck,
  Sparkles,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"
import { ShimmerButton } from "@/components/ui/shimmer-button"
import { BlurFade } from "@/components/ui/blur-fade"
import { CountUp } from "@/components/reactbits/count-up"
import { SpotlightCard } from "@/components/reactbits/spotlight-card"
import {
  api,
  money,
  type Ad,
  type AdVisualCheck,
  type Product,
  type VisionLayer,
  type VisionResult,
  type VisionStatus,
} from "@/lib/api"
import { cn } from "@/lib/utils"

const LAYERS: [string, string][] = [
  ["visual", "Visual (DINOv2)"],
  ["ocr", "OCR text"],
  ["brand", "Brand"],
  ["model", "Model number"],
  ["packaging", "Packaging"],
  ["supplier", "Supplier data"],
]

const VERDICT_STYLES: Record<string, string> = {
  exact: "bg-emerald-50 text-emerald-700 border-emerald-200",
  likely: "bg-sky-50 text-sky-700 border-sky-200",
  possible: "bg-amber-50 text-amber-700 border-amber-200",
  unknown: "bg-muted text-muted-foreground",
}

function LayerBars({ layers }: { layers: VisionLayer[] }) {
  const byName = new Map(layers.map((layer) => [layer.layer, layer]))
  return (
    <div className="space-y-2.5">
      {LAYERS.map(([name, label]) => {
        const layer = byName.get(name)
        const available = layer?.available ?? false
        const pct = Math.round(Math.max(0, Math.min(1, layer?.score ?? 0)) * 100)
        return (
          <div key={name} className={cn("space-y-1", !available && "opacity-45")}>
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-xs font-medium">{label}</span>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {available ? `${pct}%` : "not available"}
              </span>
            </div>
            <Progress value={available ? pct : 0} className="h-1.5" />
            {layer?.detail ? (
              <p className="truncate text-[11px] text-muted-foreground">{layer.detail}</p>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

function VisionPanel({ productKey }: { productKey: string }) {
  const [status, setStatus] = useState<VisionStatus | null>(null)
  const [result, setResult] = useState<VisionResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.visionStatus().then(setStatus).catch(() => setStatus(null))
  }, [])

  const send = useCallback(
    async (file: File) => {
      setBusy(true)
      setError("")
      try {
        setResult(await api.identify(file, productKey))
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      } finally {
        setBusy(false)
      }
    },
    [productKey],
  )

  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      const items = event.clipboardData?.items
      if (!items) return
      for (const item of Array.from(items)) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile()
          if (file) {
            event.preventDefault()
            void send(file)
            return
          }
        }
      }
    }
    window.addEventListener("paste", onPaste)
    return () => window.removeEventListener("paste", onPaste)
  }, [send])

  const badges = [
    {
      label: status?.visual_backend === "dinov2" ? "DINOv2" : status?.visual_backend === "clip" ? "CLIP fallback" : "No model",
      on: status?.visual_backend === "dinov2",
    },
    { label: status?.vector_backend === "faiss" ? "FAISS" : "Exact search", on: status?.vector_backend === "faiss" },
    { label: status?.ocr ? "OCR ready" : "OCR off", on: Boolean(status?.ocr) },
  ]

  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Visual identification
          </p>
          <CardTitle className="mt-1 text-base">Read this product from a photo</CardTitle>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
            The photo is embedded with DINOv2, retrieved with FAISS, then corroborated by OCR text,
            brand, model number and packaging. A layer that cannot run is skipped, never scored zero.
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {badges.map((badge) => (
            <Badge key={badge.label} variant="outline" className="gap-1.5 text-[11px] font-semibold">
              <span
                className={cn(
                  "size-1.5 rounded-full",
                  badge.on ? "bg-emerald-500" : "bg-muted-foreground/50",
                )}
              />
              {badge.label}
            </Badge>
          ))}
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 md:grid-cols-[260px_minmax(0,1fr)]">
        <div className="space-y-2">
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault()
              setDragging(false)
              const file = event.dataTransfer.files?.[0]
              if (file) void send(file)
            }}
            disabled={busy}
            className={cn(
              "flex min-h-[140px] w-full flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-4 py-6 text-center transition-colors",
              "hover:border-primary/60 hover:bg-primary/5 disabled:opacity-60",
              dragging && "border-primary bg-primary/5",
            )}
          >
            {busy ? (
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            ) : (
              <Camera className="size-5 text-muted-foreground" />
            )}
            <span className="text-sm font-medium">
              {busy ? "Reading the photo…" : "Drop a product photo"}
            </span>
            <span className="text-[11px] text-muted-foreground">
              or click to choose · Ctrl+V to paste
            </span>
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void send(file)
              event.target.value = ""
            }}
          />
          {status ? (
            <p className="text-[11px] text-muted-foreground">
              {status.indexed_photos.toLocaleString()} indexed photos · index capped at 150 products
            </p>
          ) : null}
        </div>

        <div className="min-w-0">
          {error ? (
            <div className="rounded-xl border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {error}
            </div>
          ) : null}

          {!result && !error ? (
            <div className="flex h-full min-h-[140px] flex-col justify-center rounded-xl border border-dashed px-5 py-6">
              <p className="text-sm font-medium">Nothing read yet</p>
              <p className="mt-1 max-w-md text-xs leading-relaxed text-muted-foreground">
                Drop a supplier photo, a packaging shot or an ad creative. The reading is checked
                against this product&apos;s stored photos, and its suppliers are ranked by confidence.
              </p>
            </div>
          ) : null}

          {result ? (
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={cn(
                    "rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide",
                    VERDICT_STYLES[result.verdict] || VERDICT_STYLES.unknown,
                  )}
                >
                  {result.verdict}
                </span>
                <span className="text-sm font-semibold">
                  {result.product_name || "No candidate product"}
                </span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {Math.round((result.confidence || 0) * 100)}% · {result.elapsed_ms ?? 0} ms
                </span>
              </div>

              {result.brand || result.models?.length || result.packaging?.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {result.brand ? (
                    <Badge variant="secondary" className="text-[11px]">
                      brand · {result.brand}
                    </Badge>
                  ) : null}
                  {result.models?.map((model) => (
                    <Badge key={model} variant="secondary" className="text-[11px]">
                      model · {model}
                    </Badge>
                  ))}
                  {result.packaging?.map((pack) => (
                    <Badge key={pack} variant="secondary" className="text-[11px]">
                      pack · {pack}
                    </Badge>
                  ))}
                </div>
              ) : null}

              {result.ocr_text ? (
                <pre className="overflow-x-auto rounded-xl bg-zinc-950 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-zinc-200">
                  {result.ocr_text}
                </pre>
              ) : null}

              <LayerBars layers={result.layers || result.alternatives?.[0]?.layers || []} />

              {(result.supplier_tiers || result.suppliers || []).length ? (
                <>
                  <Separator />
                  <div>
                    <p className="mb-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                      Suppliers by confidence
                    </p>
                    <div className="space-y-1.5">
                      {(result.supplier_tiers || result.suppliers || []).slice(0, 5).map((row, index) => (
                        <div
                          key={`${row.supplier}-${index}`}
                          className="flex items-center justify-between gap-3 rounded-lg border px-3 py-1.5"
                        >
                          <div className="min-w-0">
                            <p className="truncate text-xs font-semibold">{row.supplier}</p>
                            <p className="truncate text-[11px] text-muted-foreground">
                              {[row.phone, row.whatsapp, row.city].filter(Boolean).join(" · ") ||
                                row.category_sheet ||
                                "No contact recorded"}
                            </p>
                          </div>
                          <Badge variant="outline" className="text-[10px] uppercase">
                            {row.tier_label || row.tier || "similar"}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}

function AdCard({ ad }: { ad: Ad }) {
  const image = ad.image_url || ""
  const title = ad.creative_title || ad.page_name || "Untitled ad"
  const body = ad.creative_text || ad.link_url || ""
  return (
    <div className="flex gap-3 rounded-xl border p-2.5">
      <div className="flex size-16 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-muted">
        {image ? (
          <img src={image} alt="" loading="lazy" className="size-full object-cover" />
        ) : (
          <ImageOff className="size-5 text-muted-foreground" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <p className="truncate text-xs font-semibold">{title}</p>
          {ad.visual_score != null ? (
            <span
              className={cn(
                "shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold tabular-nums",
                ad.visual_score >= 0.78
                  ? "bg-emerald-50 text-emerald-700"
                  : "bg-amber-50 text-amber-700",
              )}
              title="DINOv2 similarity between this creative and the product photos"
            >
              img {Math.round(ad.visual_score * 100)}%
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
          {body || "No creative text stored."}
        </p>
        {ad.match?.reasons?.[0] ? (
          <p className="mt-1 truncate text-[10px] text-muted-foreground/80">{ad.match.reasons[0]}</p>
        ) : null}
      </div>
    </div>
  )
}

function AdVerification({ productKey, onChecked }: { productKey: string; onChecked: () => void }) {
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<AdVisualCheck | null>(null)
  const [error, setError] = useState("")

  async function run() {
    setBusy(true)
    setError("")
    try {
      const payload = await api.verifyAdVisuals(productKey)
      setReport(payload)
      if (payload.checked > 0) onChecked()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <Button size="sm" variant="outline" onClick={() => void run()} disabled={busy}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
        Verify ad creatives
      </Button>
      {error ? <p className="text-[11px] text-destructive">{error}</p> : null}
      {report ? (
        <p className="text-[11px] text-muted-foreground">
          {report.reason ||
            `${report.checked} creative(s) checked, ${report.skipped} unreadable · exact requires ≥ ${Math.round(
              (report.threshold ?? 0.78) * 100,
            )}% visual similarity`}
        </p>
      ) : null}
    </div>
  )
}

export function ProductPage({ productKey }: { productKey: string }) {
  const [product, setProduct] = useState<Product | null>(null)
  const [error, setError] = useState("")
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    if (!productKey) {
      setProduct(null)
      return
    }
    api
      .product(productKey)
      .then((payload) => setProduct(payload.product))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [productKey, revision])

  if (!productKey) {
    return (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-2 py-14 text-center">
          <CircleSlash className="size-6 text-muted-foreground" />
          <p className="text-sm font-medium">No product selected</p>
          <p className="max-w-sm text-xs text-muted-foreground">
            Open a product from the Products page to see its evidence, exact ads, suppliers and the
            visual identification panel.
          </p>
        </CardContent>
      </Card>
    )
  }

  if (error) {
    return (
      <Card className="border-destructive/40 bg-destructive/5">
        <CardContent className="py-6 text-sm text-destructive">{error}</CardContent>
      </Card>
    )
  }

  const confidence = Math.round((product?.confidence ?? 0) * 100)
  const exactAds: Ad[] = product?.exact_meta_ads ?? product?.active_meta_ads ?? []
  const similarAds: Ad[] = product?.similar_meta_ads ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Product profile
          </p>
          <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight">
            {product?.product || productKey}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            Key {productKey}
            {product?.candidate_only ? " · unverified title from the offer post" : ""}
          </p>
        </div>
        <ShimmerButton className="h-9 px-4 text-sm" onClick={() => setRevision((value) => value + 1)}>
          <ScanLine className="mr-2 size-4" />
          Refresh profile
        </ShimmerButton>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { label: "Confidence", value: confidence, suffix: "%" },
          { label: "Posts", value: product?.posts ?? 0 },
          { label: "Suppliers", value: product?.sellers ?? 0 },
          { label: "Exact ads", value: (product?.active_meta_ads || []).length },
        ].map((metric, index) => (
          <BlurFade key={metric.label} delay={index * 0.05}>
            <SpotlightCard className="p-0">
              <div className="p-4">
                <p className="text-xs font-medium text-muted-foreground">{metric.label}</p>
                <p className="mt-1 text-2xl font-semibold tracking-tight">
                  <CountUp to={metric.value} suffix={metric.suffix || ""} />
                </p>
              </div>
            </SpotlightCard>
          </BlurFade>
        ))}
      </div>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="space-y-3">
          <VisionPanel productKey={productKey} />

          <Card>
            <CardHeader className="flex-row items-center justify-between gap-2">
              <CardTitle className="text-base">Exact product ads</CardTitle>
              <Badge variant="outline">{exactAds.length} ads</Badge>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-muted-foreground">
                An ad lands here only when its own text names this product — its model, brand and
                variant — and its creative image agrees. Anything uncertain goes to similar ads
                instead.
              </p>
              <AdVerification productKey={productKey} onChecked={() => setRevision((v) => v + 1)} />
              {exactAds.length ? (
                <div className="space-y-2">
                  {exactAds.slice(0, 6).map((ad, index) => (
                    <AdCard key={ad.ad_id || index} ad={ad} />
                  ))}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed px-4 py-6 text-center">
                  <p className="text-xs font-medium">No exact ads yet</p>
                  <p className="mx-auto mt-1 max-w-sm text-[11px] text-muted-foreground">
                    Run the Meta Ad Library lookup for this product. Ads that name it but look
                    different are kept in the similar section below.
                  </p>
                </div>
              )}
              {similarAds.length ? (
                <details className="rounded-lg border px-3 py-2">
                  <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {similarAds.length} similar product ads
                  </summary>
                  <div className="mt-2 space-y-2">
                    {similarAds.slice(0, 4).map((ad, index) => (
                      <AdCard key={ad.ad_id || `s-${index}`} ad={ad} />
                    ))}
                  </div>
                </details>
              ) : null}
            </CardContent>
          </Card>
        </div>

        <div className="space-y-3">
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="text-sm">Price</CardTitle>
              <BadgeCheck className="size-4 text-muted-foreground" />
            </CardHeader>
            <CardContent className="space-y-1">
              <p className="text-xl font-semibold">
                {money(product?.lowest_price, product?.price_currency, product?.price_unit)}
              </p>
              <p className="text-[11px] text-muted-foreground">lowest supplier price in this scan</p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-sm">
                <Sparkles className="size-4 text-muted-foreground" />
                Directory suppliers
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {(product?.directory_suppliers || []).length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  No wholesaler in the guide matches this product yet.
                </p>
              ) : (
                (product?.directory_suppliers || []).slice(0, 6).map((row, index) => (
                  <div key={`${row.supplier}-${index}`} className="rounded-lg border px-3 py-2">
                    <p className="truncate text-xs font-semibold">{row.supplier}</p>
                    <p className="truncate text-[11px] text-muted-foreground">
                      {[row.phone, row.city].filter(Boolean).join(" · ") || row.category_sheet}
                    </p>
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
