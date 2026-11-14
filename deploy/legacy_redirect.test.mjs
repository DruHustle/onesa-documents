import assert from 'node:assert/strict';
import test from 'node:test';
import {redirectDocument} from './legacy_redirect.mjs';
test('old bookmarks redirect only to the canonical internal record',async()=>{
  const old='https://docs.engineering.onesa.eu';
  const result=redirectDocument(new Request(old+'/docs/architecture/design?revision=2&code=discard&view=plan'));
  assert.equal(result.status,308);
  assert.equal(result.headers.get('location'),'https://docs.internal.onesa.eu/docs/architecture/design?view=plan&revision=2');
  assert.equal(await result.text(),'');
  assert.equal(redirectDocument(new Request(old+'/callback?code=discard')).status,410);
  assert.equal(redirectDocument(new Request(old+'/docs/x',{method:'POST'})).status,405);
  assert.equal(redirectDocument(new Request('https://docs.onesa.eu/')).status,404);
});
