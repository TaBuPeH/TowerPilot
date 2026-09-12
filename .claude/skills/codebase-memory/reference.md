# Codebase Memory — Query Recipes

> **Updated:** 2026-07-10

Sidecar for [SKILL.md](SKILL.md). Concrete `mcp__codebase-memory__*` calls. Substitute `<project>` with the path-derived project name (SKILL.md Rule 6).

## Query Recipes

### Cross-service: which services does a repo call?

The cross-service edge is `CROSS_GRPC_CALLS`, from the caller node to a `Route` node whose QN is `__grpc__<Service>/<Method>`. Edge props: `url_path` = service, `target_function` = RPC method, `target_file` = proto source, `target_project` = the proto repo.

```cypher
// query_graph, project=<caller repo>
MATCH ()-[e:CROSS_GRPC_CALLS]->(r:Route)
RETURN r.qualified_name AS service_method, e.properties AS props
```

Or follow it as a traversal from a specific function:

```
trace_path function_name:<fn> project:<repo> mode:cross_service direction:outbound
```

`mode:cross_service` follows `CROSS_GRPC_CALLS` (plus HTTP/async/CHANNEL/GraphQL/tRPC cross-edges) to hop into the called services.

### Callers / callees / blast radius

```
trace_path function_name:X project:<repo> mode:calls direction:inbound   // who calls X
trace_path function_name:X project:<repo> mode:calls direction:outbound  // what X calls
trace_path function_name:X project:<repo> mode:calls direction:both risk_labels:true  // impact, hop-risk tagged
```

### Data flow of a value

```
trace_path function_name:X project:<repo> mode:data_flow parameter_name:<arg>
```

### Complexity / hot paths (one query finds all candidates)

```cypher
// query_graph, project=<repo>
MATCH (f:Function)
WHERE f.transitive_loop_depth >= 3 OR f.linear_scan_in_loop >= 1 OR f.unguarded_recursion = 1
RETURN f.qualified_name, f.transitive_loop_depth, f.linear_scan_in_loop, f.recursive
ORDER BY f.transitive_loop_depth DESC
```

Queryable complexity props on every `Function`/`Method`: `complexity` (cyclomatic), `cognitive`, `loop_count`, `loop_depth`, `transitive_loop_depth` (interprocedural worst-case nesting propagated along CALLS), `linear_scan_in_loop` (hidden O(n²) find/contains/indexOf in a loop), `alloc_in_loop`, `recursion_in_loop`, `unguarded_recursion`, `recursive`, `param_count`, `max_access_depth`.

### Files that parsed incompletely (missed graph)

```cypher
// query_graph, project=<repo>, graph="missed"
MATCH (f:File) WHERE f.kind = "parse_partial"
RETURN f.file_path, f.detail
```

`detail` gives the 1-based line ranges that may be missing from the code graph. Grep those regions directly.

### What's indexed

```
list_projects
```

## Notes

- **Bidirectional edges.** The cross-repo pass writes edges into BOTH the caller and target DBs, so a per-project `CROSS_GRPC_CALLS` count on the caller is the outbound view; the proto/target repo carries the reverse.
- **Service name ≠ implementing repo.** The edge names the proto **service** (`UserService`), not the repo that implements it. Mapping service → implementing repo is a further hop cbm does not draw.
- **Row ceiling.** `query_graph` caps at 100k rows; add `LIMIT` in the Cypher or use `search_graph` with offset/limit for broad browsing.
- **CLI parity (for scripts/refresh, not MCP).** The cross-repo pass on the CLI takes a bare glob: `--target-projects '*'` — NOT `'["*"]'` (parsed as a literal project name → 0 matches). The MCP `index_repository` tool takes the JSON array form.
