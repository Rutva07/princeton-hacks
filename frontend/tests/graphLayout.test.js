import assert from 'node:assert/strict';
import test from 'node:test';
import {focusedGraph, layoutGraph} from '../src/graphLayout.js';

const graph = {
  nodes: [{id: 'q1', kind: 'query'}, {id: 'q2', kind: 'query'},
    {id: 'a', kind: 'source'}, {id: 'b', kind: 'source'}, {id: 'c', kind: 'source'}],
  edges: [{id: 'q1-a', source: 'q1', target: 'a', kind: 'retrieved'},
    {id: 'q2-a', source: 'q2', target: 'a', kind: 'retrieved'},
    {id: 'q2-c', source: 'q2', target: 'c', kind: 'retrieved'},
    {id: 'a-b', source: 'a', target: 'b', kind: 'cites'}]
};

test('focusing a query preserves shared sources and citation neighbors', () => {
  const result = focusedGraph(graph, 'q1');
  assert.deepEqual(result.nodes.map(node => node.id), ['q1', 'a', 'b']);
  assert.deepEqual(result.edges.map(edge => edge.id), ['q1-a', 'a-b']);
  assert.equal(result.edges[1].source, 'a');
  assert.equal(result.edges[1].target, 'b');
});

test('automatic layout separates nodes and handles citation cycles', () => {
  const edges = [...graph.edges, {source: 'b', target: 'a', kind: 'cites'}];
  const nodes = layoutGraph(graph.nodes, edges);
  assert.equal(nodes.length, graph.nodes.length);
  for (const node of nodes) {
    assert.ok(Number.isFinite(node.position.x) && Number.isFinite(node.position.y));
    for (const other of nodes) {
      if (node.id === other.id) continue;
      assert.ok(Math.abs(node.position.x - other.position.x) >= 196 ||
        Math.abs(node.position.y - other.position.y) >= 104, `${node.id} overlaps ${other.id}`);
    }
  }
});
