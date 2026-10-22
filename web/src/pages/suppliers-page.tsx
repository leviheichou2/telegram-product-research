import { useEffect, useMemo, useState } from "react"
import { Building2, Phone, Search, ShieldCheck, Upload, Users } from "lucide-react"

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
import { api, type DirectorySupplier } from "@/lib/api"
import { cn } from "@/lib/utils"

export function SuppliersPage() {
  const [rows, setRows] = useState<DirectorySupplier[] | null>(null)
  const [categories, setCategories] = useState<string[]>([])
  const [sheets, setSheets] = useState<
    { sheet_name: string; row_count: number; columns: string[] }[]
  >([])
  const [category, setCategory] = useState("")
  const [query, setQuery] = useState("")
  const [error, setError] = useState("")
  const [uploading, setUploading] = useState(false)
  const [uploadMessage, setUploadMessage] = useState("")

  useEffect(() => {
    api
      .suppliers()
      .then((payload) => {
        setRows(payload.suppliers || [])
        setCategories(payload.categories || [])
        setSheets(payload.sheets || [])
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  const filtered = useMemo(() => {
    const all = rows || []
    const needle = query.trim().toLowerCase()
    return all.filter((row) => {
      if (category && row.category_sheet !== category) return false
      if (!needle) return true
      return `${row.supplier_name || ""} ${row.category || ""} ${row.city || ""} ${row.phone || ""}`
        .toLowerCase()
        .includes(needle)
    })
  }, [rows, category, query])

  async function upload(file: File) {
    setUploading(true)
    setUploadMessage("")
    setError("")
    try {
      const form = new FormData()
      form.append("file", file, file.name)
      const response = await fetch("/api/suppliers/upload", { method: "POST", body: form })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`)
      setUploadMessage(`Imported ${payload.count ?? 0} suppliers.`)
      const refreshed = await api.suppliers()
      setRows(refreshed.suppliers || [])
      setCategories(refreshed.categories || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
            Suppliers
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">
            {rows === null ? "Loading…" : `${filtered.length} suppliers`}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            The wholesaler guide. Every product page matches against it on demand.
          </p>
        </div>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted">
          <Upload className="size-4" />
          {uploading ? "Importing…" : "Import Excel"}
          <input
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void upload(file)
              event.target.value = ""
            }}
          />
        </label>
      </div>

      {error ? (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="py-6 text-sm text-destructive">{error}</CardContent>
        </Card>
      ) : null}
      {uploadMessage ? (
        <Card className="border-emerald-500/40 bg-emerald-500/5">
          <CardContent className="py-4 text-sm text-emerald-700">{uploadMessage}</CardContent>
        </Card>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-3">
        {[
          { label: "Suppliers", value: rows?.length ?? 0, icon: Users },
          { label: "Categories", value: categories.length, icon: Building2 },
          { label: "Sheets imported", value: sheets.length, icon: ShieldCheck },
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
        <CardContent className="pt-4">
          <div className="flex flex-wrap items-center gap-2 pb-3">
            <div className="relative min-w-[220px] flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search suppliers, cities, phones…"
                className="pl-8"
              />
            </div>
            <div className="flex flex-wrap gap-1.5">
              <button
                type="button"
                onClick={() => setCategory("")}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-[11px] font-medium",
                  !category && "border-primary bg-primary/10 text-primary",
                )}
              >
                All
              </button>
              {categories.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setCategory(category === item ? "" : item)}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[11px] font-medium",
                    category === item && "border-primary bg-primary/10 text-primary",
                  )}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>

          {rows === null ? (
            <div className="space-y-2">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-10 text-center text-sm text-muted-foreground">
              {rows.length === 0
                ? "No suppliers imported yet. Upload the supplier Excel file to populate the guide."
                : "Nothing matches that filter."}
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Supplier</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>City</TableHead>
                  <TableHead>Phone</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.slice(0, 300).map((row, index) => (
                  <TableRow key={row.id ?? index}>
                    <TableCell className="max-w-[280px]">
                      <div className="truncate font-medium">{row.supplier_name || "—"}</div>
                      <div className="truncate text-[11px] text-muted-foreground">
                        {row.product_type || row.category_sheet || ""}
                      </div>
                    </TableCell>
                    <TableCell className="text-[12px]">{row.category || "—"}</TableCell>
                    <TableCell className="text-[12px]">{row.city || "—"}</TableCell>
                    <TableCell className="text-[12px]">
                      {row.phone ? (
                        <span className="inline-flex items-center gap-1">
                          <Phone className="size-3 text-muted-foreground" />
                          {row.phone}
                        </span>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell className="max-w-[240px] truncate text-[12px] text-muted-foreground">
                      {row.notes || "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
