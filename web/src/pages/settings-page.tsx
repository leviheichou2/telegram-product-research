import { useEffect, useState } from "react"
import {
  Bot,
  CalendarRange,
  Globe,
  Loader2,
  RotateCcw,
  Save,
  Search,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { api, type AppSettings } from "@/lib/api"

const DEFAULTS: AppSettings = {
  market_country: "United States",
  product_entity_label: "PRODUCT",
  product_ner_model: "Atheer174/Products_NER2",
  scan_lookback_days: 2,
  scan_category: "",
  auto_identify_photos: true,
  auto_meta_ads_lookup: true,
  auto_web_research: true,
}

function Toggle({
  id,
  checked,
  onChange,
  label,
  hint,
}: {
  id: string
  checked: boolean
  onChange: (value: boolean) => void
  label: string
  hint: string
}) {
  return (
    <label
      htmlFor={id}
      className="flex cursor-pointer items-start justify-between gap-4 rounded-lg border px-3 py-2.5"
    >
      <span className="min-w-0">
        <span className="block text-sm font-medium">{label}</span>
        <span className="block text-[11px] leading-relaxed text-muted-foreground">{hint}</span>
      </span>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 shrink-0 accent-primary"
      />
    </label>
  )
}

export function SettingsPage() {
  const [settings, setSettings] = useState<AppSettings>(DEFAULTS)
  const [markets, setMarkets] = useState<string[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [status, setStatus] = useState<{ tone: "ok" | "error"; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api
      .appSettings()
      .then((payload) => {
        setSettings({ ...DEFAULTS, ...payload.settings })
        setMarkets(payload.markets || [])
        setCategories(payload.categories || [])
      })
      .catch((err) =>
        setStatus({ tone: "error", text: err instanceof Error ? err.message : String(err) }),
      )
  }, [])

  function update<K extends keyof AppSettings>(key: K, value: AppSettings[K]) {
    setSettings((previous) => ({ ...previous, [key]: value }))
  }

  async function save() {
    setSaving(true)
    setStatus(null)
    try {
      const payload = await api.saveAppSettings(settings)
      setSettings({ ...DEFAULTS, ...payload.settings })
      setStatus({ tone: "ok", text: "Settings saved." })
    } catch (err) {
      setStatus({ tone: "error", text: err instanceof Error ? err.message : String(err) })
    } finally {
      setSaving(false)
    }
  }

  async function reset() {
    setSaving(true)
    setStatus(null)
    try {
      const payload = await api.saveAppSettings({ reset: true })
      setSettings({ ...DEFAULTS, ...payload.settings })
      setStatus({ tone: "ok", text: "Settings reset to the defaults." })
    } catch (err) {
      setStatus({ tone: "error", text: err instanceof Error ? err.message : String(err) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            System
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">App configuration</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            These change how the app behaves. Telegram keys and the Ad Library country live in the
            header buttons.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => void reset()} disabled={saving}>
            <RotateCcw className="mr-2 size-4" />
            Reset to defaults
          </Button>
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Save className="mr-2 size-4" />
            )}
            Save settings
          </Button>
        </div>
      </div>

      {status ? (
        <Card
          className={
            status.tone === "ok"
              ? "border-emerald-500/40 bg-emerald-500/5"
              : "border-destructive/40 bg-destructive/5"
          }
        >
          <CardContent className="py-4 text-sm">
            <span className={status.tone === "ok" ? "text-emerald-700" : "text-destructive"}>
              {status.text}
            </span>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <CalendarRange className="size-4 text-muted-foreground" />
            Scanning
          </CardTitle>
          <Badge variant="outline">used by the next scan</Badge>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="text-[10px] font-medium text-muted-foreground">
                Default look-back (days)
              </span>
              <Input
                type="number"
                min={1}
                max={365}
                value={settings.scan_lookback_days}
                onChange={(event) =>
                  update("scan_lookback_days", Number(event.target.value) || 1)
                }
              />
              <span className="block text-[11px] text-muted-foreground">
                The Scan page opens with this date window.
              </span>
            </label>
            <label className="space-y-1">
              <span className="text-[10px] font-medium text-muted-foreground">
                Default source category
              </span>
              <select
                value={settings.scan_category}
                onChange={(event) => update("scan_category", event.target.value)}
                className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
              >
                <option value="">All categories</option>
                {categories.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
              <span className="block text-[11px] text-muted-foreground">
                Pre-selected in the Scan page. Empty means every channel.
              </span>
            </label>
          </div>
          <label className="block space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">
              Default research market
            </span>
            <select
              value={settings.market_country}
              onChange={(event) => update("market_country", event.target.value)}
              className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
            >
              {(markets.length ? markets : DEFAULTS.market_country ? [DEFAULTS.market_country] : []).map(
                (item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ),
              )}
            </select>
            <span className="block text-[11px] text-muted-foreground">
              Pre-selected in the Scan page and stored on every new scan.
            </span>
          </label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Bot className="size-4 text-muted-foreground" />
            Product recognition
          </CardTitle>
          <Badge variant="outline">applies to the next scan</Badge>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="block space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">Entity label</span>
            <Input
              value={settings.product_entity_label}
              onChange={(event) => update("product_entity_label", event.target.value)}
              placeholder="PRODUCT"
            />
            <span className="block text-[11px] text-muted-foreground">
              The token the recognizer looks for in a post. Posts labelled differently are read as
              plain offers instead of named products.
            </span>
          </label>
          <label className="block space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">NER model</span>
            <Input
              value={settings.product_ner_model}
              onChange={(event) => update("product_ner_model", event.target.value)}
              placeholder="Atheer174/Products_NER2"
            />
            <span className="block text-[11px] text-muted-foreground">
              A local model folder or a Hugging Face model id. The model reloads on the next scan.
            </span>
          </label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Search className="size-4 text-muted-foreground" />
            Automatic work after a scan
          </CardTitle>
          <Badge variant="outline">background jobs</Badge>
        </CardHeader>
        <CardContent className="space-y-2">
          <Toggle
            id="auto-identify"
            checked={settings.auto_identify_photos}
            onChange={(value) => update("auto_identify_photos", value)}
            label="Name products from their photos"
            hint="Runs the vision model over every offer photo so unnamed posts get a name."
          />
          <Toggle
            id="auto-web-research"
            checked={settings.auto_web_research}
            onChange={(value) => update("auto_web_research", value)}
            label="Research each product on the web"
            hint="Looks up the supplier's own wording to confirm or correct the product name."
          />
          <Toggle
            id="auto-meta-ads"
            checked={settings.auto_meta_ads_lookup}
            onChange={(value) => update("auto_meta_ads_lookup", value)}
            label="Look products up in the Meta Ad Library"
            hint="Checks whether a product is being advertised, and pulls the active ads."
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Globe className="size-4 text-muted-foreground" />
            Where these live
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-[12px] text-muted-foreground">
          <p>
            App configuration is stored in <code className="text-[11px]">data/app_settings.json</code>{" "}
            next to the database. Telegram keys and the Ad Library country are stored separately and
            encrypted.
          </p>
          <p>
            Turning the background jobs off does not cancel anything already running; it only stops
            the next one from starting.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
