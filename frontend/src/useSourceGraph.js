import {useEffect, useRef, useState} from 'react';
import {request} from './api.js';

const empty = () => ({nodes: [], edges: [], query_total: 0, has_more: false,
  next_cursor: null, loading: false, loaded: false, error: '', key: null});

export function useSourceGraph({roomId, token, revision, enabled}) {
  const key = JSON.stringify([roomId, token, revision]);
  const [state, setState] = useState(empty);
  const current = useRef(state);
  const scope = useRef(key);
  const generation = useRef(0);
  const controller = useRef(null);
  scope.current = key;

  function update(value) {
    current.current = value;
    setState(value);
  }

  async function load(append = false) {
    if (!enabled || !roomId || !token || current.current.loading) return;
    const previous = current.current;
    const cursor = append ? previous.next_cursor : null;
    const requestGeneration = generation.current;
    const abort = new AbortController();
    controller.current = abort;
    update({...((append && previous.key === key) ? previous : empty()), key, loading: true});
    try {
      const query = new URLSearchParams({limit: '8'});
      if (cursor) query.set('cursor', cursor);
      const data = await request(`/rooms/${encodeURIComponent(roomId)}/source-graph?${query}`,
        {token, signal: abort.signal});
      if (scope.current !== key || generation.current !== requestGeneration || abort.signal.aborted) return;
      const merge = name => [...new Map([
        ...(append ? previous[name] : []), ...data[name]
      ].map(item => [item.id, item])).values()];
      update({...data, nodes: merge('nodes'), edges: merge('edges'), key,
        loading: false, loaded: true, error: ''});
    } catch (error) {
      if (scope.current !== key || generation.current !== requestGeneration || abort.signal.aborted) return;
      update({...current.current, loading: false, error: error.message});
    }
  }

  useEffect(() => {
    generation.current += 1;
    if (current.current.key !== key) update(empty());
    else if (current.current.loading) update({...current.current, loading: false});
    if (enabled && !current.current.loaded) load();
    return () => {
      generation.current += 1;
      controller.current?.abort();
    };
  }, [key, enabled]);

  const graph = state.key === key ? state : empty();
  return {...graph, loadMore: () => load(graph.loaded && Boolean(graph.next_cursor)), refresh: () => load()};
}
