export async function api(path, token, options = {}) {
  const timeout = AbortSignal.timeout(25000);
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const response = await fetch(path, { ...options, signal, headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json', ...options.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail) ? body.detail.map(e => `${e.loc?.at(-1)}: ${e.msg}`).join('; ') : body.detail;
    const error = new Error(detail || `El servicio respondió HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

// Retains partial lines across arbitrary UTF-8 network chunks; comments are heartbeats.
export class SSEDecoder {
  buffer = '';
  feed(chunk) {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, '\n');
    if (this.buffer.length > 1024 * 1024) throw new Error('Evento SSE demasiado grande');
    const frames = [];
    let boundary;
    while ((boundary = this.buffer.indexOf('\n\n')) >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const frame = { event: 'message', id: null }, data = [];
      for (const line of block.split('\n')) {
        if (line.startsWith(':')) continue;
        const split = line.indexOf(':');
        const field = split < 0 ? line : line.slice(0, split);
        const value = split < 0 ? '' : line.slice(split + 1).replace(/^ /, '');
        if (field === 'data') data.push(value);
        else if (field === 'event') frame.event = value;
        else if (field === 'id') frame.id = value;
      }
      if (data.length) frames.push({ ...frame, data: JSON.parse(data.join('\n')) });
    }
    return frames;
  }
}

export async function stream(token, cursor, signal, onFrame) {
  const headers = { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' };
  if (cursor) headers['Last-Event-ID'] = cursor;
  const response = await fetch('/events', { headers, signal });
  if (!response.ok) {
    const error = new Error(`No se pudo abrir SSE (HTTP ${response.status})`);
    error.status = response.status;
    throw error;
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('El servidor no respondió con SSE');
  const reader = response.body.getReader(), decoder = new TextDecoder(), parser = new SSEDecoder();
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) throw new Error('El servidor cerró el stream');
      for (const frame of parser.feed(decoder.decode(value, { stream: true }))) onFrame(frame);
    }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export function recentEvents(previous, incoming) {
  const unique = new Map(previous.map(e => [e.id, e]));
  for (const event of incoming) unique.set(event.id, event);
  return [...unique.values()].slice(-20);
}

export function packetRoute(event) {
  const cluster = /^mongo_[123]$/.test(event.cluster_id) ? Number(event.cluster_id.at(-1)) : null;
  const bot = /^bot-[1-9]$/.test(event.bot_id) ? Number(event.bot_id.split('-')[1]) : null;
  const y = cluster ? 75 + (cluster - 1) * 80 : null;
  if (event.lifecycle || event.operation === 'BOT' || event.status === 'error') return null;
  if (event.operation === 'ETL') {
    if (event.status === 'sent' && cluster) return `M 555 ${y} C 610 ${y},610 155,665 155`;
    if (event.status === 'success' && event.outcome === 'applied') return 'M 775 155 C 825 155,840 155,885 155';
    return null;
  }
  if (event.status === 'sent' && bot) return `M 89 ${38 + (bot - 1) * 29} C 145 ${38 + (bot - 1) * 29},140 155,205 155`;
  if (['success','deleted'].includes(event.status) && cluster) return `M 315 155 C 370 155,365 ${y},425 ${y}`;
  return null;
}
