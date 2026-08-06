import { Waypoints } from 'lucide-react'
import type { ReactElement } from 'react'

import { getGraph } from '@/api/client'
import { useAsync } from '@/components/admin/useAsync'
import { ErrorRow, LoadingRow } from '@/components/common/StateRow'
import { KnowledgeGraph } from '@/components/graph/KnowledgeGraph'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { initialRunState } from '@/state/runReducer'

interface Props {
  token: string | null
}

/**
 * The Memory visual anchor (§4.3): the subject's knowledge graph, rendered idle
 * (no run in flight) so it gently breathes as the resting centrepiece. Reuses the
 * shared `KnowledgeGraph` read-only, feeding it the empty run state.
 */
export function KnowledgeGraphTile({ token }: Props): ReactElement {
  const { state } = useAsync(() => getGraph(token), [token])

  if (state.status === 'ready') {
    return <KnowledgeGraph base={state.data} state={initialRunState} beat={null} idle />
  }

  return (
    <Card className="flex h-full flex-col overflow-hidden">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Waypoints className="size-4 text-graph-ink" />
        <CardTitle>Knowledge graph</CardTitle>
      </CardHeader>
      <div className="grid flex-1 place-items-center px-5">
        {state.status === 'loading' && <LoadingRow label="Loading graph…" />}
        {state.status === 'error' && <ErrorRow message={state.message} />}
      </div>
    </Card>
  )
}
