export async function request(path, {token, ...options} = {}) {
  const response = await fetch('/api' + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? {Authorization: `Bearer ${token}`} : {})
    }
  });

  if (!response.ok) {
    let data = {};
    try {
      data = await response.json();
    } catch {}
    throw new Error(data.detail || 'Request failed');
  }

  return response.json();
}
