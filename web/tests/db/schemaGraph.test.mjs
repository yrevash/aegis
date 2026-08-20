/**
 * The ER diagram's geometry, tested where it can actually go wrong.
 *
 * Three claims carry the picture, and each one is invisible in a rendered tree:
 *
 * - **Every arrow runs the same way.** That is what makes a layered diagram readable
 *   rather than a hairball, and it is a property of `rankTables`, not of the drawing.
 * - **Nothing is dropped in silence.** A key pointing outside the catalog and a table
 *   with no key at all are both *facts about the schema*; the layout returns them
 *   instead of quietly leaving them out of the picture.
 * - **A reference cycle terminates.** No schema here has one, which is exactly why a
 *   cycle arriving must be a slightly odd diagram rather than a hung tab.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

const {
  NODE_H,
  NODE_W,
  layoutMap,
  neighbourhood,
  rankTables,
  schemaEdges,
  schemaStats,
} = await import('../../src/components/db/schemaGraph.ts')

/** One table, with only the fields the graph reads spelled out. */
function table(name, { fks = [], scoped = true, rows = 0, withheld = [], cols = ['id'] } = {}) {
  return {
    name,
    columns: cols.map((column) => ({
      name: column,
      dataType: 'integer',
      nullable: false,
      isPrimaryKey: column === 'id',
    })),
    primaryKey: ['id'],
    foreignKeys: fks.map(([column, referencesTable, referencesColumn = 'id']) => ({
      column,
      referencesTable,
      referencesColumn,
    })),
    rowEstimate: rows,
    tenantScoped: scoped,
    withheldColumns: withheld,
  }
}

/** A miniature of the real shape: a hub, two middles, two leaves, two loners. */
const schema = [
  table('tenants', { scoped: false, rows: 4, cols: ['id', 'name'] }),
  table('users', {
    fks: [['tenant_id', 'tenants']],
    rows: 12,
    withheld: ['password_hash'],
    cols: ['id', 'tenant_id', 'email'],
  }),
  table('documents', { fks: [['tenant_id', 'tenants']], rows: 30 }),
  table('chunks', {
    fks: [
      ['document_id', 'documents'],
      ['tenant_id', 'tenants'],
    ],
    rows: 900,
  }),
  table('settings', {
    fks: [
      ['tenant_id', 'tenants'],
      ['user_id', 'users'],
    ],
  }),
  table('checkpoints', { scoped: false }),
  table('eval_results', {}),
]

test('a key whose target is not in the catalog is counted, never drawn', () => {
  const withStranger = [...schema, table('outbox', { fks: [['queue_id', 'not_granted']] })]
  const { edges, unresolved } = schemaEdges(withStranger)
  assert.equal(unresolved, 1)
  assert.ok(edges.every((edge) => edge.to !== 'not_granted'))
})

test('a self-reference is counted separately — it is not a layer relation', () => {
  const { edges, selfReferences } = schemaEdges([
    ...schema,
    table('folders', { fks: [['parent_id', 'folders']] }),
  ])
  assert.equal(selfReferences, 1)
  assert.ok(edges.every((edge) => edge.from !== edge.to))
})

test('rank puts a referencing table strictly beyond the one it references', () => {
  const { edges } = schemaEdges(schema)
  const rank = rankTables(edges)
  assert.equal(rank.get('tenants'), 0)
  assert.equal(rank.get('users'), 1)
  assert.equal(rank.get('documents'), 1)
  // chunks -> documents -> tenants is the longest path it sits on.
  assert.equal(rank.get('chunks'), 2)
  assert.equal(rank.get('settings'), 2)
  for (const edge of edges) assert.ok(rank.get(edge.from) > rank.get(edge.to))
})

test('a reference cycle terminates instead of recursing forever', () => {
  const cyclic = [
    table('a', { fks: [['b_id', 'b']] }),
    table('b', { fks: [['a_id', 'a']] }),
  ]
  const { edges } = schemaEdges(cyclic)
  const rank = rankTables(edges)
  assert.ok(Number.isFinite(rank.get('a')))
  assert.ok(Number.isFinite(rank.get('b')))
})

test('every arrow in the map runs left to right, and no two boxes overlap', () => {
  const map = layoutMap(schema)
  for (const edge of map.edges) assert.ok(edge.x1 < edge.x2, `${edge.from} -> ${edge.to}`)

  for (const a of map.nodes) {
    for (const b of map.nodes) {
      if (a === b) continue
      const apart =
        a.x + NODE_W <= b.x || b.x + NODE_W <= a.x || a.y + NODE_H <= b.y || b.y + NODE_H <= a.y
      assert.ok(apart, `${a.name} overlaps ${b.name}`)
    }
  }
})

test('every relation is either placed or named as unlinked — none is lost', () => {
  const map = layoutMap(schema)
  const placed = map.nodes.map((node) => node.name)
  const accounted = [...placed, ...map.unlinked].sort()
  assert.deepEqual(accounted, schema.map((entry) => entry.name).sort())
  assert.deepEqual(map.unlinked, ['checkpoints', 'eval_results'])
})

test('a schema with no keys at all still returns every table', () => {
  const flat = [table('a', { fks: [] }), table('b', { fks: [] })]
  const map = layoutMap(flat)
  assert.deepEqual(map.nodes, [])
  assert.deepEqual(map.unlinked, ['a', 'b'])
  assert.equal(map.width, 0)
})

test('a neighbourhood splits the two directions and names the joining column', () => {
  const near = neighbourhood(schema, 'users')
  assert.deepEqual(
    near.children.map((relation) => [relation.table.name, relation.column]),
    [['settings', 'user_id']],
  )
  assert.deepEqual(
    near.parents.map((relation) => [relation.table.name, relation.column]),
    [['tenants', 'tenant_id']],
  )
  assert.equal(neighbourhood(schema, 'no_such_table'), null)
})

test('the stats name the most-referenced relation and count what is withheld', () => {
  const stats = schemaStats(schema)
  assert.equal(stats.tables, 7)
  assert.equal(stats.tenantScoped, 5)
  assert.equal(stats.platform, 2)
  assert.equal(stats.foreignKeys, 6)
  assert.equal(stats.unlinked, 2)
  assert.deepEqual(stats.hub, { name: 'tenants', referencedBy: 4 })
  assert.equal(stats.withheld, 1)
  assert.deepEqual(stats.withheldTables, ['users'])
})
