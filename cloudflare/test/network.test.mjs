import test from 'node:test';
import assert from 'node:assert/strict';
import {onRequestGet} from '../functions/api/monitor/network.js';

test('carrier checks expose only edge-derived country and ASN and never cache a visitor response', async()=>{
  const request=new Request('https://example.test/api/monitor/network',{headers:{'cf-ipcountry':'CN'}});
  assert.equal(onRequestGet({request}).status,503);
  Object.defineProperty(request,'cf',{value:{country:'GB',asn:123,city:'London'}});
  const res=onRequestGet({request});
  assert.equal(res.headers.get('cache-control'),'no-store');
  assert.deepEqual(await res.json(),{country:'GB',asn:123});
});
