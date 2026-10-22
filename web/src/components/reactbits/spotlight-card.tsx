import { useRef, type ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * React Bits - SpotlightCard.
 *
 * The card tracks the pointer and paints a soft radial highlight under it, so a
 * grid of cards feels alive without any animation loop running when idle.
 */
export function SpotlightCard({
  children,
  className,
  spotlightColor = "rgba(14, 138, 153, 0.14)",
}: {
  children: ReactNode
  className?: string
  spotlightColor?: string
}) {
  const ref = useRef<HTMLDivElement>(null)

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const node = ref.current
    if (!node) return
    const rect = node.getBoundingClientRect()
    node.style.setProperty("--spot-x", `${event.clientX - rect.left}px`)
    node.style.setProperty("--spot-y", `${event.clientY - rect.top}px`)
  }

  return (
    <div
      ref={ref}
      onPointerMove={handlePointerMove}
      className={cn(
        "group relative overflow-hidden rounded-2xl border bg-card p-5 transition-shadow",
        "hover:shadow-lg hover:shadow-black/5",
        className,
      )}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background: `radial-gradient(320px circle at var(--spot-x, 50%) var(--spot-y, 50%), ${spotlightColor}, transparent 70%)`,
        }}
      />
      <div className="relative">{children}</div>
    </div>
  )
}
