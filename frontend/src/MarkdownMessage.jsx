import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const remarkPlugins = [remarkGfm];
const components = {
  a({node, ...props}) {
    return <a {...props} target="_blank" rel="noopener noreferrer"/>;
  },
  table({node, ...props}) {
    return (
      <div className="markdown-table" tabIndex={0} role="region" aria-label="Table">
        <table {...props}/>
      </div>
    );
  }
};

export default function MarkdownMessage({children}) {
  return (
    <div className="rich-text markdown-message">
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {String(children ?? '')}
      </ReactMarkdown>
    </div>
  );
}
