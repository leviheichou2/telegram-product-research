import { useEffect, useRef, useState } from "react"
import { animate } from "motion/react"

/**
 * React Bits - CountUp.
 *
 * Animates a number from zero once it is on screen. Used for the metric cards so
 * the dashboard settles into place instead of popping in.
 */
export function CountUp({
  to,
  from = 0,
  duration = 1.1,
  decimals = 0,
  suffix = "",
  prefix = "",
}: {
  to: number
  from?: number
  duration?: number
  decimals?: number
  suffix?: string
  prefix?: string
}) {
  const [value, setValue] = useState(from)
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const node = ref.current
    if (!node || !Number.isFinite(to)) return
    let cancelled = false
    const observer = new IntersectionObserver((entries) => {
      if (!entries[0]?.isIntersecting || cancelled) return
      observer.disconnect()
      const controls = animate(from, to, {
        duration,
        ease: "easeOut",
        onUpdate: (latest) => setValue(latest),
      })
      return () => controls.stop()
    })
    observer.observe(node)
    return () => {
      cancelled = true
      observer.disconnect()
    }
  }, [to, from, duration])

  return (
    <span ref={ref} className="tabular-nums">
      {prefix}
      {value.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
      {suffix}
    </span>
  )
}
