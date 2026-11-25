// The carrier probe needs only edge-derived country and ASN. Avoid exposing IP addresses.
export function onRequestGet({request}) {
  const cf=request.cf;
  const headers={'Content-Type':'application/json', 'Cache-Control':'no-store'};
  if (!cf?.country || !Number.isInteger(cf.asn)) {
    return new Response('{"error":"network_unknown"}',{status:503,headers});
  }
  return new Response(JSON.stringify({country:cf.country,asn:cf.asn}),{headers});
}
