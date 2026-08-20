/**
 * The schema's own shape, derived from `GET /database/overview` and nothing else.
 *
 * **Nothing here knows the names of any of our tables.** Every layer, every edge and
 * every figure is read out of the payload the server sent, so the diagram the console
 * draws stays true the day somebody adds a table or drops a foreign key — which is the
 * only kind of ER diagram worth putting in front of a reviewer. A hand-maintained
 * picture of a schema is a picture of the schema *as it was*, and it is wrong silently.
 *
 * Kept out of the component for the reason `dbView.ts` is: ranking a directed graph,
 * ordering columns to reduce edge crossings, and routing a curve between two boxes are
 * all easy to get subtly wrong and impossible to test through a rendered tree.
 * `web/tests/db/schemaGraph.test.mjs` exercises them directly.
 *
 * @see web/src/lib/api/database.ts for the payload these read.
 */

import type { DbTable } from '@/lib/api/database'

/** One declared foreign key, resolved against the tables actually in the payload. */
export interface SchemaEdge {
  /** The referencing table — the **many** side. */
  from: string
  /** The referenced table — the **one** side. */
  to: string
  /** The column on `from` that carries the reference. */
  column: string
  /** The column on `to` it points at. */
  references: string
}

/**
 * Every foreign key that can actually be drawn, and a count of the ones that cannot.
 *
 * Two kinds are held back, and both are stated rather than dropped in silence:
 *
 * - A key pointing at a relation this connection may not read. The catalog is built
 *   from the console's own grants, so a target outside it genuinely is not on the
 *   page — drawing an edge to nowhere would be a worse lie than counting it.
 * - A self-reference. It is a real key, but it is not a *layer* relation: a tree
 *   inside one table cannot be a column in a layered diagram, and forcing it in would
 *   put the table above itself.
 */
export function schemaEdges(tables: readonly DbTable[]): {
  edges: SchemaEdge[]
  /** Keys whose target is not in the catalog this connection can see. */
  unresolved: number
  /** Keys a table declares onto itself. */
  selfReferences: number
} {
  const known = new Set(tables.map((table) => table.name))
  const edges: SchemaEdge[] = []
  let unresolved = 0
  let selfReferences = 0

  for (const table of tables) {
    for (const key of table.foreignKeys) {
      if (key.referencesTable === table.name) {
        selfReferences += 1
        continue
      }
      if (!known.has(key.referencesTable)) {
        unresolved += 1
        continue
      }
      edges.push({
        from: table.name,
        to: key.referencesTable,
        column: key.column,
        references: key.referencesColumn,
      })
    }
  }
  return { edges, unresolved, selfReferences }
}

/**
 * How far a table sits from a table that references nothing.
 *
 * `rank(t) = 0` when `t` declares no outgoing key, and `1 + max(rank(target))`
 * otherwise — the longest path along the reference direction. It is what turns an
 * unordered set of relations into columns: everything that only *is* referenced ends up
 * on one side, everything that only *does* reference ends up on the other, and the
 * arrows all run the same way.
 *
 * A reference cycle is stopped rather than followed — the value cached for a table
 * reached inside one is the depth of the truncated walk. No schema here has a cycle;
 * the guard exists so that one arriving is a slightly odd picture rather than a hung tab.
 */
export function rankTables(edges: readonly SchemaEdge[]): Map<string, number> {
  const targets = new Map<string, string[]>()
  for (const edge of edges) {
    const list = targets.get(edge.from)
    if (list) list.push(edge.to)
    else targets.set(edge.from, [edge.to])
  }

  const rank = new Map<string, number>()
  const walk = (name: string, trail: Set<string>): number => {
    const settled = rank.get(name)
    if (settled !== undefined) return settled
    if (trail.has(name)) return 0
    trail.add(name)
    let depth = 0
    for (const target of targets.get(name) ?? []) depth = Math.max(depth, 1 + walk(target, trail))
    trail.delete(name)
    rank.set(name, depth)
    return depth
  }

  for (const edge of edges) {
    walk(edge.from, new Set())
    walk(edge.to, new Set())
  }
  return rank
}

/* ── Geometry ───────────────────────────────────────────────────────────────
 *
 * Fixed pixels rather than a viewBox that scales, because the boxes are real HTML —
 * selectable names, a tooltip on the withheld badge, a focus ring on every node — and
 * scaled HTML is blurry HTML. The diagram lives in its own `overflow-x-auto` box, so a
 * 390px viewport scrolls the diagram and never the page (DESIGN.md §4).
 */

/** One table box in the whole-schema map. */
export const NODE_W = 182
export const NODE_H = 46
/** Vertical gap between boxes in a column, and horizontal gap between columns. */
export const ROW_GAP = 12
export const COL_GAP = 86
/** Room above the first row for the column caption. */
export const CAPTION_H = 24

/** One table, placed. */
export interface PlacedNode {
  name: string
  /** Column index, left to right. Column 0 references the most. */
  column: number
  x: number
  y: number
}

/** One edge, placed — the curve plus the two endpoints its notation hangs off. */
export interface RoutedEdge extends SchemaEdge {
  /** SVG cubic path from the many end to the one end. */
  path: string
  /** Where the curve leaves the referencing box. */
  x1: number
  y1: number
  /** Where it meets the referenced box. */
  x2: number
  y2: number
}

/** The whole-schema map. */
export interface MapLayout {
  nodes: PlacedNode[]
  edges: RoutedEdge[]
  /** Tables no resolvable key connects to anything, in payload order. */
  unlinked: string[]
  /** How many columns the layered part occupies. */
  columns: number
  width: number
  height: number
}

/**
 * Lay the referenced half of the schema out as columns, arrows running one way.
 *
 * Two passes and no physics. Rank decides the column; then the columns are ordered
 * from the right — the side everything points at — and each column to its left is
 * sorted by the mean height of what its tables point *at*. That is the barycentre
 * heuristic, and on a schema this shape it is the difference between four tidy columns
 * and the hairball this function exists to avoid.
 *
 * Tables with no resolvable key are not placed. They are not failures and they are not
 * hidden: they come back in `unlinked` and the screen gives them their own labelled
 * band, because "this relation declares no foreign key" is a fact about the schema.
 */
export function layoutMap(tables: readonly DbTable[]): MapLayout {
  const { edges } = schemaEdges(tables)
  const rank = rankTables(edges)

  const linked = tables.filter((table) => rank.has(table.name))
  const unlinked = tables.filter((table) => !rank.has(table.name)).map((table) => table.name)

  if (linked.length === 0) {
    return { nodes: [], edges: [], unlinked, columns: 0, width: 0, height: 0 }
  }

  const maxRank = Math.max(...linked.map((table) => rank.get(table.name) ?? 0))
  const columns = maxRank + 1

  // Column index runs left to right; rank runs the other way, because rank 0 is the
  // table everything points at and that belongs on the right where the arrows land.
  const byColumn: string[][] = Array.from({ length: columns }, () => [])
  for (const table of linked) byColumn[maxRank - (rank.get(table.name) ?? 0)].push(table.name)

  const tallest = Math.max(...byColumn.map((column) => column.length))
  const bodyH = tallest * NODE_H + (tallest - 1) * ROW_GAP
  const height = CAPTION_H + bodyH

  const outgoing = new Map<string, string[]>()
  for (const edge of edges) {
    const list = outgoing.get(edge.from)
    if (list) list.push(edge.to)
    else outgoing.set(edge.from, [edge.to])
  }

  const placed = new Map<string, PlacedNode>()

  const place = (column: number, names: string[]): void => {
    const count = names.length
    const columnH = count * NODE_H + (count - 1) * ROW_GAP
    const top = CAPTION_H + (bodyH - columnH) / 2
    names.forEach((name, index) => {
      placed.set(name, {
        name,
        column,
        x: column * (NODE_W + COL_GAP),
        y: top + index * (NODE_H + ROW_GAP),
      })
    })
  }

  // Right to left: a column can only be ordered once the column it points at is.
  for (let column = columns - 1; column >= 0; column -= 1) {
    const names = [...byColumn[column]].sort((a, b) => a.localeCompare(b))
    if (column < columns - 1) {
      const centre = (name: string): number => {
        const ys = (outgoing.get(name) ?? [])
          .map((target) => placed.get(target)?.y)
          .filter((y): y is number => y !== undefined)
        return ys.length === 0 ? Number.MAX_SAFE_INTEGER : ys.reduce((a, b) => a + b, 0) / ys.length
      }
      names.sort((a, b) => centre(a) - centre(b) || a.localeCompare(b))
    }
    place(column, names)
  }

  const routed: RoutedEdge[] = []
  for (const edge of edges) {
    const from = placed.get(edge.from)
    const to = placed.get(edge.to)
    if (!from || !to) continue
    const x1 = from.x + NODE_W
    const y1 = from.y + NODE_H / 2
    const x2 = to.x
    const y2 = to.y + NODE_H / 2
    const bend = Math.max(24, (x2 - x1) * 0.42)
    routed.push({
      ...edge,
      x1,
      y1,
      x2,
      y2,
      path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
    })
  }

  // Left to right, top to bottom — the columns are *placed* right to left, because a
  // barycentre needs the column it points at to be settled first, but the reading and
  // tab order of the boxes must follow the diagram rather than the algorithm.
  const nodes = [...placed.values()].sort((a, b) => a.column - b.column || a.y - b.y)

  return {
    nodes,
    edges: routed,
    unlinked,
    columns,
    width: columns * NODE_W + (columns - 1) * COL_GAP,
    height,
  }
}

/** One table beside the focused one, with the key that joins them. */
export interface Relation {
  table: DbTable
  column: string
  references: string
}

/** One table and everything a declared key connects it to. */
export interface Neighbourhood {
  focus: DbTable
  /** Tables that reference the focus. The **many** side, pointing in. */
  children: Relation[]
  /** Tables the focus references. The **one** side, pointed at. */
  parents: Relation[]
}

/**
 * The focused table and its immediate neighbours — the answer to "how does this
 * relation connect", asked one table at a time.
 *
 * Forty-three relations drawn in full detail at once is a hairball nobody reads, and
 * cropping the schema to fit would be a diagram that lies by omission. Focusing is the
 * escape: the whole map stays available and complete, and detail is a click.
 */
export function neighbourhood(
  tables: readonly DbTable[],
  name: string,
): Neighbourhood | null {
  const focus = tables.find((table) => table.name === name)
  if (!focus) return null

  const by = new Map(tables.map((table) => [table.name, table]))
  const { edges } = schemaEdges(tables)

  const children: Relation[] = []
  const parents: Relation[] = []
  for (const edge of edges) {
    if (edge.to === name) {
      const table = by.get(edge.from)
      if (table) children.push({ table, column: edge.column, references: edge.references })
    } else if (edge.from === name) {
      const table = by.get(edge.to)
      if (table) parents.push({ table, column: edge.column, references: edge.references })
    }
  }
  const order = (a: Relation, b: Relation): number => a.table.name.localeCompare(b.table.name)
  return { focus, children: children.sort(order), parents: parents.sort(order) }
}

/** The figures the map states about itself. Every one is a count of the payload. */
export interface SchemaStats {
  tables: number
  tenantScoped: number
  platform: number
  foreignKeys: number
  unresolvedKeys: number
  /** Relations no resolvable key connects to anything. */
  unlinked: number
  /** Columns the catalog has but this connection may not read. */
  withheld: number
  withheldTables: string[]
  /** The most-referenced relation, which on a multi-tenant schema is the story. */
  hub: { name: string; referencedBy: number } | null
}

export function schemaStats(tables: readonly DbTable[]): SchemaStats {
  const { edges, unresolved } = schemaEdges(tables)
  const rank = rankTables(edges)

  const incoming = new Map<string, number>()
  for (const edge of edges) incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1)

  let hub: SchemaStats['hub'] = null
  for (const [name, referencedBy] of incoming) {
    if (hub === null || referencedBy > hub.referencedBy) hub = { name, referencedBy }
  }

  const withheldTables = tables
    .filter((table) => table.withheldColumns.length > 0)
    .map((table) => table.name)

  return {
    tables: tables.length,
    tenantScoped: tables.filter((table) => table.tenantScoped).length,
    platform: tables.filter((table) => !table.tenantScoped).length,
    foreignKeys: edges.length,
    unresolvedKeys: unresolved,
    unlinked: tables.filter((table) => !rank.has(table.name)).length,
    withheld: tables.reduce((n, table) => n + table.withheldColumns.length, 0),
    withheldTables,
    hub,
  }
}
