// One place that talks to the Flask backend.
// Usage:  const cars = await api('/cars');
//         await api('/login', { method: 'POST', body: { email, password } });

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

const SERVER_HELP =
  'Cannot reach the AutoHub server. Start it with "flask --app app run" and open http://localhost:5000';

export async function api(path, { method = 'GET', body } = {}) {
  let response;
  try {
    response = await fetch('/api' + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'same-origin', // sends the login cookie
    });
  } catch {
    throw new ApiError(SERVER_HELP, 0);
  }

  // If the answer isn't JSON, we're probably not talking to Flask (e.g. Live Server).
  if (!(response.headers.get('content-type') || '').includes('application/json')) {
    throw new ApiError(SERVER_HELP, response.status);
  }
  const data = await response.json();
  if (!response.ok) throw new ApiError(data.error || 'Something went wrong.', response.status);
  return data;
}
