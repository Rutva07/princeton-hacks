import React, {memo, useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {Background, Controls, Handle, MarkerType, MiniMap, Position,
  ReactFlow, useNodesInitialized, useNodesState, useReactFlow, useUpdateNodeInternals} from '@xyflow/react';
import {BookOpen, ExternalLink, GitBranch, LoaderCircle, Maximize2, MessageSquare,
  RefreshCw, X} from 'lucide-react';
import {useSourceGraph} from './useSourceGraph.js';
import {focusedGraph, layoutGraph} from './graphLayout.js';
import '@xyflow/react/dist/style.css';
import './sourceGraph.css';

const ResearchNode = memo(function ResearchNode({id, data, selected}) {
  const query = data.kind === 'query';
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, data.direction, updateNodeInternals]);
  return (
    <div className={`dependency-node ${query ? 'query-node' : 'source-node'} ${selected ? 'is-selected' : ''}`}>
      {!query && <Handle type="target" position={data.direction === 'TB' ? Position.Top : Position.Left} isConnectable={false}/>}
      <span className="dependency-node-kind">
        {query ? <MessageSquare size={12}/> : <BookOpen size={12}/>}
        {query ? 'QUERY' : 'SOURCE'} {data.year ? `· ${data.year}` : ''}
      </span>
      <strong title={data.title}>{data.title}</strong>
      <small>{query ? `${data.sourceCount} sources found`
        : data.queryCount > 1 ? `Shared by ${data.queryCount} loaded queries` : 'Select to inspect source'}</small>
      <Handle type="source" position={data.direction === 'TB' ? Position.Bottom : Position.Right} isConnectable={false}/>
    </div>
  );
});
const nodeTypes = {research: ResearchNode};
const fitOptions = {padding: 0.18, maxZoom: 1};
const graphAriaLabels = {'controls.zoomIn.ariaLabel': 'Zoom in', 'controls.zoomOut.ariaLabel': 'Zoom out',
  'controls.fitView.ariaLabel': 'Fit graph to view'};
const minimapNodeColor = node => node.data.kind === 'query' ? '#185c49' : '#dcebe2';

function AutoFit({container, layoutKey}) {
  const initialized = useNodesInitialized();
  const {fitView} = useReactFlow();
  useEffect(() => {
    if (!initialized || !container.current) return;
    let firstFrame;
    let secondFrame;
    const fit = () => {
      cancelAnimationFrame(firstFrame);
      cancelAnimationFrame(secondFrame);
      firstFrame = requestAnimationFrame(() => {
        secondFrame = requestAnimationFrame(() => {
          if (container.current?.clientWidth && container.current?.clientHeight) fitView(fitOptions);
        });
      });
    };
    const observer = new ResizeObserver(fit);
    observer.observe(container.current);
    fit();
    return () => { observer.disconnect(); cancelAnimationFrame(firstFrame); cancelAnimationFrame(secondFrame); };
  }, [initialized, container, layoutKey, fitView]);
  return null;
}

function GraphCanvas({graph, focused, expanded, selected, onSelect, positions}) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const container = useRef(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [wide, setWide] = useState(() => window.matchMedia('(min-width:821px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(min-width:821px)');
    const change = () => setWide(media.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  const direction = expanded && wide ? 'TB' : 'LR';
  const visible = useMemo(() => focusedGraph(graph, focused), [graph.nodes, graph.edges, focused]);
  const queryCounts = useMemo(() => {
    const counts = new Map();
    for (const edge of graph.edges) {
      if (edge.kind === 'retrieved') counts.set(edge.target, (counts.get(edge.target) || 0) + 1);
    }
    return counts;
  }, [graph.edges]);
  const sourceCounts = useMemo(() => {
    const counts = new Map();
    for (const edge of graph.edges) {
      if (edge.kind === 'retrieved') counts.set(edge.source, (counts.get(edge.source) || 0) + 1);
    }
    return counts;
  }, [graph.edges]);

  useEffect(() => {
    const layout = layoutGraph(visible.nodes, visible.edges, direction);
    setNodes(layout.map(node => ({id: node.id, type: 'research',
      initialWidth: 196, initialHeight: 104,
      position: positions.current.get(`${direction}:${node.id}`) || node.position,
      data: {...node, direction, queryCount: queryCounts.get(node.id) || 0,
        sourceCount: sourceCounts.get(node.id) || 0},
      selected: node.id === selectedRef.current,
      ariaLabel: `${node.kind === 'query' ? 'Query' : 'Source'}: ${node.title}`})));
  }, [visible, queryCounts, sourceCounts, direction, positions, setNodes]);

  // React Flow owns selection through onNodesChange. The inspector observes it;
  // writing the inspector's selection back into nodes can replay a stale selection.
  const handleSelectionChange = useCallback(({nodes: chosen}) => {
    onSelect(chosen.length === 1 ? chosen[0].id : null);
  }, [onSelect]);
  const handleNodeDragStop = useCallback((_, node) => {
    positions.current.set(`${direction}:${node.id}`, node.position);
  }, [direction, positions]);

  const edges = useMemo(() => visible.edges.map(edge => ({...edge,
    sourceHandle: null, targetHandle: null,
    type: edge.kind === 'cites' ? 'default' : 'smoothstep',
    markerEnd: {type: MarkerType.ArrowClosed, color: edge.kind === 'cites' ? '#c16b42' : '#609982'},
    style: {stroke: edge.kind === 'cites' ? '#c16b42' : '#609982',
      strokeWidth: edge.kind === 'cites' ? 2 : 1.5,
      strokeDasharray: edge.kind === 'cites' ? '6 4' : undefined,
      opacity: selected && edge.source !== selected && edge.target !== selected ? 0.25 : 1},
    ariaLabel: edge.kind === 'cites' ? 'Citing paper to cited paper' : 'Query to retrieved source'
  })), [visible.edges, selected]);

  return (
    <ReactFlow ref={container} nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange}
      onNodeDragStop={handleNodeDragStop} onSelectionChange={handleSelectionChange}
      nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null}
      edgesFocusable={false} minZoom={0.08} maxZoom={2.5} fitView fitViewOptions={fitOptions}
      zoomOnPinch zoomOnScroll panOnDrag onlyRenderVisibleElements
      ariaLabelConfig={graphAriaLabels}>
      <AutoFit container={container} layoutKey={`${focused}:${direction}:${graph.nodes.length}`}/>
      <Background gap={20} size={1} color="#cbd7cd"/>
      <Controls showInteractive={false} fitViewOptions={fitOptions}/>
      {expanded && <MiniMap pannable zoomable nodeColor={minimapNodeColor}/>}
    </ReactFlow>
  );
}

function externalUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export default function SourceGraph({roomId, token, revision, active, drawerOpen}) {
  const [expanded, setExpanded] = useState(false);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width:820px)').matches);
  useEffect(() => {
    const media = window.matchMedia('(max-width:820px)');
    const change = () => setMobile(media.matches);
    media.addEventListener('change', change);
    return () => media.removeEventListener('change', change);
  }, []);
  const visible = active && (!mobile || drawerOpen || expanded);
  const graph = useSourceGraph({roomId, token, revision, enabled: visible});
  const [focused, setFocused] = useState('');
  const [selected, setSelected] = useState(null);
  const dialog = useRef(null);
  const expandButton = useRef(null);
  const wasExpanded = useRef(false);
  const positions = useRef(new Map());
  const queries = graph.nodes.filter(node => node.kind === 'query');
  const sources = graph.nodes.filter(node => node.kind === 'source');
  const citationCount = graph.edges.filter(edge => edge.kind === 'cites').length;
  const missingReferences = sources.filter(node => node.citation_data === 'unavailable').length;
  const selectedNode = graph.nodes.find(node => node.id === selected);
  const selectedUrl = selectedNode && externalUrl(selectedNode.url);

  useEffect(() => {
    if (!graph.nodes.some(node => node.id === focused)) setFocused('');
    if (!graph.nodes.some(node => node.id === selected)) setSelected(null);
  }, [graph.nodes]);

  useEffect(() => {
    if (expanded && active) dialog.current?.showModal();
    else dialog.current?.close();
    if (!expanded && wasExpanded.current) expandButton.current?.focus();
    wasExpanded.current = expanded;
  }, [expanded, active]);

  if (!visible) return null;

  const contents = (
    <section className={`source-graph ${expanded ? 'graph-expanded' : ''}`} aria-label="Citation graph">
      <div className="graph-heading">
        <h3 id="source-graph-heading">Citation graph</h3>
        <div className="graph-heading-actions">
          <button className="graph-icon-button" onClick={graph.refresh} disabled={graph.loading}
            aria-label="Refresh graph" title="Refresh graph"><RefreshCw size={15}/></button>
          <button ref={expandButton} className="graph-icon-button" onClick={() => setExpanded(!expanded)}
            aria-label={expanded ? 'Close expanded graph' : 'Expand graph'} title={expanded ? 'Close expanded graph' : 'Expand graph'}>
            {expanded ? <X size={17}/> : <Maximize2 size={15}/>}
          </button>
        </div>
      </div>
      <p className="graph-summary" role="status">
        {queries.length}{queries.length < graph.query_total ? ' of ' + graph.query_total : ''} {graph.query_total === 1 ? 'query' : 'queries'}
        {' · '}{sources.length} {sources.length === 1 ? 'source' : 'sources'}
        {' · '}{citationCount} citation {citationCount === 1 ? 'link' : 'links'}
      </p>
      {queries.length > 0 && <label className="graph-query-filter">Focus on a query
        <select value={focused} onChange={event => { setFocused(event.target.value); setSelected(null); }}>
          <option value="">All loaded queries</option>
          {queries.map(query => <option key={query.id} value={query.id}>{query.title.slice(0, 100)}</option>)}
        </select>
      </label>}
      <div className="graph-legend" aria-label="Graph legend">
        <span><i className="query-key"/>Query</span><span><i className="source-key"/>Source</span>
        <span><i className="retrieved-key"/>Found for query</span><span><i className="citation-key"/>Cites →</span>
      </div>
      {missingReferences > 0 && <p className="graph-coverage">
        Reference data is unavailable for {missingReferences} source{missingReferences === 1 ? '' : 's'}; some citation links may be missing.
      </p>}
      {graph.error && <div className="graph-error" role="alert">Could not load graph: {graph.error}
        <button onClick={graph.loadMore}>Retry graph</button></div>}
      <div className="source-graph-canvas" aria-busy={graph.loading}>
        {graph.nodes.length > 0 ? <GraphCanvas
          key={`${focused}:${expanded}:${queries.length}`} graph={graph} focused={focused} expanded={expanded}
          selected={selected} onSelect={setSelected} positions={positions}/>
          : <div className="graph-empty">
            {graph.loading ? <LoaderCircle className="spin" size={25}/> : <GitBranch size={30}/>}
            <strong>{graph.loading ? 'Mapping your research…' : 'Your research, connected.'}</strong>
            <p>{graph.loading ? 'Loading query links and available citation data.'
              : 'Ask @chatbot a research question. Its sources will connect to that query here.'}</p>
          </div>}
        {graph.loading && graph.nodes.length > 0 && <div className="graph-loading" role="status">
          <LoaderCircle className="spin" size={13}/> Loading more queries…</div>}
      </div>
      <div className="graph-inspector" aria-live="polite">
        {selectedNode ? <>
          <span className="kicker">{selectedNode.kind === 'query' ? 'SELECTED QUERY' : 'SELECTED SOURCE'}</span>
          <strong>{selectedNode.title}</strong>
          {selectedNode.authors && <p>{selectedNode.authors}</p>}
          {selectedNode.kind === 'source' && selectedNode.citation_data === 'unavailable' &&
            <p>Citation data is unavailable for this paper.</p>}
          {selectedUrl && <a href={selectedUrl} target="_blank" rel="noopener noreferrer">
            Open original source <ExternalLink size={12}/></a>}
        </> : <p>Select a node to see its details.</p>}
      </div>
      {graph.has_more && <div className="graph-history">
        <button onClick={graph.loadMore} disabled={graph.loading}>Load older queries</button>
      </div>}
    </section>
  );

  return <>
    {!expanded && contents}
    {expanded && createPortal(<dialog ref={dialog} className="source-graph-dialog" aria-labelledby="source-graph-heading"
      onCancel={() => setExpanded(false)} onClose={() => setExpanded(false)}
      onClick={event => { if (event.target === event.currentTarget) setExpanded(false); }}>
      {contents}
    </dialog>, document.body)}
  </>;
}
