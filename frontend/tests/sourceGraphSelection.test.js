import assert from 'node:assert/strict';
import test from 'node:test';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('switching sources keeps the inspector and both graph views stable', async t => {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', {
    url: 'http://localhost/', pretendToBeVisual: true
  });
  const {window} = dom;
  for (const name of ['window', 'document', 'HTMLElement', 'Element', 'SVGElement',
    'SVGSVGElement', 'HTMLDialogElement', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
    Object.defineProperty(globalThis, name, {configurable: true, value: window[name]});
  }
  Object.defineProperty(globalThis, 'navigator', {configurable: true, value: window.navigator});
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia = () => ({matches: false, addEventListener() {}, removeEventListener() {}});
  class ResizeObserver {
    constructor(notify) { this.notify = notify; this.pending = new Set(); }
    observe(target) {
      const timer = setTimeout(() => {
        this.pending.delete(timer);
        this.notify([{target}], this);
      }, 0);
      this.pending.add(timer);
    }
    unobserve() {}
    disconnect() { for (const timer of this.pending) clearTimeout(timer); this.pending.clear(); }
  }
  class DOMMatrixReadOnly {
    constructor(value = '') { this.m22 = Number(value.match(/scale\(([^)]+)\)/)?.[1] || 1); }
  }
  Object.assign(globalThis, {ResizeObserver, DOMMatrixReadOnly});
  Object.assign(window, {ResizeObserver, DOMMatrixReadOnly});
  const dimension = (element, axis) => {
    const raw = element.style[axis];
    if (raw.endsWith('%')) return element.parentElement?.[axis === 'width' ? 'offsetWidth' : 'offsetHeight'] || 800;
    if (parseFloat(raw)) return parseFloat(raw);
    if (element.classList.contains('react-flow__handle')) return 6;
    if (element.classList.contains('react-flow__node')) return axis === 'width' ? 196 : 104;
    return axis === 'width' ? 800 : 600;
  };
  for (const [property, axis] of Object.entries({offsetWidth: 'width', clientWidth: 'width',
    offsetHeight: 'height', clientHeight: 'height'})) {
    Object.defineProperty(window.HTMLElement.prototype, property, {configurable: true,
      get() { return dimension(this, axis); }});
  }
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    return new window.DOMRect(0, 0, this.offsetWidth, this.offsetHeight);
  };
  window.SVGElement.prototype.getBBox = () => ({x: 0, y: 0, width: 0, height: 0});
  window.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  window.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };

  const fixture = {
    nodes: [{id: 'query:1', kind: 'query', title: 'A research question'},
      ...['Alpha', 'Beta', 'Gamma'].map(title => ({id: `source:${title}`, kind: 'source', title,
        url: `https://example.org/${title}`, citation_data: 'available'}))],
    edges: ['Alpha', 'Beta', 'Gamma'].map(title => ({id: `found:${title}`,
      source: 'query:1', target: `source:${title}`, kind: 'retrieved'})),
    query_total: 1, has_more: false, next_cursor: null
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ok: true, json: async () => fixture});
  // Compile the actual component; only styles are omitted from this DOM test.
  const bundle = await build({entryPoints: ['src/SourceGraph.jsx'], bundle: true, write: false,
    platform: 'node', format: 'esm', packages: 'external', plugins: [{name: 'test-styles', setup(builder) {
      builder.onResolve({filter: /\.css$/}, args => ({path: args.path, namespace: 'test-style'}));
      builder.onLoad({filter: /.*/, namespace: 'test-style'}, () => ({contents: '', loader: 'js'}));
    }}]});
  const source = bundle.outputFiles[0].text.replace(/from "([^"]+)"/g,
    (_, name) => `from ${JSON.stringify(import.meta.resolve(name))}`);
  const {default: SourceGraph} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  const {default: React, act} = await import('react');
  const {createRoot} = await import('react-dom/client');
  const errors = [];
  const originalError = console.error;
  console.error = (...args) => errors.push(args.map(String).join(' '));
  const root = createRoot(document.getElementById('root'), {onUncaughtError: error => errors.push(String(error))});
  t.after(async () => {
    await act(async () => root.unmount());
    console.error = originalError;
    globalThis.fetch = originalFetch;
    dom.window.close();
  });

  await act(async () => root.render(React.createElement(SourceGraph, {
    roomId: 'room', token: 'test-session', revision: 1, active: true, drawerOpen: true
  })));
  await act(async () => new Promise(resolve => setTimeout(resolve, 80)));

  const inspect = async title => {
    const node = document.querySelector(`.react-flow__node[data-id="source:${title}"]`);
    assert.ok(node, `Missing source ${title}`);
    await act(async () => node.dispatchEvent(new window.MouseEvent('click', {bubbles: true})));
    assert.equal(document.querySelector('.graph-inspector strong')?.textContent, title);
    assert.equal(document.querySelectorAll('.react-flow').length, 1);
    assert.equal(document.querySelectorAll('.react-flow__node.selected').length, 1);
  };
  for (const title of ['Alpha', 'Beta', 'Gamma', 'Alpha']) await inspect(title);
  await act(async () => document.querySelector('[aria-label="Expand graph"]').click());
  await act(async () => new Promise(resolve => setTimeout(resolve, 50)));
  assert.ok(document.querySelector('dialog[open]'));
  for (const title of ['Beta', 'Alpha', 'Gamma', 'Beta']) await inspect(title);
  await act(async () => document.querySelector('.react-flow__pane').dispatchEvent(
    new window.MouseEvent('click', {bubbles: true})));
  assert.equal(document.querySelector('.graph-inspector strong'), null);
  assert.equal(document.querySelectorAll('.react-flow__node.selected').length, 0);
  await inspect('Alpha');
  await act(async () => document.querySelector('[aria-label="Close expanded graph"]').click());
  await act(async () => new Promise(resolve => setTimeout(resolve, 50)));
  await inspect('Gamma');
  assert.deepEqual(errors, []);
});
