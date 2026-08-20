'use client'

import { ArrowLeft, KeyRound, Lock, Network, Table2 } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import type { DbTable } from '@/lib/api/database'
import { cn } from '@/lib/utils'

import { estimate } from './dbView'
import {
  CAPTION_H,
  NODE_H,
  NODE_W,
  layoutMap,
  neighbourhood,
  schemaStats,
  type Relation,
} from './schemaGraph'

/** The one focus treatment on this surface: the ring token, at 2px, always visible. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background'

/* ── Focus-view geometry ─────────────────────────────────────────────────── */

/** A neighbour box in the focused view. */
const NEIGHBOUR_W = 244
const NEIGHBOUR_H = 46
const NEIGHBOUR_GAP = 10
/** The focused entity, which carries its whole column list. */
const ENTITY_W = 328
const ENTITY_HEAD = 40
const ENTITY_ROW = 23
/** Horizontal room between a lane and the entity — enough for a key name on the curve. */
const LANE_GAP = 104

/**
 * The schema as an entity–relationship diagram, drawn from the live catalog.
 *
 * **Nothing in this file names a table.** Columns, keys, row estimates and the layering
 * all come out of `GET /database/overview`, which the server builds from
 * `information_schema` under the console's own grants — so the picture is the schema as
 * it is now, and a migration changes the diagram without anybody redrawing it. A
 * hand-kept ER diagram is a picture of the schema *as it was*, and it goes wrong
 * silently, which is the one failure mode a governance console cannot afford.
 *
 * **Forty-three relations, drawn so they can be read.** All of them at full detail at
 * once is a hairball, and cropping to the "interesting" ones is a diagram that lies by
 * omission. So there are two views over the same complete data: a layered map of every
 * relation a foreign key touches — arrows all running one way, ordered to keep the
 * crossings down — with the relations that declare no key given their own labelled band
 * beneath it; and, on any table, its own neighbourhood at full detail, every column
 * typed, with each edge landing on the exact column it references.
 *
 * **Withheld columns are drawn.** `users.password_hash` is absent from this connection's
 * `information_schema` because a Postgres **COLUMN grant** withholds it — not because
 * the application filtered it on the way out. That distinction is the whole difference
 * between a privacy control and a privacy claim, so the column appears in its table, in
 * its place, locked, and says which mechanism is holding it back.
 */
export function SchemaMap({
  tables,
  scope,
  selected,
  onSelect,
}: {
  tables: DbTable[]
  /** What the page is currently reading as, for the receipt. */
  scope: string
  /** The table the console has selected, if any — the map follows it. */
  selected: string | null
  /** Selecting a table here browses it, so the map and the reader stay in step. */
  onSelect: (name: string) => void
}): ReactElement {
  const [focus, setFocus] = useState<string | null>(selected)
  const [hover, setHover] = useState<string | null>(null)

  // The rail and the map are two ways into the same choice, so picking a relation in
  // either one moves both. Clearing is deliberate and local: "whole schema" widens the
  // diagram without throwing away the result the console has already read.
  useEffect(() => {
    if (selected !== null) setFocus(selected)
  }, [selected])

  const stats = useMemo(() => schemaStats(tables), [tables])
  const map = useMemo(() => layoutMap(tables), [tables])
  const byName = useMemo(() => new Map(tables.map((table) => [table.name, table])), [tables])
  const near = useMemo(
    () => (focus === null ? null : neighbourhood(tables, focus)),
    [tables, focus],
  )

  return (
    <Card>
      <CardHeader
        eyebrow={
          near
            ? `structure · ${near.focus.columns.length} columns · ${estimate(near.focus.rowEstimate)} · ${near.children.length + near.parents.length} declared key${near.children.length + near.parents.length === 1 ? '' : 's'}`
            : `information_schema · ${stats.foreignKeys} declared foreign keys`
        }
        title={near ? `How ${near.focus.name} connects` : 'How the schema fits together'}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {near ? (
              <button
                type="button"
                onClick={() => setFocus(null)}
                className={cn(
                  'inline-flex h-7 touch-manipulation items-center gap-1.5 rounded-full border border-border bg-card px-2.5 text-xs font-medium text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 hover:text-foreground',
                  FOCUS,
                )}
              >
                <ArrowLeft className="size-3" aria-hidden />
                Whole schema
              </button>
            ) : null}
            <InfoTip label="How to read this diagram">
              Each arrow runs from the relation that carries the foreign key — the many
              side, drawn with a crow&rsquo;s foot — to the relation it points at, marked
              with a single bar. Columns are ordered so an arrow never runs backwards, and
              tables are placed to keep the crossings down. Choose any relation to see its
              own neighbourhood with every column typed.
            </InfoTip>
          </div>
        }
      />

      <CardBody className="flex flex-col gap-3 pt-0">
        <Legend stats={stats} focused={near !== null} />

        {near ? (
          <FocusView
            near={near}
            onSelect={(name) => {
              setFocus(name)
              onSelect(name)
            }}
          />
        ) : (
          <>
            <div
              className="-mx-1 overflow-x-auto overscroll-x-contain px-1 pb-1"
              role="group"
              aria-label="Entity relationship map of every relation a foreign key touches"
            >
              <div
                className="relative mx-auto"
                style={{ width: map.width, height: map.height, minWidth: map.width }}
              >
                <svg
                  aria-hidden
                  className="pointer-events-none absolute inset-0"
                  width={map.width}
                  height={map.height}
                >
                  {map.edges.map((edge) => {
                    const lit =
                      hover === null || hover === edge.from || hover === edge.to
                    return (
                      <g
                        key={`${edge.from}.${edge.column}->${edge.to}`}
                        opacity={hover === null ? 1 : lit ? 1 : 0.15}
                      >
                        <path
                          d={edge.path}
                          fill="none"
                          stroke={
                            hover !== null && lit ? 'var(--blue-600)' : 'var(--blue-400)'
                          }
                          strokeOpacity={hover !== null && lit ? 1 : 0.55}
                          strokeWidth={hover !== null && lit ? 1.8 : 1.2}
                        />
                        <CrowsFoot x={edge.x1} y={edge.y1} />
                        <OneBar x={edge.x2} y={edge.y2} />
                      </g>
                    )
                  })}
                </svg>

                {map.nodes.map((node) => {
                  const table = byName.get(node.name)
                  if (!table) return null
                  return (
                    <div
                      key={node.name}
                      className="absolute"
                      style={{ left: node.x, top: node.y, width: NODE_W, height: NODE_H }}
                    >
                      <TableNode
                        table={table}
                        dim={hover !== null && !isNear(map.edges, hover, node.name)}
                        onSelect={() => {
                          setFocus(node.name)
                          onSelect(node.name)
                        }}
                        onHover={setHover}
                      />
                    </div>
                  )
                })}
              </div>
            </div>

            {/* The diagram is a picture; these are the same relationships as sentences,
                so the map is never the only carrier of what it draws. */}
            <ul className="sr-only">
              {map.edges.map((edge) => (
                <li key={`sr-${edge.from}.${edge.column}->${edge.to}`}>
                  {`${edge.from}.${edge.column} references ${edge.to}.${edge.references}`}
                </li>
              ))}
            </ul>

            <Unlinked
              names={map.unlinked}
              byName={byName}
              onSelect={(name) => {
                setFocus(name)
                onSelect(name)
              }}
            />
          </>
        )}

        <Receipt
          label="Drawn from"
          origin="information_schema · pg_class.reltuples · this connection's column grants"
          detail={`${stats.tables} relations · ${stats.foreignKeys} foreign keys · ${stats.withheld} column${stats.withheld === 1 ? '' : 's'} withheld · read as ${scope}`}
        />
      </CardBody>
    </Card>
  )
}

/** Whether `name` is the hovered table or shares an edge with it. */
function isNear(
  edges: ReadonlyArray<{ from: string; to: string }>,
  hover: string,
  name: string,
): boolean {
  if (hover === name) return true
  return edges.some(
    (edge) =>
      (edge.from === hover && edge.to === name) || (edge.to === hover && edge.from === name),
  )
}

/**
 * What the diagram is made of, counted — including the two things it deliberately
 * does not draw as edges.
 *
 * A relation that declares no foreign key is not a gap in the picture; on this schema it
 * is usually a table an embedded subsystem owns and manages itself. Saying how many
 * there are, beside the count that *is* drawn, is what stops the map reading as the
 * whole schema when it is a named part of it.
 */
function Legend({
  stats,
  focused,
}: {
  stats: ReturnType<typeof schemaStats>
  focused: boolean
}): ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-surface-2/50 px-3 py-2">
      <Chip>
        <Table2 className="size-3 text-muted-foreground" aria-hidden />
        <Figure className="text-[0.7rem] text-foreground">{stats.tables}</Figure> relations
      </Chip>
      <Chip>
        <span aria-hidden className="size-2 rounded-[2px] bg-blue-400" />
        <Figure className="text-[0.7rem] text-foreground">{stats.tenantScoped}</Figure>{' '}
        tenant-scoped
        <InfoTip label="What tenant-scoped means">
          The relation carries a tenant column, so the scope selector above changes what a
          read of it returns — by a clause the server welds into the WHERE.
        </InfoTip>
      </Chip>
      <Chip>
        <span aria-hidden className="size-2 rounded-[2px] bg-muted-foreground/45" />
        <Figure className="text-[0.7rem] text-foreground">{stats.platform}</Figure> platform
        <InfoTip label="What platform means">
          No tenant column, so the scope selector does not change what these show. Reading
          them as the same kind of thing is how somebody concludes the filter is broken.
        </InfoTip>
      </Chip>
      {stats.hub !== null ? (
        <Chip>
          <Network className="size-3 text-blue-700" aria-hidden />
          <Figure className="text-[0.7rem] text-foreground">{stats.hub.referencedBy}</Figure> keys
          land on <Figure className="text-[0.7rem] text-foreground">{stats.hub.name}</Figure>
          <InfoTip label="Why one relation is referenced most">
            The most-referenced relation in the catalog, counted from the declared keys.
            On a multi-tenant schema this is the tenancy root, and it is the reason the
            arrows all converge on one column.
          </InfoTip>
        </Chip>
      ) : null}
      {!focused && stats.unlinked > 0 ? (
        <Chip>
          <Figure className="text-[0.7rem] text-foreground">{stats.unlinked}</Figure> declare no
          key
          <InfoTip label="Relations with no declared foreign key">
            They are listed under the map rather than drawn in it: with no key there is no
            edge to draw and no column to place them in. Most are owned by an embedded
            subsystem that manages its own tables.
          </InfoTip>
        </Chip>
      ) : null}
      {stats.withheld > 0 ? (
        <Chip>
          <Lock className="size-3 text-[color:var(--risk-ink)]" aria-hidden />
          <Figure className="text-[0.7rem] text-foreground">{stats.withheld}</Figure> withheld
          <InfoTip label="What withheld means">
            {stats.withheldTables.join(', ')} carr
            {stats.withheldTables.length === 1 ? 'ies' : 'y'} a column this connection has
            no grant on. It is missing from information_schema itself — withheld by a
            Postgres COLUMN grant, not filtered by application code on the way out — so
            it cannot be read, ordered by or filtered on from here at all.
          </InfoTip>
        </Chip>
      ) : null}
      {stats.unresolvedKeys > 0 ? (
        <Chip>
          <Figure className="text-[0.7rem] text-foreground">{stats.unresolvedKeys}</Figure> point
          outside this catalog
          <InfoTip label="Keys that cannot be drawn">
            The relation they reference is not one this connection may read, so there is no
            box to draw the arrow to. Counted here rather than dropped in silence.
          </InfoTip>
        </Chip>
      ) : null}
    </div>
  )
}

/** One legend fact. */
function Chip({ children }: { children: ReactNode }): ReactElement {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      {children}
    </span>
  )
}

/**
 * One relation in the map: its name, what kind of relation it is, and its size.
 *
 * The scope is a **word** on every box rather than only a tint, because DESIGN.md §2 is
 * explicit that identity is never carried by colour alone — and because "tenant" beside
 * a name is what makes the tenant/platform split legible in a screenshot, at a distance,
 * and to a reader who cannot tell the two fills apart.
 */
function TableNode({
  table,
  dim,
  onSelect,
  onHover,
}: {
  table: DbTable
  dim: boolean
  onSelect: () => void
  onHover: (name: string | null) => void
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onSelect}
      onMouseEnter={() => onHover(table.name)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(table.name)}
      onBlur={() => onHover(null)}
      aria-label={`${table.name} — ${table.tenantScoped ? 'tenant-scoped' : 'platform'} relation, ${table.columns.length} columns, ${estimate(table.rowEstimate)}. Show its neighbourhood.`}
      className={cn(
        'flex size-full touch-manipulation flex-col justify-center gap-0.5 rounded-md border border-l-[3px] bg-card px-2.5 py-1.5 text-left transition-all duration-[--dur-fast] hover:border-blue-600 hover:bg-blue-50',
        FOCUS,
        table.tenantScoped ? 'border-l-blue-400' : 'border-l-muted-foreground/45',
        'border-y-border border-r-border',
        dim && 'opacity-35',
      )}
    >
      <span className="flex items-center gap-1">
        <Figure className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">
          {table.name}
        </Figure>
        {table.withheldColumns.length > 0 ? (
          <Lock className="size-3 shrink-0 text-[color:var(--risk-ink)]" aria-hidden />
        ) : null}
      </span>
      <span className="truncate font-mono text-[0.6rem] leading-3 text-muted-foreground">
        {table.tenantScoped ? 'tenant' : 'platform'} · {table.columns.length} cols ·{' '}
        {table.rowEstimate > 0 ? `~${table.rowEstimate.toLocaleString()}` : 'no estimate'}
      </span>
    </button>
  )
}

/** The crow's foot — the many end of a relationship, at the referencing table. */
function CrowsFoot({ x, y }: { x: number; y: number }): ReactElement {
  return (
    <path
      d={`M ${x + 12} ${y} L ${x} ${y - 5} M ${x + 12} ${y} L ${x} ${y} M ${x + 12} ${y} L ${x} ${y + 5}`}
      fill="none"
      stroke="currentColor"
      className="text-blue-600"
      strokeOpacity="0.75"
      strokeWidth="1.2"
    />
  )
}

/** The single bar — the one end, at the referenced table. */
function OneBar({ x, y }: { x: number; y: number }): ReactElement {
  return (
    <path
      d={`M ${x - 9} ${y - 5} L ${x - 9} ${y + 5}`}
      stroke="currentColor"
      className="text-blue-600"
      strokeOpacity="0.75"
      strokeWidth="1.4"
    />
  )
}

/**
 * The relations no declared key connects to anything.
 *
 * Split by scope, because that is the only grouping left once there are no edges — and
 * because it is the grouping that decides whether the tenant selector changes what they
 * hold. They are listed rather than drawn, and they are still selectable: a table with
 * no foreign key is still a table somebody wants to read.
 */
function Unlinked({
  names,
  byName,
  onSelect,
}: {
  names: string[]
  byName: Map<string, DbTable>
  onSelect: (name: string) => void
}): ReactElement | null {
  const tables = names
    .map((name) => byName.get(name))
    .filter((table): table is DbTable => table !== undefined)
  if (tables.length === 0) return null

  const groups: Array<{ title: string; tip: string; items: DbTable[] }> = [
    {
      title: 'Tenant-scoped',
      tip: 'They carry a tenant column, so the scope selector narrows them — they simply declare no foreign key onto another relation.',
      items: tables.filter((table) => table.tenantScoped),
    },
    {
      title: 'Platform',
      tip: 'No tenant column and no declared key. Most are owned by an embedded subsystem — a checkpointer or a graph store — that manages its own tables.',
      items: tables.filter((table) => !table.tenantScoped),
    },
  ]

  return (
    <section className="rounded-lg border border-dashed border-border p-3">
      <h3 className="eyebrow mb-2 flex items-center gap-1">
        No declared foreign key
        <Figure className="text-[0.65rem]">{tables.length}</Figure>
        <InfoTip label="Why these are not in the diagram">
          A relation with no key has no edge to draw and no column to place it in, so
          putting it in the map would mean inventing a position for it. They are named
          here instead, grouped by whether the scope selector reaches them.
        </InfoTip>
      </h3>
      <div className="flex flex-col gap-2">
        {groups.map((group) =>
          group.items.length === 0 ? null : (
            <div key={group.title} className="flex flex-wrap items-center gap-1.5">
              <span className="inline-flex items-center gap-1 text-[0.65rem] text-muted-foreground">
                {group.title}
                <Figure className="text-[0.65rem]">{group.items.length}</Figure>
                <InfoTip label={`About the ${group.title.toLowerCase()} group`}>
                  {group.tip}
                </InfoTip>
              </span>
              {group.items.map((table) => (
                <button
                  key={table.name}
                  type="button"
                  onClick={() => onSelect(table.name)}
                  title={`${table.columns.length} columns · ${estimate(table.rowEstimate)}`}
                  className={cn(
                    'inline-flex touch-manipulation items-center gap-1 rounded-md border border-l-[3px] border-y-border border-r-border bg-card px-2 py-0.5 transition-colors duration-[--dur-fast] hover:border-blue-600 hover:bg-blue-50',
                    FOCUS,
                    table.tenantScoped ? 'border-l-blue-400' : 'border-l-muted-foreground/45',
                  )}
                >
                  <Figure className="text-[0.68rem] text-foreground">{table.name}</Figure>
                  <span className="font-mono text-[0.6rem] text-muted-foreground">
                    {table.columns.length}c
                  </span>
                  {table.withheldColumns.length > 0 ? (
                    <Lock className="size-2.5 text-[color:var(--risk-ink)]" aria-hidden />
                  ) : null}
                </button>
              ))}
            </div>
          ),
        )}
      </div>
    </section>
  )
}

/**
 * One relation at full detail, with everything a declared key connects it to.
 *
 * The entity in the middle carries every column the catalog reports, typed, with its
 * primary key marked — and its withheld columns in place, locked. Each edge lands on the
 * **exact column** it references rather than on the middle of the box, which is what
 * turns a picture of boxes into something you can check a join against.
 */
function FocusView({
  near,
  onSelect,
}: {
  near: NonNullable<ReturnType<typeof neighbourhood>>
  onSelect: (name: string) => void
}): ReactElement {
  const { focus, children, parents } = near
  const rows = focus.columns.length + focus.withheldColumns.length
  const entityH = ENTITY_HEAD + rows * ENTITY_ROW + 6

  const laneH = (count: number): number =>
    count === 0 ? 0 : count * NEIGHBOUR_H + (count - 1) * NEIGHBOUR_GAP
  const childrenH = laneH(children.length)
  const parentsH = laneH(parents.length)
  const height = Math.max(entityH, childrenH, parentsH)

  const hasChildren = children.length > 0
  const hasParents = parents.length > 0
  const entityX = hasChildren ? NEIGHBOUR_W + LANE_GAP : 0
  const parentX = entityX + ENTITY_W + LANE_GAP
  const width = parentX + (hasParents ? NEIGHBOUR_W : -LANE_GAP)

  const entityY = (height - entityH) / 2
  const childY = (index: number): number =>
    (height - childrenH) / 2 + index * (NEIGHBOUR_H + NEIGHBOUR_GAP)
  const parentY = (index: number): number =>
    (height - parentsH) / 2 + index * (NEIGHBOUR_H + NEIGHBOUR_GAP)

  /** The vertical centre of one column's row inside the entity box. */
  const rowY = (column: string): number => {
    const index = focus.columns.findIndex((entry) => entry.name === column)
    if (index < 0) return entityY + entityH / 2
    return entityY + ENTITY_HEAD + index * ENTITY_ROW + ENTITY_ROW / 2
  }

  const curve = (x1: number, y1: number, x2: number, y2: number): string => {
    const bend = Math.max(28, (x2 - x1) * 0.45)
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
  }

  return (
    <div
      className="-mx-1 max-h-[38rem] overflow-auto overscroll-x-contain px-1 pb-1"
      role="group"
      aria-label={`Entity relationship diagram for ${focus.name} and its neighbours`}
    >
      <div
        className="relative mx-auto"
        style={{ width, height: height + CAPTION_H, minWidth: width }}
      >
        <svg aria-hidden className="pointer-events-none absolute inset-0" width={width} height={height}>
          {children.map((relation, index) => {
            const x1 = NEIGHBOUR_W
            const y1 = childY(index) + NEIGHBOUR_H / 2
            const x2 = entityX
            const y2 = rowY(relation.references)
            return (
              <g key={`in-${relation.table.name}-${relation.column}`}>
                <path
                  d={curve(x1, y1, x2, y2)}
                  fill="none"
                  stroke="var(--blue-600)"
                  strokeOpacity="0.7"
                  strokeWidth="1.4"
                />
                <CrowsFoot x={x1} y={y1} />
                <OneBar x={x2} y={y2} />
                <text
                  x={(x1 + x2) / 2}
                  y={(y1 + y2) / 2 - 6}
                  textAnchor="middle"
                  className="fill-muted-foreground font-mono text-[9.5px]"
                >
                  {`${relation.column} → ${relation.references}`}
                </text>
              </g>
            )
          })}
          {parents.map((relation, index) => {
            const x1 = entityX + ENTITY_W
            const y1 = rowY(relation.column)
            const x2 = parentX
            const y2 = parentY(index) + NEIGHBOUR_H / 2
            return (
              <g key={`out-${relation.table.name}-${relation.column}`}>
                <path
                  d={curve(x1, y1, x2, y2)}
                  fill="none"
                  stroke="var(--blue-600)"
                  strokeOpacity="0.7"
                  strokeWidth="1.4"
                />
                <CrowsFoot x={x1} y={y1} />
                <OneBar x={x2} y={y2} />
                <text
                  x={(x1 + x2) / 2}
                  y={(y1 + y2) / 2 - 6}
                  textAnchor="middle"
                  className="fill-muted-foreground font-mono text-[9.5px]"
                >
                  {`${relation.column} → ${relation.references}`}
                </text>
              </g>
            )
          })}
        </svg>

        {children.map((relation, index) => (
          <div
            key={`child-${relation.table.name}-${relation.column}`}
            className="absolute"
            style={{
              left: 0,
              top: childY(index),
              width: NEIGHBOUR_W,
              height: NEIGHBOUR_H,
            }}
          >
            <NeighbourNode
              relation={relation}
              side="references"
              other={focus.name}
              onSelect={() => onSelect(relation.table.name)}
            />
          </div>
        ))}

        {parents.map((relation, index) => (
          <div
            key={`parent-${relation.table.name}-${relation.column}`}
            className="absolute"
            style={{
              left: parentX,
              top: parentY(index),
              width: NEIGHBOUR_W,
              height: NEIGHBOUR_H,
            }}
          >
            <NeighbourNode
              relation={relation}
              side="referenced by"
              other={focus.name}
              onSelect={() => onSelect(relation.table.name)}
            />
          </div>
        ))}

        <div
          className="absolute"
          style={{ left: entityX, top: entityY, width: ENTITY_W }}
        >
          <Entity table={focus} />
        </div>

        {!hasChildren && !hasParents ? (
          <p
            className="absolute text-xs text-muted-foreground italic"
            style={{ left: entityX, top: entityY + entityH + 8, width: ENTITY_W }}
          >
            No declared foreign key connects this relation to another one.
          </p>
        ) : null}
      </div>
    </div>
  )
}

/** One neighbour, with the direction of the relationship said in words. */
function NeighbourNode({
  relation,
  side,
  other,
  onSelect,
}: {
  relation: Relation
  side: 'references' | 'referenced by'
  other: string
  onSelect: () => void
}): ReactElement {
  const { table } = relation
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-label={`${table.name} ${side === 'references' ? `references ${other}` : `is referenced by ${other}`} on ${relation.column}. Focus it.`}
      className={cn(
        'flex size-full touch-manipulation flex-col justify-center gap-0.5 rounded-md border border-l-[3px] border-y-border border-r-border bg-card px-2.5 py-1.5 text-left transition-colors duration-[--dur-fast] hover:border-blue-600 hover:bg-blue-50',
        FOCUS,
        table.tenantScoped ? 'border-l-blue-400' : 'border-l-muted-foreground/45',
      )}
    >
      <Figure className="truncate text-xs font-medium text-foreground">{table.name}</Figure>
      <span className="truncate font-mono text-[0.6rem] leading-3 text-muted-foreground">
        {side} · {table.tenantScoped ? 'tenant' : 'platform'} ·{' '}
        {table.rowEstimate > 0 ? `~${table.rowEstimate.toLocaleString()}` : 'no estimate'}
      </span>
    </button>
  )
}

/**
 * The focused relation as an ER entity: every column, typed, in catalog order.
 *
 * The withheld columns sit at the bottom in the same list rather than in a footnote,
 * because the point is that they are **part of this table** and still unreadable from
 * here. A column that only appeared as a badge would read as a UI decision; a locked row
 * in the column list reads as what it is — a grant.
 */
function Entity({ table }: { table: DbTable }): ReactElement {
  return (
    <div className="overflow-hidden rounded-lg border border-blue-600 bg-card shadow-[var(--shadow-card)]">
      <div
        className="flex items-center gap-1.5 border-b border-border bg-blue-50 px-3"
        style={{ height: ENTITY_HEAD }}
      >
        <Table2 className="size-3.5 shrink-0 text-blue-700" aria-hidden />
        <Figure className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">
          {table.name}
        </Figure>
        <Badge tone={table.tenantScoped ? 'graph' : 'neutral'}>
          {table.tenantScoped ? 'tenant-scoped' : 'platform'}
        </Badge>
      </div>
      <ul>
        {table.columns.map((column) => (
          <li
            key={column.name}
            className="flex items-center gap-1.5 border-b border-border/50 px-3 last:border-0"
            style={{ height: ENTITY_ROW }}
          >
            {column.isPrimaryKey ? (
              <KeyRound className="size-3 shrink-0 text-blue-700" aria-label="primary key" />
            ) : (
              <span aria-hidden className="size-3 shrink-0" />
            )}
            <Figure className="min-w-0 flex-1 truncate text-[0.7rem] text-foreground">
              {column.name}
            </Figure>
            <span className="shrink-0 truncate font-mono text-[0.62rem] text-muted-foreground">
              {column.dataType}
            </span>
            {column.nullable ? null : (
              <span
                className="shrink-0 font-mono text-[0.58rem] text-muted-foreground/70"
                title="NOT NULL"
              >
                nn
              </span>
            )}
          </li>
        ))}
        {table.withheldColumns.map((name) => (
          <li
            key={`withheld-${name}`}
            className="flex items-center gap-1.5 border-b border-border/50 bg-risk/10 px-3 last:border-0"
            style={{ height: ENTITY_ROW }}
          >
            <Lock className="size-3 shrink-0 text-[color:var(--risk-ink)]" aria-hidden />
            <Figure className="min-w-0 flex-1 truncate text-[0.7rem] text-[color:var(--risk-ink)]">
              {name}
            </Figure>
            <span className="shrink-0 font-mono text-[0.62rem] text-[color:var(--risk-ink)]">
              withheld
            </span>
            <InfoTip label={`Why ${name} is withheld`}>
              This connection has no grant on {table.name}.{name}, so the column is missing
              from information_schema itself — withheld by a Postgres COLUMN grant, not
              filtered out by application code on the way to the browser. It cannot be
              selected, ordered by or filtered on from this console at all, and the catalog
              the server builds does not contain it. It is named here because a control you
              cannot see is a control nobody can check.
            </InfoTip>
          </li>
        ))}
      </ul>
    </div>
  )
}
