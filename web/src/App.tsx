import { useEffect, useState } from "react"

import { AppShell, type PageId } from "@/components/app-shell"
import { DashboardPage } from "@/pages/dashboard-page"
import { ProductPage } from "@/pages/product-page"
import { ProductsPage } from "@/pages/products-page"
import { ScanPage } from "@/pages/scan-page"
import { ImageSearchPage } from "@/pages/image-search-page"
import { SuppliersPage } from "@/pages/suppliers-page"
import { ReportsPage } from "@/pages/reports-page"
import { SettingsPage } from "@/pages/settings-page"
import { TooltipProvider } from "@/components/ui/tooltip"

export default function App() {
  const [page, setPage] = useState<PageId>("overview")
  const [productKey, setProductKey] = useState("")
  const [scanLabel, setScanLabel] = useState("")

  useEffect(() => {
    fetch("/api/results")
      .then((response) => response.json())
      .then((payload: { scan?: { scan_id?: string; status?: string } }) => {
        const scan = payload?.scan
        if (scan?.scan_id) setScanLabel(`Scan ${scan.scan_id.slice(0, 8)}`)
      })
      .catch(() => setScanLabel(""))
  }, [])

  function openProduct(key: string) {
    setProductKey(key)
    setPage("product")
  }

  return (
    <TooltipProvider>
      <AppShell page={page} onNavigate={setPage} scanLabel={scanLabel}>
        {page === "overview" ? <DashboardPage onOpenProduct={openProduct} /> : null}
        {page === "products" ? <ProductsPage onOpenProduct={openProduct} /> : null}
        {page === "product" ? <ProductPage productKey={productKey} /> : null}
        {page === "scan" ? <ScanPage /> : null}
        {page === "image-search" ? <ImageSearchPage /> : null}
        {page === "suppliers" ? <SuppliersPage /> : null}
        {page === "reports" ? <ReportsPage /> : null}
        {page === "settings" ? <SettingsPage /> : null}
      </AppShell>
    </TooltipProvider>
  )
}
