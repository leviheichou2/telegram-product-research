import { useEffect, useRef, useState } from "react"
import { Camera, ImageIcon, Loader2, RefreshCw, Search, Send } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Progress } from "@/components/ui/progress"
import { api, money, type Chat, type ImageMatch } from "@/lib/api"
import { cn } from "@/lib/utils"

export function ImageSearchPage() {
  const [chats, setChats] = useState<Chat[]>([])
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [query, setQuery] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [file, setFile] = useState<File | null>(null)
  const [matches, setMatches] = useState<ImageMatch[]>([])
  const [indexing, setIndexing] = useState(false)
  const [searchHash, setSearchHash] = useState("")
  const [indexedPhotos, setIndexedPhotos] = useState(0)
  const [readyChats, setReadyChats] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api
      .chats()
      .then((payload) => setChats(payload.chats || []))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
    api
      .imageSearchStatus()
      .then((payload) => {
        setIndexedPhotos(payload.index.indexed_photos || 0)
        setReadyChats(payload.index.indexed_chats || 0)
      })
      .catch(() => {})
  }, [])

  // Poll the index while a search is running.
  useEffect(() => {
    if (!indexing) return
    const timer = window.setInterval(() => {
      api
        .imageSearchStatus(searchHash)
        .then((payload) => {
          setIndexedPhotos(payload.index.indexed_photos || 0)
          setReadyChats(payload.index.indexed_chats || 0)
          if (!payload.telegram.running) {
            setIndexing(false)
            setMatches([])
          }
        })
        .catch(() => setIndexing(false))
    }, 3000)
    return () => window.clearInterval(timer)
  }, [indexing, searchHash])

  const visible = chats.filter((chat) => {
    if (!query.trim()) return true
    const needle = query.trim().toLowerCase()
    return `${chat.title} ${chat.username || ""}`.toLowerCase().includes(needle)
  })

  function toggle(id: number) {
    setSelected((previous) => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function run() {
    if (!file) {
      setError("Choose a product photo first.")
      return
    }
    if (selected.size === 0) {
      setError("Select at least one Telegram channel or group.")
      return
    }
    setBusy(true)
    setError("")
    setMatches([])
    try {
      const payload = await api.imageSearch(file, Array.from(selected), startDate, endDate)
      setMatches(payload.matches || [])
      setSearchHash(payload.search_hash || "")
      if (payload.indexing) {
        setIndexing(true)
        setMatches([])
      }
      if (payload.index) {
        setIndexedPhotos(payload.index.indexed_photos || 0)
        setReadyChats(payload.index.indexed_chats || 0)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Research tools
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Image search</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            Find the same product in your Telegram channels by photo, not by name.
          </p>
        </div>
        <div className="flex gap-2">
          <Badge variant="outline">{indexedPhotos.toLocaleString()} indexed photos</Badge>
          <Badge variant="outline">{readyChats} channels ready</Badge>
        </div>
      </div>

      {error ? (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_360px]">
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">1 · Product photo</CardTitle>
            {file ? (
              <Badge variant="secondary" className="truncate">
                {file.name}
              </Badge>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-3">
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                const dropped = event.dataTransfer.files?.[0]
                if (dropped) setFile(dropped)
              }}
              className="flex min-h-[160px] w-full flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-4 py-6 text-center transition-colors hover:border-primary/60 hover:bg-primary/5"
            >
              <Camera className="size-6 text-muted-foreground" />
              <span className="text-sm font-medium">
                {file ? "Replace the photo" : "Drop a product photo"}
              </span>
              <span className="text-[11px] text-muted-foreground">
                or click to choose · max 8 MB
              </span>
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(event) => {
                const chosen = event.target.files?.[0]
                if (chosen) setFile(chosen)
                event.target.value = ""
              }}
            />

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label className="text-[10px] font-medium text-muted-foreground">Start date</label>
                <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
              </div>
              <div className="space-y-1">
                <label className="text-[10px] font-medium text-muted-foreground">End date</label>
                <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
              </div>
            </div>

            <Button onClick={() => void run()} disabled={busy || !file}>
              {busy ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <Search className="mr-2 size-4" />
              )}
              Search {selected.size} channel{selected.size === 1 ? "" : "s"}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">2 · Channels</CardTitle>
            <div className="flex gap-1">
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setSelected(new Set(visible.map((chat) => chat.id)))}
              >
                All
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
                None
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter channels…"
            />
            <div className="max-h-[420px] space-y-1 overflow-y-auto">
              {chats.length === 0 ? (
                <p className="py-6 text-center text-xs text-muted-foreground">
                  Connect Telegram to list your channels and groups.
                </p>
              ) : (
                visible.map((chat) => (
                  <button
                    key={chat.id}
                    type="button"
                    onClick={() => toggle(chat.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors",
                      selected.has(chat.id) && "border-primary bg-primary/5",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-medium">{chat.title}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">
                        {chat.kind}
                        {chat.username ? ` · @${chat.username}` : ""}
                      </span>
                    </span>
                    {selected.has(chat.id) ? (
                      <Badge variant="secondary" className="shrink-0 text-[10px]">
                        ✓
                      </Badge>
                    ) : null}
                  </button>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {indexing ? (
        <Card>
          <CardContent className="space-y-2 py-5">
            <div className="flex items-center gap-2">
              <RefreshCw className="size-4 animate-spin text-muted-foreground" />
              <p className="text-sm font-medium">Indexing channels for this photo…</p>
            </div>
            <Progress value={readyChats ? 60 : 20} className="h-1.5" />
            <p className="text-[11px] text-muted-foreground">
              {indexedPhotos.toLocaleString()} photos indexed so far. The page updates itself.
            </p>
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle className="text-base">3 · Matches</CardTitle>
          <Badge variant="outline">{matches.length}</Badge>
        </CardHeader>
        <CardContent>
          {matches.length === 0 ? (
            <div className="rounded-xl border border-dashed px-4 py-8 text-center">
              <ImageIcon className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium">No matches yet</p>
              <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
                Pick a photo and the channels to search. Every hit shows the supplier, the price and
                a link back to the original post.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {matches.map((match, index) => (
                <div
                  key={`${match.chat_id}-${match.message_id}-${index}`}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">
                      {match.product || "Unnamed product"}
                    </p>
                    <p className="truncate text-[11px] text-muted-foreground">
                      {[
                        match.supplier,
                        match.chat_title,
                        match.message_date,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold tabular-nums">
                      {money(match.price, match.currency)}
                    </span>
                    {match.telegram_url ? (
                      <Button size="sm" variant="ghost" asChild>
                        <a href={match.telegram_url} target="_blank" rel="noreferrer">
                          <Send className="size-3.5" />
                        </a>
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
