import dagre from '@dagrejs/dagre';

export function layoutGraph(nodes, edges, direction = 'LR') {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({rankdir: direction, nodesep: 32, ranksep: direction === 'TB' ? 72 : 100, marginx: 30, marginy: 30});
  for (const node of nodes) graph.setNode(node.id, {width: 196, height: 104});
  for (const edge of edges) graph.setEdge(edge.source, edge.target);
  dagre.layout(graph);
  return nodes.map(node => {
    const position = graph.node(node.id);
    return {...node, position: {x: position.x - 98, y: position.y - 52}};
  });
}

export function focusedGraph(graph, queryId) {
  if (!queryId) return {nodes: graph.nodes, edges: graph.edges};
  const ids = new Set([queryId]);
  for (const edge of graph.edges) {
    if (edge.kind === 'retrieved' && edge.source === queryId) ids.add(edge.target);
  }
  // Include citation neighbors of this query's sources, without bringing other queries in.
  const direct = new Set(ids);
  for (const edge of graph.edges) {
    if (edge.kind === 'cites' && (direct.has(edge.source) || direct.has(edge.target))) {
      ids.add(edge.source);
      ids.add(edge.target);
    }
  }
  return {nodes: graph.nodes.filter(node => ids.has(node.id)),
    edges: graph.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target))};
}
