import { apiFetch } from './client';

export async function fetchSummary(merchantId) {
  const res = await apiFetch(`/api/v1/dashboard/summary?merchant_id=${encodeURIComponent(merchantId)}`);
  return res.ok ? await res.json() : null;
}

export async function fetchInventory(merchantId) {
  const res = await apiFetch(`/api/v1/dashboard/inventory?merchant_id=${encodeURIComponent(merchantId)}`);
  return res.ok ? await res.json() : [];
}

export async function fetchParties(merchantId) {
  const res = await apiFetch(`/api/v1/dashboard/parties?merchant_id=${encodeURIComponent(merchantId)}`);
  return res.ok ? await res.json() : [];
}

export async function fetchTransactions(merchantId) {
  const res = await apiFetch(`/api/v1/dashboard/transactions?merchant_id=${encodeURIComponent(merchantId)}`);
  return res.ok ? await res.json() : [];
}

export async function fetchCandidates(merchantId) {
  const res = await apiFetch(`/api/v1/dashboard/candidates?merchant_id=${encodeURIComponent(merchantId)}`);
  return res.ok ? await res.json() : [];
}

export async function confirmCandidate(merchantId, candidateId) {
  const res = await apiFetch('/api/v1/dashboard/candidates/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      merchant_id: merchantId,
      candidate_id: candidateId,
      confirmed_by: merchantId,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Held for review');
  }
  return data;
}

export async function cancelCandidate(merchantId, candidateId) {
  const res = await apiFetch('/api/v1/dashboard/candidates/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      merchant_id: merchantId,
      candidate_id: candidateId,
      confirmed_by: merchantId,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Unable to cancel candidate');
  }
  return data;
}

export async function clarifyCandidate(candidateId, answer, revision) {
  const res = await apiFetch(`/api/v1/dashboard/candidates/${candidateId}/clarify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer, revision }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Unable to save clarification reply.');
  }
  return data;
}

export async function reviewCandidate(candidateId, candidateFacts, note) {
  const res = await apiFetch(`/api/v1/dashboard/candidates/${candidateId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ candidate: candidateFacts, note }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail || 'Still requires review.');
  }
  return data;
}

export async function extractTransaction({ merchantId, businessName, audioFile, demoText }) {
  const fields = {
    merchant_id: merchantId,
    message_id: crypto.randomUUID(),
    merchant_business_name: businessName || 'My Business',
    stage: true,
  };

  let response;
  if (audioFile) {
    const form = new FormData();
    Object.entries(fields).forEach(([k, v]) => form.append(k, v));
    form.append('file', audioFile);
    response = await apiFetch('/api/v1/extract/media', { method: 'POST', body: form });
  } else {
    response = await apiFetch('/api/v1/extract/text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...fields, raw_text: demoText }),
    });
  }

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || 'Extraction failed');
  }
  return data;
}
