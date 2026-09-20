'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { patchGaxios, patchNativeRetry, install, requestMethod, shouldRetry } = require('./calendar-retry.cjs');
const url = 'https://www.googleapis.com/calendar/v3/calendars/test/events/one';
const error = (status = 403, reason = 'rateLimitExceeded') => Object.assign(new Error('private detail'), {
  response: { status, data: { error: { errors: [{ reason }] } } },
});

function harness(outcomes, options = {}) {
  const calls = [], waits = [], logs = [];
  class Gaxios {
    request(opts) {
      assert.equal(this.marker, 'instance');
      calls.push(opts);
      const outcome = outcomes[Math.min(calls.length - 1, outcomes.length - 1)];
      return outcome instanceof Error ? Promise.reject(outcome) : Promise.resolve(outcome);
    }
  }
  patchGaxios({ Gaxios }, { sleep: async ms => waits.push(ms), random: () => 1,
    log: line => logs.push(line), ...options });
  const instance = new Gaxios();
  instance.marker = 'instance';
  return { Gaxios, instance, calls, waits, logs };
}

test('classifier only accepts exact Google HTTPS Calendar v3 mutation routes', () => {
  for (const method of ['PATCH', 'put', 'DELETE']) assert.equal(requestMethod({ url, method }), method.toUpperCase());
  for (const bad of [
    { url, method: 'POST' }, { url, method: 'GET' }, { url },
    { url: url.replace('www.googleapis.com', 'evil.test'), method: 'PATCH' },
    { url: url.replace('www.googleapis.com', 'www.googleapis.com.evil.test'), method: 'PATCH' },
    { url: url.replace('https:', 'http:'), method: 'PATCH' },
    { url: url.replace('/calendar/v3/', '/drive/v3/'), method: 'PATCH' },
    { url: url.replace('/calendar/v3/', '/calendar/v30/'), method: 'PATCH' },
    { url: 'not a url', method: 'PATCH' },
  ]) assert.equal(requestMethod(bad), null);
});

test('retry reasons require 403/429 and only rate-limit JSON reasons', () => {
  for (const status of [403, 429]) for (const reason of ['rateLimitExceeded', 'userRateLimitExceeded']) assert.equal(shouldRetry(error(status, reason)), true);
  for (const status of [401, 404, 500]) assert.equal(shouldRetry(error(status)), false);
  for (const reason of ['authError', 'forbidden', 'quotaExceeded']) assert.equal(shouldRetry(error(403, reason)), false);
  assert.equal(shouldRetry(new Error('rateLimitExceeded')), false);
  assert.equal(shouldRetry({ response: { status: 429, data: 'not JSON' } }), false);
});

test('transient failure retries preserving this/body and logs no request or error details', async () => {
  const h = harness([error(), { data: 'ok' }]);
  const opts = { method: 'PATCH', url, data: { description: 'private' }, headers: { authorization: 'secret' }, retry: true };
  assert.deepEqual(await h.instance.request(opts), { data: 'ok' });
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0].data, opts.data);
  assert.equal(h.calls[0].retry, true);
  assert.equal(opts.retry, true);
  assert.deepEqual(h.waits, [1750]);
  assert.deepEqual(h.logs, ['[calendar-retry] method=PATCH retry=1 status=403']);
});

test('exhaustion permits five retries and no more than 35 seconds total waiting', async () => {
  const failure = error(429);
  const h = harness([failure]);
  await assert.rejects(h.instance.request({ url, method: 'DELETE' }), e => e === failure);
  assert.equal(h.calls.length, 6);
  assert.deepEqual(h.waits, [1750, 2750, 4750, 8750, 16750]);
  assert.ok(h.waits.reduce((sum, n) => sum + n, 0) <= 35000);
});

test('POST never duplicates; unrelated/auth failures never retry', async () => {
  for (const [opts, failure] of [
    [{ url, method: 'POST' }, error()],
    [{ url: 'https://other.test/calendar/v3/events', method: 'PATCH' }, error()],
    [{ url, method: 'PATCH' }, error(403, 'authError')],
  ]) {
    const h = harness([failure]);
    await assert.rejects(h.instance.request(opts), e => e === failure);
    assert.equal(h.calls.length, 1);
    assert.deepEqual(h.waits, []);
    if (opts.method === 'POST') assert.equal(h.calls[0], opts);
  }
});

test('distinct Gaxios module classes are patched once across repeated loads', async () => {
  const one = harness([error(), 'one']);
  const two = harness([error(), 'two']);
  const original = one.Gaxios.prototype.request;
  patchGaxios({ Gaxios: one.Gaxios });
  assert.equal(one.Gaxios.prototype.request, original);
  assert.equal(await one.instance.request({ url, method: 'PATCH' }), 'one');
  assert.equal(await two.instance.request({ url, method: 'PUT' }), 'two');
  assert.equal(one.calls.length, 2);
  assert.equal(two.calls.length, 2);
});

test('loader handles root/nested gaxios while preserving loader context and unrelated exports', async () => {
  const constructors = [];
  for (let i = 0; i < 2; i++) constructors.push(class Gaxios { request() { return Promise.resolve(i); } });
  const exportsList = constructors.map(Gaxios => ({ Gaxios }));
  const unrelated = { hello: 'untouched' };
  const loader = { _cache: {}, _load(request, parent, main) {
    assert.equal(this, loader);
    assert.equal(parent, 'parent');
    assert.equal(main, false);
    return request === 'gaxios' ? exportsList[0] : request.includes('node_modules/gaxios') ? exportsList[1] : unrelated;
  } };
  const prior = constructors.map(C => C.prototype.request);
  install(loader);
  const installed = loader._load;
  install(loader);
  assert.equal(loader._load, installed);
  assert.equal(loader._load('gaxios', 'parent', false), exportsList[0]);
  assert.equal(loader._load('/calcom/node_modules/google-auth-library/node_modules/gaxios', 'parent', false), exportsList[1]);
  assert.equal(loader._load('unrelated', 'parent', false), unrelated);
  for (let i = 0; i < 2; i++) assert.notEqual(constructors[i].prototype.request, prior[i]);
  const wrapped = constructors[0].prototype.request;
  loader._load('gaxios', 'parent', false);
  assert.equal(constructors[0].prototype.request, wrapped);
});

test('aborted request does not perform another attempt after backoff', async () => {
  const controller = new AbortController();
  const failure = error();
  const h = harness([failure], { sleep: async () => controller.abort() });
  await assert.rejects(h.instance.request({ url, method: 'PATCH', signal: controller.signal }), e => e === failure);
  assert.equal(h.calls.length, 1);
});

test('an earlier preload cached gaxios is patched too', () => {
  class Gaxios { request() { return Promise.resolve('ok'); } }
  const before = Gaxios.prototype.request;
  install({ _load() {}, _cache: { '/calcom/node_modules/gaxios/build/src/index.js': { exports: { Gaxios } } } });
  assert.notEqual(Gaxios.prototype.request, before);
});

test('real Node preload catches separate root and nested module copies', () => {
  const fs = require('node:fs');
  const os = require('node:os');
  const path = require('node:path');
  const { spawnSync } = require('node:child_process');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'cal-calendar-retry-'));
  try {
    for (const relative of ['node_modules/gaxios', 'node_modules/google-auth-library/node_modules/gaxios']) {
      fs.mkdirSync(path.join(directory, relative), { recursive: true });
      fs.writeFileSync(path.join(directory, relative, 'index.js'), 'exports.Gaxios = class Gaxios { request() { return Promise.resolve("ok"); } };');
    }
    fs.writeFileSync(path.join(directory, 'probe.cjs'), `
      const assert = require('node:assert/strict');
      const root = require('gaxios').Gaxios;
      const nested = require('./node_modules/google-auth-library/node_modules/gaxios').Gaxios;
      assert.notEqual(root, nested);
      for (const C of [root, nested]) assert.equal(C.prototype.request[Symbol.for('cal-diy.calendar-retry.request.v1')], true);
      console.log('both module copies patched');
    `);
    const result = spawnSync(process.execPath, [path.join(directory, 'probe.cjs')], {
      encoding: 'utf8', env: { ...process.env, NODE_OPTIONS: `--require=${require.resolve('./calendar-retry.cjs')}` },
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), 'both module copies patched');
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('native retry gate preserves non-rate errors and suppresses only scoped rate-limit errors', async () => {
  let calls = 0;
  const native = { getRetryConfig: async () => { calls++; return { shouldRetry: true }; } };
  patchNativeRetry(native);
  const patched = native.getRetryConfig;
  patchNativeRetry(native);
  assert.equal(native.getRetryConfig, patched);
  for (const method of ['PUT', 'DELETE']) {
    const failure = error(503, 'backendError');
    failure.config = { url, method };
    assert.equal((await native.getRetryConfig(failure)).shouldRetry, true);
  }
  const limited = error(429);
  limited.config = { url, method: 'DELETE' };
  assert.equal((await native.getRetryConfig(limited)).shouldRetry, false);
  assert.equal(calls, 2);
  limited.config.method = 'POST';
  assert.equal((await native.getRetryConfig(limited)).shouldRetry, true);
  assert.equal(calls, 3);
});

// Optional integration lane: point GAXIOS_TEST_MODULE at the installed package.
test('actual Gaxios preserves PUT/DELETE 503 retries without nesting rate-limit retries',
  { skip: !process.env.GAXIOS_TEST_MODULE }, async () => {
    const { Gaxios } = require(process.env.GAXIOS_TEST_MODULE);
    for (const method of ['PUT', 'DELETE']) {
      let calls = 0;
      let nativeBackoffs = 0;
      const response = await new Gaxios().request({
        url, method, retry: true,
        retryConfig: { retryBackoff: async () => {}, onRetryAttempt: () => { nativeBackoffs++; } },
        adapter: async config => ({ config, status: ++calls === 1 ? 503 : 204,
          statusText: 'test', headers: {}, data: {} }),
      });
      assert.equal(response.status, 204);
      assert.equal(calls, 2);
      assert.equal(nativeBackoffs, 1);
    }
    let calls = 0;
    let nativeBackoffs = 0;
    const response = await new Gaxios().request({
      url, method: 'DELETE', retry: true,
      retryConfig: { retryBackoff: async () => {}, onRetryAttempt: () => { nativeBackoffs++; } },
      adapter: async config => ({ config, status: ++calls === 1 ? 429 : 204,
        statusText: 'test', headers: {}, data: { error: { errors: [{ reason: 'rateLimitExceeded' }] } } }),
    });
    assert.equal(response.status, 204);
    assert.equal(calls, 2);
    assert.equal(nativeBackoffs, 0);
  });
