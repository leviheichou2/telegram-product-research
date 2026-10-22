import { useEffect, useState } from "react"
import { Loader2, RefreshCw, Save, Send, Stethoscope } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { api } from "@/lib/api"

type Output = { tone: "ok" | "error"; text: string } | null

function OutputLine({ output }: { output: Output }) {
  if (!output) return null
  return (
    <p
      className={
        output.tone === "ok"
          ? "rounded-lg bg-emerald-500/10 px-3 py-2 text-[12px] text-emerald-700"
          : "rounded-lg bg-destructive/10 px-3 py-2 text-[12px] text-destructive"
      }
    >
      {output.text}
    </p>
  )
}

/** Telegram only: the API credentials and a way to reload the chat list. */
export function TelegramDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [apiId, setApiId] = useState("")
  const [apiHash, setApiHash] = useState("")
  const [busy, setBusy] = useState("")
  const [output, setOutput] = useState<Output>(null)

  useEffect(() => {
    if (open) setOutput(null)
  }, [open])

  async function saveCredentials() {
    setBusy("save")
    setOutput(null)
    try {
      await api.saveSettings({ telegram_api_id: apiId, telegram_api_hash: apiHash })
      setApiId("")
      setApiHash("")
      setOutput({
        tone: "ok",
        text: "Saved. Use Refresh chats to reconnect with the new credentials.",
      })
    } catch (error) {
      setOutput({ tone: "error", text: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy("")
    }
  }

  async function refreshChats() {
    setBusy("refresh")
    setOutput(null)
    try {
      await api.reloadChats()
      setOutput({ tone: "ok", text: "Reloading your channels and groups…" })
    } catch (error) {
      setOutput({ tone: "error", text: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy("")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect Telegram</DialogTitle>
          <DialogDescription>
            Use your own API ID and hash from my.telegram.org. They are stored only on this
            computer.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">API ID</span>
            <Input
              value={apiId}
              onChange={(event) => setApiId(event.target.value)}
              placeholder="1234567"
              inputMode="numeric"
            />
          </label>
          <label className="space-y-1">
            <span className="text-[10px] font-medium text-muted-foreground">API hash</span>
            <Input
              value={apiHash}
              onChange={(event) => setApiHash(event.target.value)}
              placeholder="32 hex characters"
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() => void saveCredentials()}
            disabled={busy === "save" || !apiId || !apiHash}
          >
            {busy === "save" ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Save className="mr-2 size-4" />
            )}
            Save credentials
          </Button>
          <Button variant="outline" onClick={() => void refreshChats()} disabled={busy === "refresh"}>
            {busy === "refresh" ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-2 size-4" />
            )}
            Refresh chats
          </Button>
        </div>

        <OutputLine output={output} />
      </DialogContent>
    </Dialog>
  )
}

/** Meta Ad Library only: the country and a live connection test. */
export function MetaAdsDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [countries, setCountries] = useState<{ code: string; name: string }[]>([])
  const [country, setCountry] = useState("")
  const [busy, setBusy] = useState("")
  const [output, setOutput] = useState<Output>(null)

  useEffect(() => {
    if (!open) return
    setOutput(null)
    api
      .settings()
      .then((payload) => {
        setCountries(payload.meta_ad_countries || [])
        setCountry(payload.meta_ad_country || "")
      })
      .catch(() => {})
  }, [open])

  async function testMeta() {
    setBusy("test")
    setOutput(null)
    try {
      const payload = await api.testMetaAds()
      setOutput({
        tone: "ok",
        text: payload.message || `Connected. ${payload.matching_ads} matching ads returned.`,
      })
    } catch (error) {
      setOutput({ tone: "error", text: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy("")
    }
  }

  async function diagnoseMeta() {
    setBusy("diagnose")
    setOutput(null)
    try {
      const payload = await api.diagnoseMetaAds()
      setOutput({
        tone: payload.category === "ok" ? "ok" : "error",
        text: [payload.message, payload.detail].filter(Boolean).join(" — "),
      })
    } catch (error) {
      setOutput({ tone: "error", text: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy("")
    }
  }

  async function saveCountry() {
    setBusy("country")
    setOutput(null)
    try {
      await api.saveSettings({ meta_ad_country: country })
      setOutput({ tone: "ok", text: "Ad Library country saved." })
    } catch (error) {
      setOutput({ tone: "error", text: error instanceof Error ? error.message : String(error) })
    } finally {
      setBusy("")
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Meta Ad Library</DialogTitle>
          <DialogDescription>
            No access token or app review is needed. The Ad Library is queried by the local
            scraper package.
          </DialogDescription>
        </DialogHeader>

        <label className="block space-y-1">
          <span className="text-[10px] font-medium text-muted-foreground">Ad Library country</span>
          <select
            value={country}
            onChange={(event) => setCountry(event.target.value)}
            className="h-8 w-full rounded-lg border bg-background px-2 text-sm"
          >
            {countries.length === 0 ? <option value="">Loading…</option> : null}
            {countries.map((item) => (
              <option key={item.code} value={item.code}>
                {item.name} ({item.code})
              </option>
            ))}
          </select>
        </label>

        <div className="flex flex-wrap gap-2">
          <Button onClick={() => void testMeta()} disabled={busy === "test"}>
            {busy === "test" ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Send className="mr-2 size-4" />
            )}
            Test connection
          </Button>
          <Button variant="outline" onClick={() => void diagnoseMeta()} disabled={busy === "diagnose"}>
            {busy === "diagnose" ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Stethoscope className="mr-2 size-4" />
            )}
            Diagnose
          </Button>
          <Button
            variant="outline"
            onClick={() => void saveCountry()}
            disabled={busy === "country" || !country}
          >
            {busy === "country" ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Save className="mr-2 size-4" />
            )}
            Save country
          </Button>
        </div>

        <OutputLine output={output} />
      </DialogContent>
    </Dialog>
  )
}
