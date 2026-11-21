import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, packetRoute, recentEvents, stream } from './api.js';
import './style.css';

const COLORS = { INSERT: '#3ba881', READ: '#6493e3', UPDATE: '#d6a045', DELETE: '#de807c', ETL: '#9a80cb', BOT: '#7a8ba7' };
const STATE = { running: 'Activo', stopping: 'Deteniendo', stopped: 'Apagado', completed: 'Completado', error: 'Error' };
const STATUS = { sent: 'Enviado', success: 'Confirmado', deleted: 'Borrado', error: 'Error' };
const empty = { bots: {}, active_count: 0, assignments: {}, cluster_loads: {}, etl: { status: 'unknown' }, recent: [] };
const fmt = value => value == null ? '—' : Number(value).toLocaleString('es-MX');
const time = value => value ? new Date(value).toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';

function useLab(token) {
  const [runtime, setRuntime] = useState(empty), [events, setEvents] = useState([]), [packets, setPackets] = useState([]);
  const [metrics, setMetrics] = useState(null), [metricsError, setMetricsError] = useState('');
  const [connection, setConnection] = useState('offline'), [notice, setNotice] = useState('');
  const [dirty, setDirty] = useState(false);
  const [ready, setReady] = useState(false), [pending, setPending] = useState('');
  const refreshRef = useRef(() => {}), generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    const controller = new AbortController(), { signal } = controller;
    let cursor = null, metricTimer, botTimer, fetchingMetrics = false, mutations = 0;
    setReady(false); setDirty(false); setRuntime(empty); setEvents([]); setPackets([]); setMetrics(null); setMetricsError(''); setNotice(''); setPending('');
    if (!token) { setConnection('offline'); return () => controller.abort(); }
    setConnection('connecting');
    async function refreshBots() {
      try { const value = await api('/bots', token, { signal }); if (!signal.aborted) setRuntime(value); }
      catch (error) { if (!signal.aborted) setNotice(error.message); }
    }
    refreshRef.current = refreshBots;
    async function poll() {
      if (fetchingMetrics) return;
      fetchingMetrics = true; clearTimeout(metricTimer); const startedAtMutation = mutations;
      try { const value = await api('/metrics', token, { signal }); if (!signal.aborted) { setMetrics(value); if (mutations === startedAtMutation && value.mongo.pending_documents != null) setDirty(value.mongo.pending_documents > 0); setMetricsError(value.errors.join(' · ')); } }
      catch (error) { if (!signal.aborted) setMetricsError(`Última consulta fallida: ${error.message}`); }
      finally { fetchingMetrics = false; if (!signal.aborted) metricTimer = setTimeout(poll, 10000); }
    }
    function onFrame(frame) {
      if (signal.aborted) return;
      if (frame.id) cursor = frame.id;
      setConnection('live');
      if (frame.event === 'snapshot') {
        setRuntime(frame.data); setEvents(frame.data.recent); setPackets([]);
      } else if (frame.event === 'operation') {
        const event = frame.data;
        if (['INSERT','UPDATE','DELETE'].includes(event.operation) && ['success','deleted'].includes(event.status)) { mutations += 1; setDirty(true); }
        setEvents(previous => recentEvents(previous, [event]));
        if (packetRoute(event) || event.status === 'error') setPackets(previous => [...previous.filter(p => p.id !== event.id), event].slice(-18));
        if (event.operation === 'BOT') {
          clearTimeout(botTimer); botTimer = setTimeout(refreshBots, 100);
        }
        if (event.operation === 'ETL' && event.lifecycle) {
          if (event.status !== 'sent') poll();
          setRuntime(previous => ({ ...previous, etl: { ...previous.etl, status: event.status === 'sent' ? 'running' : event.status === 'success' ? 'succeeded' : 'error', message: event.message } }));
        }
      }
    }
    async function connect() {
      try {
        const initial = await api('/bots', token, { signal });
        if (signal.aborted) return;
        setRuntime(initial); setReady(true); poll();
      } catch (error) { if (!signal.aborted) { setNotice(error.message); setConnection('error'); } return; }
      while (!signal.aborted) {
        try { await stream(token, cursor, signal, onFrame); }
        catch (error) {
          if (signal.aborted) break;
          if ([401,403].includes(error.status)) { setReady(false); setConnection('error'); setNotice('Acceso rechazado. Conecta de nuevo con el token del operador.'); break; }
          setConnection('reconnecting');
          await new Promise(resolve => { const timer = setTimeout(done, 2000); function done() { clearTimeout(timer); signal.removeEventListener('abort', done); resolve(); } signal.addEventListener('abort', done, { once: true }); });
        }
      }
    }
    connect();
    return () => { controller.abort(); clearTimeout(metricTimer); clearTimeout(botTimer); };
  }, [token]);
  async function command(key, path, method, body) {
    if (!ready || pending) return;
    const version = generation.current;
    setPending(key); setNotice('');
    try {
      const result = await api(path, token, { method, body: body === undefined ? undefined : JSON.stringify(body) });
      if (version !== generation.current) return;
      if (result.bots) setRuntime(result);
      if (key === 'sync') setRuntime(previous => ({ ...previous, etl: { ...previous.etl, status: 'running' } }));
      setNotice(key === 'sync' ? 'Sincronización solicitada. Sigue sus eventos en el recorrido.' : 'Control recibido por la API.');
      refreshRef.current();
    } catch (error) { if (version === generation.current) setNotice(`No se pudo completar: ${error.message}`); }
    finally { if (version === generation.current) setPending(''); }
  }
  const removePacket = useCallback(id => setPackets(previous => previous.filter(p => p.id !== id)), []);
  return { runtime, events, packets, removePacket, metrics, metricsError, connection, notice, ready, dirty, pending, command };
}

function Packet({ event, remove }) {
  const motion = useRef(null);
  useEffect(() => {
    if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) motion.current?.beginElement();
    const timer = setTimeout(() => remove(event.id), 1450);
    return () => clearTimeout(timer);
  }, [event.id, remove]);
  const color = COLORS[event.operation] || COLORS.READ;
  return <g className="packet"><circle r="8" fill={color} opacity=".15" /><circle r="4" fill={color} /><animateMotion ref={motion} path={packetRoute(event)} dur="1.3s" begin="indefinite" fill="freeze" /></g>;
}

function FailureMarker({ event, remove }) {
  useEffect(() => { const timer = setTimeout(() => remove(event.id), 2500); return () => clearTimeout(timer); }, [event.id, remove]);
  const cluster = /^mongo_[123]$/.test(event.cluster_id) ? Number(event.cluster_id.at(-1)) : 1;
  const x = event.stage === 'etl' ? 720 : event.stage === 'mongo' ? 490 : 260;
  const y = event.stage === 'mongo' ? 75 + (cluster - 1) * 80 : 155;
  return <g transform={`translate(${x+45} ${y-27})`} role="img" aria-label={event.message}><circle r="11" fill="#dc5261" /><path d="M-3 -3L3 3M3 -3L-3 3" stroke="white" strokeWidth="2" /></g>;
}

function Database({ x, y, name, sub, selected, color }) {
  return <g transform={`translate(${x} ${y})`} className={selected ? 'destination' : ''}>
    <rect x="-65" y="-26" width="130" height="52" rx="10" fill="white" stroke={selected ? color : '#e2e8f0'} strokeWidth={selected ? '2' : '1'} />
    <g transform="translate(-47 -8)" fill="none" stroke={color} strokeWidth="1.5"><ellipse rx="7" ry="3" /><path d="M-7 0v13c0 4 14 4 14 0V0 M-7 7c0 4 14 4 14 0" /></g>
    <text x="-30" y="-2" className="svg-name">{name}</text><text x="-30" y="14" className="svg-sub">{sub}</text>
  </g>;
}

function Flow({ runtime, packets, removePacket, events, connected }) {
  const latestError = [...events].reverse().find(e => e.status === 'error');
  return <section className="panel flow"><div className="section-head"><div><span className="overline">EL RECORRIDO</span><h2>De una operación a una entrega.</h2></div><span className="live-pill"><i className={connected ? 'green-dot' : ''} />{connected ? 'Eventos reales · SSE' : 'Sin stream en vivo'}</span></div>
    <div className="legend">{Object.entries(COLORS).filter(([name]) => name !== 'BOT').map(([name,color]) => <span key={name}><i style={{ background: color }} />{name === 'ETL' ? 'Carga ETL' : name}</span>)}<span><i style={{background:'#dc5261'}} />Error</span></div>
    <div className="graph-scroll"><svg className="graph" viewBox="0 0 1050 330" role="img" aria-label="Flujo de nueve bots a API REST, tres clústeres MongoDB, ETL y Neon. Los paquetes se muestran únicamente al recibir eventos reales.">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0L10 5L0 10" fill="#cbd5e1" /></marker></defs>
      {[1,2,3,4,5,6,7,8,9].map(i => <path key={i} className="route" d={`M89 ${38+(i-1)*29}C145 ${38+(i-1)*29},140 155,205 155`} />)}
      {[1,2,3].map(i => <g key={i}><path className="route" d={`M315 155C370 155,365 ${75+(i-1)*80},425 ${75+(i-1)*80}`} /><path className="route" d={`M555 ${75+(i-1)*80}C610 ${75+(i-1)*80},610 155,665 155`} /></g>)}
      <path className="route" d="M775 155C825 155,840 155,885 155" />
      {[[60,'01 · BOTS'],[260,'02 · API REST'],[490,'03 · MONGODB ATLAS'],[720,'04 · ETL'],[940,'05 · NEON']].map(([x,label]) => <text key={label} x={x} y="14" textAnchor="middle" className="svg-heading">{label}</text>)}
      {Array.from({length:9},(_,index) => { const id=`bot-${index+1}`, active=['running','stopping'].includes(runtime.bots[id]?.state); return <g key={id} transform={`translate(30 ${27+index*29})`}><rect width="59" height="22" rx="6" fill={active?'#edf4ff':'#f8fafc'} stroke={active?'#aac4ec':'#e2e8f0'} /><circle cx="10" cy="11" r="2.5" fill={active?'#6493e3':'#cbd5e1'} /><text x="20" y="15" className="svg-sub">Bot {index+1}</text></g>; })}
      <g transform="translate(205 120)"><rect width="110" height="70" rx="12" fill="#f3f7ff" stroke="#dbe6f7" /><text x="55" y="30" textAnchor="middle" className="svg-name">API REST</text><text x="55" y="48" textAnchor="middle" className="svg-sub">Valida y enruta</text></g>
      {[1,2,3].map(i => { const load = runtime.cluster_loads?.[`mongo_${i}`] || 0; return <Database key={i} x={490} y={75+(i-1)*80} name={`Clúster ${i}`} sub={load ? `${load} / 3 bots` : 'Sin bots asignados'} selected={load > 0} color="#61ab94" />; })}
      <g transform="translate(665 120)"><rect width="110" height="70" rx="12" fill="#f8f5fc" stroke="#e7dff3" /><text x="55" y="30" textAnchor="middle" className="svg-name">ETL</text><text x="55" y="48" textAnchor="middle" className="svg-sub">Cada 3 minutos</text></g>
      <Database x={950} y={155} name="PostgreSQL" sub="Neon · destino" color="#9a80cb" />
      {packets.map(event => event.status === 'error' ? <FailureMarker key={event.id} event={event} remove={removePacket} /> : <Packet key={event.id} event={event} remove={removePacket} />)}
      <text x="490" y="296" textAnchor="middle" className="svg-sub">{runtime.active_count ? `Bots por clúster: ${[1,2,3].map(i => runtime.cluster_loads?.[`mongo_${i}`] || 0).join(' / ')} · Máximo 3 por clúster` : 'Enciende un bot para activar el flujo'}</text>
    </svg></div>
    <div className="flow-foot"><p>Los clústeres resaltados reciben nuevas creaciones de sus bots asignados. Los documentos existentes conservan su origen. El ETL revisa los tres clústeres; solo una carga nueva confirmada continúa hacia Neon.</p><span>Representación visual de eventos<br />Sin tráfico inventado</span></div>
    {latestError && <div className="error-note"><b>Último fallo · {time(latestError.timestamp)}</b><span>{latestError.message}</span></div>}
  </section>;
}

function Metrics({ data, error, activeCount }) {
  const mongo = data?.mongo, pg = data?.postgres;
  const groups = [ ['MongoDB Atlas','Origen', [ ['Documentos totales',mongo?.total_documents],['Sincronizados',mongo?.synchronized_documents],['Clústeres en línea',mongo?.active_clusters],['Bots activos',activeCount] ]],
    ['PostgreSQL Neon','Destino', [ ['Registros totales',pg?.total_records],['Tablas con datos',pg?.synchronized_tables],['Errores de carga',pg?.load_errors],['Última sincronización',pg?.last_sync, true] ]] ];
  return <section className="metric-groups">{groups.map(([title,tag,items]) => <div className="panel metric-panel" key={title}><div className="metric-heading"><h2>{title}</h2><span>{tag}</span></div><div className="metric-grid">{items.map(([label,value,isTime]) => <div key={label}><span>{label}</span><strong className={isTime?'metric-time':''}>{isTime ? time(value) : fmt(value)}</strong></div>)}</div></div>)}
    <div className={`metric-caption ${error?'warning':''}`}><span>{error || (data ? `Observado a las ${time(data.observed_at)} · Incluye documentos con borrado lógico` : 'Conecta para consultar métricas reales')}</span><span>{data?.mongo?.pending_documents != null ? `${fmt(data.mongo.pending_documents)} documentos pendientes` : ''}</span></div>
  </section>;
}

function App() {
  const [draft, setDraft] = useState(''), [token, setToken] = useState('');
  const [count, setCount] = useState(3), [rounds, setRounds] = useState('1'), [interval, setIntervalValue] = useState(2);
  const lab = useLab(token);
  const disabled = !lab.ready || !!lab.pending;
  const options = { interval_seconds: interval, rounds: rounds === '' ? null : Number(rounds) };
  const setBots = n => { setCount(n); return lab.command('count', '/bots', 'PUT', { ...options, count: n }); };
  const connectionText = {offline:'Sin conectar',connecting:'Conectando',live:'En vivo',reconnecting:'Reconectando SSE',error:'Sin acceso'}[lab.connection];
  const etl = lab.runtime.etl.status;
  const syncText = lab.dirty && etl !== 'running' && etl !== 'error' ? 'Pendiente de sincronizar' : {unknown:'Sin observación',running:'En ejecución',succeeded:'Sincronizado',error:'Error'}[etl] || 'Sin observación';
  return <main>
    <header><a className="brand" href="/"><span className="brand-mark">c</span>cloud<span>db</span><small>LABORATORIO</small></a><div className={`connection ${lab.connection}`}><i />{connectionText}</div></header>
    <div className="intro"><div><span className="overline">SISTEMAS DISTRIBUIDOS / TIEMPO REAL</span><h1>Cada operación, a la vista<span>.</span></h1><p>Controla tus bots y sigue el recorrido de los datos hasta Neon.</p></div><div className={`sync-badge ${etl}`}><i />{syncText}<small>Sincronización automática · 180 s</small></div></div>
    <form className="connect" onSubmit={event => { event.preventDefault(); setToken(draft.trim()); setDraft(''); }}><label htmlFor="token">Acceso del operador</label><input id="token" type="password" value={draft} onChange={e=>setDraft(e.target.value)} placeholder="Token de acceso" autoComplete="off" required /><button className="button secondary" type="submit" disabled={!draft.trim()}>{token?'Reconectar':'Conectar'}</button><small>Solo en memoria de esta pestaña.</small></form>
    <div className="notice" role="status" aria-live="polite">{lab.notice || (lab.ready ? 'Conectado. Los controles ejecutan operaciones reales sobre las bases de datos.' : 'Introduce el token para consultar el laboratorio y activar los controles.')}</div>
    <Metrics data={lab.metrics} error={lab.metricsError} activeCount={lab.ready ? lab.runtime.active_count : null} />
    <section className="panel controls"><div className="section-head"><div><span className="overline">CONTROLA LA CARGA</span><h2>{lab.runtime.active_count} de 9 bots activos</h2></div><div className="control-actions"><button className="button danger" disabled={disabled || !lab.runtime.active_count} onClick={()=>lab.command('stop','/bots/stop','POST')}>{lab.pending==='stop'?'Deteniendo…':'Detener todos'}</button><button className="button primary" disabled={disabled || etl==='running'} onClick={()=>lab.command('sync','/sincronizacion','POST')}>{lab.pending==='sync' || etl==='running'?'Sincronizando…':'↻ Sincronizar ahora'}</button></div></div>
      <div className="configuration"><div className="presets"><label>Cantidad rápida</label><div>{[1,3,6,9].map(n=><button key={n} className={lab.runtime.active_count===n?'chosen':''} disabled={disabled} onClick={()=>setBots(n)} aria-label={`Activar ${n} ${n===1?'bot':'bots'}`}>{n} {n===1?'bot':'bots'}</button>)}</div></div><form className="count-form" onSubmit={e=>{e.preventDefault();setBots(count);}}><label htmlFor="count">Cantidad exacta</label><div><input id="count" type="number" min="0" max="9" step="1" required value={count} onChange={e=>setCount(e.target.value===''?'':Number(e.target.value))} /><button className="button secondary" disabled={disabled || count===''}>Aplicar</button></div></form><label className="option">Ciclos por bot<input type="number" min="1" max="20" step="1" value={rounds} onChange={e=>setRounds(e.target.value)} placeholder="Continuo" /></label><label className="option">Pausa entre solicitudes<select value={interval} onChange={e=>setIntervalValue(Number(e.target.value))}><option value="2">2 s · Pausado</option><option value="1">1 s · Normal</option><option value="0.5">0.5 s · Rápido</option></select></label></div>
      <p className="config-note">Hasta 3 bots por clúster: 6 bots → 3 + 3; 9 bots → 3 + 3 + 3. Al cambiar los bots activos se reasignan las nuevas creaciones por orden de bot. Ciclos y pausa se aplican al iniciar; un ciclo realiza 10 operaciones. Vacía ciclos para ejecución continua.</p>
      <div className="bot-controls">{Array.from({length:9},(_,i)=>{const id=`bot-${i+1}`,state=lab.runtime.bots[id]?.state||'stopped',active=['running','stopping'].includes(state);return <button key={id} className={`bot-button ${state}`} disabled={disabled || state==='stopping'} aria-label={`${active?'Detener':'Iniciar'} bot ${i+1}`} onClick={()=>lab.command(id,`/bots/${i+1}/${active?'stop':'start'}`,'POST',active?undefined:options)}><span className="bot-top"><i />BOT {i+1}<span>{active?'■':'▶'}</span></span><strong>{STATE[state]}</strong><small>{active ? `Clúster ${lab.runtime.assignments?.[id]?.at(-1) || '…'} · Clic para detener` : 'Clic para iniciar'}</small></button>;})}</div>
    </section>
    <Flow runtime={lab.runtime} packets={lab.packets} removePacket={lab.removePacket} events={lab.events} connected={lab.connection==='live'} />
    <section className="panel activity"><div className="section-head"><div><span className="overline">ACTIVIDAD RECIENTE</span><h2>Lo que acaba de pasar</h2></div><span className="muted">Últimas {lab.events.length} / 20 operaciones · En memoria</span></div><div className="table-scroll"><table><thead><tr><th>Hora</th><th>Origen</th><th>Operación</th><th>Colección / clúster</th><th>Estado</th><th>Detalle</th></tr></thead><tbody>{[...lab.events].reverse().map(event=><tr key={event.id} className={event.status==='error'?'error-row':''}><td>{time(event.timestamp)}</td><td>{event.bot_id || (event.operation==='ETL'?'ETL':'Operador')}</td><td><span className={`operation op-${event.operation.toLowerCase()}`}>{event.operation}</span></td><td>{event.collection || '—'}<small>{event.cluster_id?.replace('mongo_','Clúster ') || '—'}</small></td><td><span className={`event-status ${event.status}`}>{STATUS[event.status]}</span></td><td>{event.message}</td></tr>)}</tbody></table>{!lab.events.length && <div className="empty"><span>↗</span><b>Todo comienza con una operación.</b><p>Conecta y enciende un bot para ver aquí sus envíos y respuestas.</p></div>}</div></section>
    <footer><span>Cloud DB · Proyecto integrador</span><span>4 colecciones · 3 clústeres · Máximo 9 bots</span></footer>
  </main>;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>);
