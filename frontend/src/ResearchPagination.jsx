import React from 'react';
import {LoaderCircle} from 'lucide-react';

export default function ResearchPagination({page, label, total}) {
  return (
    <div className="research-pagination">
      <p role="status" aria-live="polite">
        {page.loading ? <><LoaderCircle className="spin" size={12}/> Loading {label}…</>
          : page.items.length > 0 ? `Showing ${page.items.length} of ${total ?? page.items.length}` : null}
      </p>
      {page.error && <p role="alert">Could not load {label}: {page.error}</p>}
      {(page.has_more || page.error) && (
        <button type="button" disabled={page.loading} onClick={page.loadMore}>
          {page.error ? `Retry loading ${label}` : `Load more ${label}`}
        </button>
      )}
    </div>
  );
}
