// Thin transport layer. No UI, no state.

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : `${response.status} ${response.statusText}`;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return body;
}

const postJSON = (url, payload) =>
  request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });

export const health = () => request('/api/health');
export const startLoad = (url, maxHeight) => postJSON('/api/load', { url, max_height: maxHeight });
export const startRender = (spec) => postJSON('/api/render', spec);
export const startFit = (spec, targetBytes) =>
  postJSON('/api/fit', { spec, target_bytes: Math.round(targetBytes) });
export const estimateSize = (spec) => postJSON('/api/estimate', { spec });

/**
 * Poll a background job until it finishes.
 * @returns the job's result payload
 */
export async function awaitJob(jobId, onProgress, intervalMs = 300) {
  for (;;) {
    const job = await request(`/api/job/${jobId}`);
    if (onProgress) onProgress(job.percent ?? 0, job.message || '');
    if (job.state === 'done') return job.result;
    if (job.state === 'error') throw new Error(job.error || 'The job failed.');
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}
