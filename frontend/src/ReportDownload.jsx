import React, {useState} from 'react';
export default function ReportDownload({roomId, jobId, token}) {
  const [error, setError] = useState('');
  async function download() {
    try {
      const response = await fetch(`/api/rooms/${roomId}/reports/${jobId}`, {headers: {Authorization: `Bearer ${token}`}});
      if (!response.ok) throw new Error('Report is not available yet.');
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `experiment-${jobId}.md`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err.message); }
  }
  return <><button onClick={download}>Download report</button>{error && <p role="alert">{error}</p>}</>;
}
