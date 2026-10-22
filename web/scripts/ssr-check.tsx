import React from "react"
import { renderToString } from "react-dom/server"
import App from "../src/App"
import { DashboardPage } from "../src/pages/dashboard-page"
import { ProductPage } from "../src/pages/product-page"
import { ProductsPage } from "../src/pages/products-page"
import { ScanPage } from "../src/pages/scan-page"
import { ImageSearchPage } from "../src/pages/image-search-page"
import { SuppliersPage } from "../src/pages/suppliers-page"
import { ReportsPage } from "../src/pages/reports-page"
import { SettingsPage } from "../src/pages/settings-page"

// A render smoke test: these throw on any component-level runtime error, which
// a type check cannot catch. Effects do not run here, so the pages render their
// loading/empty states.
const cases: [string, React.ReactElement][] = [
  ["App", <App />],
  ["DashboardPage", <DashboardPage onOpenProduct={() => {}} />],
  ["ProductsPage", <ProductsPage onOpenProduct={() => {}} />],
  ["ProductPage empty", <ProductPage productKey="" />],
  ["ProductPage key", <ProductPage productKey="test-key" />],
  ["ScanPage", <ScanPage />],
  ["ImageSearchPage", <ImageSearchPage />],
  ["SuppliersPage", <SuppliersPage />],
  ["ReportsPage", <ReportsPage />],
  ["SettingsPage", <SettingsPage />],
]

let failed = 0
for (const [name, element] of cases) {
  try {
    const html = renderToString(element)
    if (!html || html.length < 40) throw new Error(`suspiciously small output (${html.length})`)
    console.log(`ok   ${name} -> ${html.length} chars`)
  } catch (error) {
    failed += 1
    console.error(`FAIL ${name}:`, error instanceof Error ? error.message : error)
  }
}

if (failed) process.exit(1)
console.log("SSR smoke test OK")
