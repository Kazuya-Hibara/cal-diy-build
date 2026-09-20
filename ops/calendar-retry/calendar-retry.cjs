'use strict';

// Preload with NODE_OPTIONS=--require=/path/to/calendar-retry.cjs.
// Temporary mitigation for Calendar createEvent's follow-up PATCH rate limit.
// Never retry event insertion: its POST may already have created an event.
const Module = require('node:module');
const PATCHED = Symbol.for('cal-diy.calendar-retry.request.v1');
const INSTALLED = Symbol.for('cal-diy.calendar-retry.loader.v1');
const RETRY_PATCHED = Symbol.for('cal-diy.calendar-retry.native.v1');
const MAX_RETRIES = 5;
const RATE_REASONS = new Set(['rateLimitExceeded', 'userRateLimitExceeded']);

function requestMethod(options) {
  if (!options || typeof options !== 'object') return null;
  const method = String(options.method || 'GET').toUpperCase();
  if (!['PATCH', 'PUT', 'DELETE'].includes(method)) return null;
  let url;
  try {
    url = new URL(options.url, options.baseURL);
  } catch {
    return null;
  }
  if (url.protocol !== 'https:' || url.hostname !== 'www.googleapis.com' ||
      (url.port && url.port !== '443') || url.username || url.password ||
      !url.pathname.startsWith('/calendar/v3/')) return null;
  return method;
}

function shouldRetry(error) {
  const response = error && error.response;
  if (!response || ![403, 429].includes(response.status)) return false;
  const errors = response.data && response.data.error && response.data.error.errors;
  // Do not retry a mixed response containing permission/authentication errors.
  return Array.isArray(errors) && errors.length > 0 &&
    errors.every(item => item && RATE_REASONS.has(item.reason));
}

function patchGaxios(exports, dependencies = {}) {
  patchNativeRetry(exports);
  const prototype = exports && exports.Gaxios && exports.Gaxios.prototype;
  if (!prototype || typeof prototype.request !== 'function' || prototype.request[PATCHED]) return false;
  const original = prototype.request;
  const sleep = dependencies.sleep || (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const random = dependencies.random || Math.random;
  const log = dependencies.log || (line => console.warn(line));

  async function request(options, ...rest) {
    const effective = { ...this.defaults, ...options };
    const method = requestMethod(effective);
    if (!method) return original.call(this, options, ...rest);
    // Native retry policy remains intact for 5xx, transport and custom failures.
    // patchNativeRetry excludes only the rate-limit errors handled by this loop.
    const retryOptions = { ...effective };
    for (let attempt = 0; ; attempt++) {
      try {
        return await original.call(this, retryOptions, ...rest);
      } catch (error) {
        if (attempt >= MAX_RETRIES || !shouldRetry(error) || retryOptions.signal?.aborted) throw error;
        // 1+2+4+8+16 seconds plus <=750ms jitter each = <=34.75 seconds.
        const sample = random();
        const jitter = Number.isFinite(sample) ? Math.floor(Math.max(0, Math.min(1, sample)) * 750) : 0;
        const delay = 1000 * (2 ** attempt) + jitter;
        log(`[calendar-retry] method=${method} retry=${attempt + 1} status=${error.response.status}`);
        await sleep(delay);
        if (retryOptions.signal?.aborted) throw error;
      }
    }
  }
  Object.defineProperty(request, PATCHED, { value: true });
  prototype.request = request;
  return true;
}

function patchNativeRetry(exports) {
  const original = exports && exports.getRetryConfig;
  if (typeof original !== 'function' || original[RETRY_PATCHED]) return false;
  function getRetryConfig(error, ...rest) {
    if (requestMethod(error && error.config) && shouldRetry(error)) {
      return Promise.resolve({ shouldRetry: false, config: error.config });
    }
    return original.call(this, error, ...rest);
  }
  Object.defineProperty(getRetryConfig, RETRY_PATCHED, { value: true });
  exports.getRetryConfig = getRetryConfig;
  return true;
}

function isGaxiosPath(request) {
  return typeof request === 'string' && /(?:^|[/\\])gaxios(?:$|[/\\])/.test(request);
}

function install(loader = Module, dependencies = {}) {
  if (loader._load[INSTALLED]) return false;
  const originalLoad = loader._load;
  function load(request, ...args) {
    const exports = originalLoad.call(this, request, ...args);
    const parentFilename = args[0] && args[0].filename;
    if (isGaxiosPath(request) || isGaxiosPath(parentFilename)) patchGaxios(exports, dependencies);
    return exports;
  }
  Object.defineProperty(load, INSTALLED, { value: true });
  loader._load = load;
  // Also cover a module loaded by an earlier preload in the same process.
  for (const [filename, cached] of Object.entries(loader._cache || {})) {
    if (isGaxiosPath(filename)) patchGaxios(cached.exports, dependencies);
  }
  return true;
}

module.exports = { requestMethod, shouldRetry, patchGaxios, patchNativeRetry, install };
install();
