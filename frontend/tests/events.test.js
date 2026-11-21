import assert from 'node:assert/strict';
import test from 'node:test';
import { SSEDecoder, packetRoute, recentEvents } from '../src/api.js';

test('SSE parses fragmented frames, CRLF, JSON and heartbeats', () => {
  const parser = new SSEDecoder();
  assert.deepEqual(parser.feed('id: a:1\r'), []);
  assert.deepEqual(parser.feed('\nevent: operation\r\ndata: {"status":"suc'), []);
  const frames = parser.feed('cess"}\r\n\r\n: keepalive\n\n');
  assert.deepEqual(frames, [{event:'operation',id:'a:1',data:{status:'success'}}]);
});
test('SSE handles several events in one chunk', () => {
  const frames = new SSEDecoder().feed('event: snapshot\ndata: {"recent":[]}\n\nid: x\nevent: operation\ndata: {"status":"error"}\n\n');
  assert.equal(frames.length, 2);
  assert.equal(frames[1].id, 'x');
});
test('history is deduplicated and bounded at twenty', () => {
  const events = Array.from({length:25},(_,i)=>({id:String(i)}));
  const result = recentEvents(events.slice(0,15), events.slice(10));
  assert.equal(result.length,20);
  assert.equal(result[0].id,'5');
});
test('failures and repeated ETL versions never animate a successful delivery', () => {
  const event = {bot_id:'bot-4',operation:'INSERT',cluster_id:'mongo_2',status:'sent'};
  assert.match(packetRoute(event), /^M 89 /);
  assert.match(packetRoute({...event,status:'success'}), /^M 315 /);
  assert.equal(packetRoute({...event,status:'error'}), null);
  assert.equal(packetRoute({...event,operation:'ETL',status:'success',outcome:'repeated'}),null);
  assert.match(packetRoute({...event,operation:'ETL',status:'success',outcome:'applied'}),/^M 775 /);
  assert.equal(packetRoute({...event,operation:'ETL',lifecycle:true}),null);
});

test('CRUD and ETL paths follow each real source, independently of the bot ID', () => {
  for (let i = 1; i <= 3; i++) {
    const event = {bot_id:'bot-9',cluster_id:`mongo_${i}`,operation:'INSERT',status:'success'};
    const y = 75 + (i - 1) * 80;
    assert.equal(packetRoute(event), `M 315 155 C 370 155,365 ${y},425 ${y}`);
    assert.equal(packetRoute({...event,operation:'ETL',status:'sent'}), `M 555 ${y} C 610 ${y},610 155,665 155`);
    assert.equal(packetRoute({...event,operation:'DELETE',status:'deleted'}), packetRoute(event));
  }
});
