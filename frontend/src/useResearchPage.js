import {useEffect, useRef, useState} from 'react';
import {request} from './api.js';

const emptyPage = () => ({items: [], next_cursor: null, has_more: false,
  loading: false, loaded: false, error: '', key: null});

export function useResearchPage({roomId, token, kind, revision, enabled = true}) {
  const key = JSON.stringify([roomId, token, kind, revision]);
  const [state, setState] = useState(emptyPage);
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
    update({...((append && previous.key === key) ? previous : emptyPage()),
      key, loading: true, error: ''});
    try {
      const query = new URLSearchParams({limit: '20'});
      if (cursor) query.set('cursor', cursor);
      const page = await request(`/rooms/${encodeURIComponent(roomId)}/${kind}?${query}`, {
        token, signal: abort.signal
      });
      if (scope.current !== key || generation.current !== requestGeneration || abort.signal.aborted) return;
      const items = append ? [...previous.items, ...page.items] : page.items;
      const unique = new Map(items.map(item => [kind === 'sources' ? item.url : item.id, item]));
      update({...page, items: [...unique.values()], key, loaded: true, loading: false, error: ''});
    } catch (error) {
      if (scope.current !== key || generation.current !== requestGeneration || abort.signal.aborted) return;
      update({...current.current, loading: false, error: error.message});
    }
  }

  useEffect(() => {
    generation.current += 1;
    // Retain a loaded tab while it is hidden, but discard another room's data.
    if (current.current.key !== key) update(emptyPage());
    else if (current.current.loading) update({...current.current, loading: false});
    if (enabled && !current.current.loaded) load();
    return () => {
      generation.current += 1;
      controller.current?.abort();
    };
  }, [key, enabled]);

  const page = state.key === key ? state : emptyPage();
  return {...page, loadMore: () => load(page.loaded && Boolean(page.next_cursor))};
}
