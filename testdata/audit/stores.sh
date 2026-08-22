#!/bin/bash
# Audit probe: point-in-time counts across Postgres / Qdrant / Neo4j.
# Postgres read as the SERVING role aegis_app with app.tenant_id bound.
LBL="$1"
export PGPASSWORD='dqQlz5sFcwhW4GPw2hNUv_sMEdNo_Sm3'
PG() { psql -U aegis_app -h localhost -d taif_run1 -tAc "SET app.tenant_id='1'; $1" 2>/dev/null | tail -1; }
echo "--- $LBL ---"
echo "pg.documents      = $(PG 'select count(*) from documents')"
echo "pg.chunks         = $(PG 'select count(*) from chunks')"
for c in lightrag_vdb_chunks lightrag_vdb_entities lightrag_vdb_relationships aegis_mem_memory_fact_d3072 aegis_mem_memory_message_d3072; do
  n=$(curl -s "http://localhost:6333/collections/$c" | python3 -c 'import sys,json;print(json.load(sys.stdin)["result"]["points_count"])' 2>/dev/null)
  echo "qdrant.$c = $n"
done
NEO=$(curl -s -u neo4j:aegisdev1 -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"}]}' \
  http://localhost:7474/db/neo4j/tx/commit | python3 -c 'import sys,json;print(json.load(sys.stdin)["results"][0]["data"][0]["row"][0])' 2>/dev/null)
echo "neo4j.nodes       = $NEO"
NEOR=$(curl -s -u neo4j:aegisdev1 -H 'Content-Type: application/json' \
  -d '{"statements":[{"statement":"MATCH ()-[r]->() RETURN count(r) AS c"}]}' \
  http://localhost:7474/db/neo4j/tx/commit | python3 -c 'import sys,json;print(json.load(sys.stdin)["results"][0]["data"][0]["row"][0])' 2>/dev/null)
echo "neo4j.rels        = $NEOR"
