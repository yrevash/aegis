/**
 * Pure derivations for the admin command center.
 *
 * Kept recharts-free and side-effect-free so every figure the Overview draws is
 * provably a function of a real accessor response. Nothing in here invents a
 * point: each helper either finds the number in the payload or leaves the slot
 * empty for an `Absence` to fill.
 */

/** One time-bucketed spend point, exactly as `GET /admin/usage` sends it. */
export interface SpendPoint {
  ts: string
  cost_usd: number
}

/** One per-model spend row, exactly as `GET /admin/usage` sends it. */
export interface ModelSpend {
  model: string
  cost_usd: number
  tokens: number
}

/** A calendar day of spend, keyed by the ISO date its bucket falls in. */
export interface DailySpend {
  day: string
  cost: number
}

/**
 * Roll hourly ledger buckets up to UTC calendar days.
 *
 * The ledger buckets hourly, and a 30-day window is 400-odd points — more marks
 * than a 700px card has pixels, so the line stops being a line and becomes a
 * texture. Days are also the unit the forecast works in (`freq: "D"`), so the
 * Overview's trend and the Forecast screen's history are the same series at the
 * same resolution rather than two different-looking views of one number.
 *
 * Buckets are summed, never averaged: a day with fewer active hours spent less,
 * and averaging would hide exactly that.
 */
export function toDaily(series: readonly SpendPoint[]): DailySpend[] {
  const byDay = new Map<string, number>()
  for (const p of series) {
    if (!Number.isFinite(p.cost_usd)) continue
    const day = String(p.ts).slice(0, 10)
    if (day.length !== 10) continue
    byDay.set(day, (byDay.get(day) ?? 0) + p.cost_usd)
  }
  return [...byDay.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([day, cost]) => ({ day, cost }))
}

/** A day label for an axis — "12 Aug", stable width, no year noise. */
export function dayLabel(day: string): string {
  const t = Date.parse(`${day}T00:00:00Z`)
  if (Number.isNaN(t)) return day
  return new Date(t).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC' })
}

/** One stacked band: the row key it occupies, its display name, its total. */
export interface SpendBand {
  key: string
  label: string
  total: number
}

/** The stacked-spend result: rows ready for recharts, and the bands that fill them. */
export interface StackedSpend {
  rows: Record<string, string | number>[]
  bands: SpendBand[]
}

/**
 * Stack per-tenant daily spend under the platform total, largest band first.
 *
 * The third band is the point of this function. The platform series is the whole
 * ledger; the per-tenant series are the rows that carry a `tenant_id`, and on
 * this deployment they sum to less than the platform total because platform-level
 * work (evaluation runs, red-team suites, ingestion) is not billed to a tenant.
 * A stack of the two tenants alone would therefore contradict the "Total spend"
 * tile sitting directly above it, which is the kind of small inconsistency a
 * careful reader finds first. So the remainder is carried as its own named band
 * rather than dropped, and the stack sums to the platform figure by construction.
 *
 * `Math.max(0, …)` guards float noise only: the tenant series are a partition of
 * the platform series, so a genuinely negative remainder is not representable.
 */
export function stackSpendByTenant(
  platform: readonly SpendPoint[],
  tenants: readonly { id: number; name: string; series: readonly SpendPoint[] }[],
): StackedSpend {
  const platformDaily = toDaily(platform)
  if (platformDaily.length === 0) return { rows: [], bands: [] }

  const perTenant = tenants.map((t) => ({
    key: `t${t.id}`,
    label: t.name,
    byDay: new Map(toDaily(t.series).map((d) => [d.day, d.cost])),
  }))

  const totals = new Map<string, number>(perTenant.map((t) => [t.key, 0]))
  let unattributed = 0

  const rows = platformDaily.map(({ day, cost }) => {
    const row: Record<string, string | number> = { day: dayLabel(day) }
    let claimed = 0
    for (const t of perTenant) {
      const v = t.byDay.get(day) ?? 0
      row[t.key] = Number(v.toFixed(4))
      claimed += v
      totals.set(t.key, (totals.get(t.key) ?? 0) + v)
    }
    const rest = Math.max(0, cost - claimed)
    row.rest = Number(rest.toFixed(4))
    unattributed += rest
    return row
  })

  const bands: SpendBand[] = [
    ...perTenant.map((t) => ({ key: t.key, label: t.label, total: totals.get(t.key) ?? 0 })),
    { key: 'rest', label: 'Platform (untenanted)', total: unattributed },
  ]
    .filter((b) => b.total > 0)
    .sort((a, b) => b.total - a.total)

  return { rows, bands }
}

/** One donut slice of the model mix, with its rank already resolved. */
export interface MixSlice {
  name: string
  value: number
  share: number
}

/**
 * The model mix, folded to at most `max` slices with the tail named "Other".
 *
 * The fold is forced by the palette rather than chosen for tidiness. The ordinal
 * ramp has four steps that pass `validate_palette.js --ordinal`; a fifth step is
 * either too pale to read against a white card (1.42:1) or too close to its
 * neighbour to separate (ΔL 0.045). A donut cannot carry more categories than it
 * has colours, because a slice's only route to its name is its swatch — so the
 * tail is summed into one honest band instead of being given a colour that lies
 * about being distinct.
 */
export function modelMix(rows: readonly ModelSpend[], max = 4): MixSlice[] {
  const priced = rows.filter((r) => Number.isFinite(r.cost_usd) && r.cost_usd > 0)
  if (priced.length === 0) return []
  const sorted = [...priced].sort((a, b) => b.cost_usd - a.cost_usd)
  const total = sorted.reduce((sum, r) => sum + r.cost_usd, 0)
  if (total <= 0) return []

  const head = sorted.slice(0, sorted.length <= max ? max : max - 1)
  const tail = sorted.slice(head.length)
  const slices = head.map((r) => ({
    name: shortModel(r.model),
    value: Number(r.cost_usd.toFixed(4)),
    share: r.cost_usd / total,
  }))
  if (tail.length > 0) {
    const rest = tail.reduce((sum, r) => sum + r.cost_usd, 0)
    slices.push({
      name: `Other (${tail.length})`,
      value: Number(rest.toFixed(4)),
      share: rest / total,
    })
  }
  return slices
}

/**
 * Trim a deployment id down to the model a reader recognises.
 *
 * `genailab-maas-Llama-4-Maverick-17B-128E-Instruct-FP8` is 52 characters of
 * which the first sixteen are the same on every row — a legend of those is a
 * legend of one repeated prefix. The vendor prefix goes; nothing else does,
 * because the size and the quantisation are the part that explains the price.
 */
export function shortModel(model: string): string {
  return model.replace(/^genailab-maas-/i, '')
}
