// Only publicFiles enter an agent workspace. Oracles stay with the evaluator.
const task = (id, specification, stub, smoke, hidden, reference) => ({
  id,
  specification,
  publicFiles: {
    "TASK.md":
      specification +
      "\n\nChange only solution.mjs. Use Node.js built-ins, no dependencies. Run node --test public.test.mjs. Do not edit tests, commit, delegate, access other directories, or look for evaluation code.\n",
    "solution.mjs": stub + "\n",
    "public.test.mjs":
      "import assert from 'node:assert/strict';\nimport * as api from './solution.mjs';\n" +
      smoke +
      "\n",
  },
  hidden,
  reference: reference + "\n",
});

export const tasks = [
  task(
    "slug",
    "Export slug(text): normalize NFKD, remove combining marks, lowercase, replace each run of non-ASCII letters/digits with one hyphen, trim edge hyphens. Empty or punctuation-only input returns ''. Input is a string.",
    "export const slug = text => text.toLowerCase().replaceAll(' ', '-');",
    "assert.equal(api.slug(' Hello World! '), 'hello-world');",
    "for (const [input, expected] of [['Crème BRÛLÉE','creme-brulee'],['a___b///c','a-b-c'],['---',''],['',''],['déjà  vu','deja-vu'],['Ｆｏｏ １２','foo-12'],['a\\u0301b','ab'],['你好 hi','hi'],['v2.0','v2-0']]) assert.equal(api.slug(input), expected);",
    "export const slug = text => text.normalize('NFKD').replace(/\\p{M}/gu,'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'');",
  ),

  task(
    "csv",
    "Export parseCsv(text) returning arrays of string fields. Support comma delimiters, LF or CRLF rows, quoted fields containing commas/newlines, and doubled quotes. Preserve spaces and empty fields. Empty input returns []; one terminal row separator does not add a row. Quotes can only begin a field; after a closing quote only comma, row separator or EOF is valid. Unterminated or misplaced quotes throw SyntaxError. Bare CR is invalid outside quotes.",
    "export const parseCsv = text => text.split('\\n').map(row => row.split(','));",
    "assert.deepEqual(api.parseCsv('a,\"b,c\"\\r\\n1,2\\r\\n'), [['a','b,c'],['1','2']]);",
    "for (const [input, expected] of [['',[]],['a,',[['a','']]],['\"\"',[['']]],['\\n',[['']]],['\\n\\n',[[''],['']]],['a,\"b\"\"c\",d',[['a','b\"c','d']]],['\"x\\ny\", z',[['x\\ny',' z']]],['a,b\\n\\n',[['a','b'],['']]]]) assert.deepEqual(api.parseCsv(input),expected); for (const bad of ['\"a','a\"b','\"a\"x','a\\rb','\"a\" ']) assert.throws(()=>api.parseCsv(bad),SyntaxError);",
    "export function parseCsv(text) { if (!text) return []; const rows=[]; let row=[], field='', quoted=false, closed=false; const push=()=>{row.push(field);field='';closed=false;}; for(let i=0;i<text.length;i++){const c=text[i];if(quoted){if(c==='\"'){if(text[i+1]==='\"'){field+='\"';i++;}else{quoted=false;closed=true;}}else field+=c;continue;} if(c==='\"'){if(field||closed)throw new SyntaxError();quoted=true;}else if(c===','){push();}else if(c==='\\n'||c==='\\r'){if(c==='\\r'){if(text[++i]!=='\\n')throw new SyntaxError();}push();rows.push(row);row=[];if(i===text.length-1)return rows;}else{if(closed)throw new SyntaxError();field+=c;}} if(quoted)throw new SyntaxError();push();rows.push(row);return rows;}",
  ),

  task(
    "intervals",
    "Export mergeIntervals(intervals). Accept an array of [start,end] finite-number pairs with start <= end, including zero-length intervals. Return new sorted pairs merging overlaps and touching boundaries. Do not mutate any input. Invalid shape, NaN, Infinity or reversed pairs throw TypeError.",
    "export const mergeIntervals = intervals => intervals.sort((a,b)=>a[0]-b[0]);",
    "assert.deepEqual(api.mergeIntervals([[4,6],[1,4]]), [[1,6]]);",
    "assert.deepEqual(api.mergeIntervals([]),[]); const x=[[4,8],[-2,1],[0,5],[10,10],[12,13]]; const copy=structuredClone(x); assert.deepEqual(api.mergeIntervals(x),[[-2,8],[10,10],[12,13]]);assert.deepEqual(x,copy);assert.deepEqual(api.mergeIntervals([[1,1],[1,1]]),[[1,1]]);for(const bad of [null,[[3,2]],[[0,NaN]],[[0,Infinity]],[[1]],[[1,2,3]],[['1',2]]])assert.throws(()=>api.mergeIntervals(bad),TypeError);",
    "export function mergeIntervals(xs){if(!Array.isArray(xs)||xs.some(x=>!Array.isArray(x)||x.length!==2||!x.every(Number.isFinite)||x[0]>x[1]))throw new TypeError();const out=[];for(const [a,b] of xs.map(x=>[...x]).sort((a,b)=>a[0]-b[0])){const last=out.at(-1);if(last&&a<=last[1])last[1]=Math.max(last[1],b);else out.push([a,b]);}return out;}",
  ),

  task(
    "query",
    "Export parseQuery(text) returning a null-prototype object whose own keys map to arrays of values in appearance order. Strip at most one leading '?', split by '&', ignore empty segments, split each segment at the first '='; missing '=' means empty value. Decode percent escapes and convert '+' to space in both key and value. Preserve empty keys, duplicates and names such as '__proto__'. Invalid percent encoding throws URIError.",
    "export const parseQuery = text => Object.fromEntries(new URLSearchParams(text));",
    "assert.deepEqual(api.parseQuery('?a=1&a=2').a,['1','2']);",
    "const q=api.parseQuery('?a+b=x%2By&&=v&flag&x=a=b&__proto__=safe&constructor=c');assert.equal(Object.getPrototypeOf(q),null);assert.deepEqual({...q},{'a b':['x+y'],'':['v'],flag:[''],x:['a=b'],['__proto__']:['safe'],constructor:['c']});assert.deepEqual(Object.keys(api.parseQuery('&&')),[]);assert.deepEqual(api.parseQuery('%E4%BD%A0=%E5%A5%BD')['你'],['好']);for(const s of ['a=%','%GG=x','a=%C0%AF'])assert.throws(()=>api.parseQuery(s),URIError);",
    "export function parseQuery(text){const out=Object.create(null);const decode=s=>decodeURIComponent(s.replace(/\\+/g,' '));for(const part of text.replace(/^\\?/,'').split('&')){if(!part)continue;const i=part.indexOf('=');const k=decode(i<0?part:part.slice(0,i)),v=decode(i<0?'':part.slice(i+1));(out[k]??=[]).push(v);}return out;}",
  ),

  task(
    "toposort",
    "Export topoSort(nodes, edges), with distinct string nodes and directed [from,to] edges. Return a topological ordering. At each step choose the earliest node in the original nodes array among ALL currently zero-indegree nodes. Duplicate edges count once. Unknown endpoints, duplicate nodes or cycles throw Error. Do not mutate inputs.",
    "export const topoSort = (nodes, edges) => [...nodes];",
    "assert.deepEqual(api.topoSort(['b','a'],[['a','b']]),['a','b']);",
    "assert.deepEqual(api.topoSort(['a','b','c','d'],[['c','a']]),['b','c','a','d']);const n=['x','y','z'],e=[['x','y'],['x','y']];assert.deepEqual(api.topoSort(n,e),['x','y','z']);assert.deepEqual(n,['x','y','z']);assert.equal(e.length,2);assert.deepEqual(api.topoSort([],[]),[]);for(const [n,e] of [[['a'],[['a','a']]],[['a','b'],[['a','b'],['b','a']]],[['a'],[['a','x']]],[['a','a'],[]]])assert.throws(()=>api.topoSort(n,e));",
    "export function topoSort(nodes,edges){const counts=new Map(nodes.map(n=>[n,0])),out=new Map(nodes.map(n=>[n,new Set()]));if(counts.size!==nodes.length)throw Error('duplicate');for(const [a,b] of edges){if(!out.has(a)||!out.has(b))throw Error('unknown');if(!out.get(a).has(b)){out.get(a).add(b);counts.set(b,counts.get(b)+1);}}const result=[];while(result.length<nodes.length){const n=nodes.find(n=>counts.get(n)===0);if(n===undefined)throw Error('cycle');counts.set(n,-1);result.push(n);for(const b of out.get(n))counts.set(b,counts.get(b)-1);}return result;}",
  ),

  task(
    "ttl-lru",
    "Export class TtlLru with constructor(capacity) where capacity is a positive integer. set(key,value,ttl,now) stores with expiry now+ttl; get(key,now) returns value or undefined. ttl and now are finite numbers, ttl >= 0, invalid values throw TypeError. Expiry is inclusive (now >= expires). Each set purges ALL expired entries before evicting the least recently used to enforce capacity. A successful get and every set refresh recency. ttl=0 deletes that key immediately. Updating an existing key does not evict unrelated entries. Keys may be any Map key. No real-time clock; use only supplied now.",
    "export class TtlLru { constructor(){this.data=new Map();}set(k,v){this.data.set(k,v);}get(k){return this.data.get(k);} }",
    "const c=new api.TtlLru(1);c.set('a',1,5,0);assert.equal(c.get('a',5),undefined);",
    "for(const n of [0,-1,1.5,NaN])assert.throws(()=>new api.TtlLru(n));const c=new api.TtlLru(2);c.set('a',1,100,0);c.set('b',2,100,0);assert.equal(c.get('a',1),1);c.set('c',3,100,2);assert.equal(c.get('b',2),undefined);c.set('a',4,100,3);assert.equal(c.get('c',3),3);c.set('a',9,0,4);assert.equal(c.get('a',4),undefined);const d=new api.TtlLru(2);d.set('live',1,100,0);d.set('dead',2,1,0);d.set('new',3,100,2);assert.equal(d.get('live',2),1);for(const ttl of [-1,NaN,Infinity])assert.throws(()=>d.set('x',0,ttl,0),TypeError);assert.throws(()=>d.get('live',NaN),TypeError);",
    "export class TtlLru {constructor(capacity){if(!Number.isInteger(capacity)||capacity<1)throw new TypeError();this.capacity=capacity;this.data=new Map();}set(k,v,ttl,now){if(!Number.isFinite(ttl)||ttl<0||!Number.isFinite(now))throw new TypeError();for(const [key,e] of this.data)if(now>=e.expires)this.data.delete(key);this.data.delete(k);if(ttl>0)this.data.set(k,{value:v,expires:now+ttl});while(this.data.size>this.capacity)this.data.delete(this.data.keys().next().value);}get(k,now){if(!Number.isFinite(now))throw new TypeError();const e=this.data.get(k);if(!e)return undefined;if(now>=e.expires){this.data.delete(k);return undefined;}this.data.delete(k);this.data.set(k,e);return e.value;}}",
  ),

  task(
    "map-limit",
    "Export async mapLimit(items, limit, fn). Call fn(item,index) at most limit concurrently, preserve result order and resolve [] for empty input. limit must be a positive integer, even for empty input; invalid limit rejects TypeError. Both synchronous throws and promise rejections reject with the original error. After observing a failure do not start more work; already running calls may settle. Do not mutate items. fn may return a plain value.",
    "export const mapLimit = async (items,limit,fn) => Promise.all(items.map(fn));",
    "let active=0,peak=0;await api.mapLimit([1,2,3],1,async x=>{active++;peak=Math.max(peak,active);await Promise.resolve();active--;return x;});assert.equal(peak,1);",
    "const gates=[],started=[],signals=Array.from({length:4},()=>Promise.withResolvers());const p=api.mapLimit([10,20,30,40],2,(x,i)=>{started.push(i);signals[i].resolve();return new Promise(resolve=>{gates[i]=()=>resolve(x+i);});});await signals[1].promise;assert.deepEqual(started,[0,1]);gates[1]();await signals[2].promise;assert.deepEqual(started,[0,1,2]);gates[2]();await signals[3].promise;gates[3]();gates[0]();assert.deepEqual(await p,[10,21,32,43]);assert.deepEqual(await api.mapLimit([],2,()=>0),[]);for(const n of [0,-1,2.2,Infinity])await assert.rejects(api.mapLimit([],n,()=>0),TypeError);const boom=new Error('boom');const seen=[];await assert.rejects(api.mapLimit([1,2,3],1,x=>{seen.push(x);throw boom;}),e=>e===boom);assert.deepEqual(seen,[1]);",
    "export async function mapLimit(items,limit,fn){if(!Number.isInteger(limit)||limit<1)throw new TypeError();const results=new Array(items.length);let next=0,failed=false;async function worker(){while(!failed&&next<items.length){const i=next++;try{results[i]=await fn(items[i],i);}catch(e){failed=true;throw e;}}}await Promise.all(Array.from({length:Math.min(limit,items.length)},worker));return results;}",
  ),

  task(
    "retry",
    "Export async retry(fn, options). options has attempts (positive integer), baseDelay and maxDelay (finite nonnegative numbers), shouldRetry(error,attemptNumber), and async sleep(milliseconds). Call fn with 1-based attempt numbers. On error, retry only if attempts remain and shouldRetry returns true. Before retry n after failed attempt n, await sleep(min(maxDelay, baseDelay * 2**(n-1))). Never sleep on success, final failure or non-retryable failure. Propagate original final error, predicate errors and sleep errors. Validate numeric options before calling fn. fn can throw synchronously.",
    "export const retry = async fn => fn(1);",
    "let n=0;assert.equal(await api.retry(()=>{if(++n<2)throw Error('retry');return 7;},{attempts:3,baseDelay:10,maxDelay:20,shouldRetry:()=>true,sleep:async()=>{}}),7);",
    "const delays=[],calls=[];const options={attempts:4,baseDelay:3,maxDelay:5,shouldRetry:()=>true,sleep:async x=>{delays.push(x);}};const err=Error('last');await assert.rejects(api.retry(n=>{calls.push(n);throw err;},options),e=>e===err);assert.deepEqual(calls,[1,2,3,4]);assert.deepEqual(delays,[3,5,5]);let count=0;await assert.rejects(api.retry(()=>{count++;throw err;},{...options,shouldRetry:()=>false}),e=>e===err);assert.equal(count,1);for(const override of [{attempts:0},{attempts:1.5},{baseDelay:-1},{maxDelay:Infinity}])await assert.rejects(api.retry(()=>0,{...options,...override}),Error);const sleepError=Error('sleep');await assert.rejects(api.retry(()=>{throw err;},{...options,sleep:async()=>{throw sleepError;}}),e=>e===sleepError);",
    "export async function retry(fn,o){if(!Number.isInteger(o.attempts)||o.attempts<1||![o.baseDelay,o.maxDelay].every(x=>Number.isFinite(x)&&x>=0))throw new TypeError();for(let n=1;;n++){try{return await fn(n);}catch(e){if(n>=o.attempts||!o.shouldRetry(e,n))throw e;await o.sleep(Math.min(o.maxDelay,o.baseDelay*2**(n-1)));}}}",
  ),

  task(
    "deep-merge",
    "Export deepMerge(left,right) for JSON-compatible values. Recursively merge only when BOTH values are non-null non-array objects. Otherwise use a deep copy of right; arrays replace rather than concatenate. Preserve own keys including '__proto__' and 'constructor' as data, without modifying prototypes. Result and all nested values must share no mutable references with either input. Output ordinary objects. Inputs are acyclic JSON values.",
    "export const deepMerge = (a,b) => ({...a,...b});",
    "assert.deepEqual(api.deepMerge({x:{a:1}},{x:{b:2}}),{x:{a:1,b:2}});",
    'const a={x:[{a:1}],y:{a:1},z:3},b={y:{b:2},z:null};const r=api.deepMerge(a,b);assert.deepEqual(r,{x:[{a:1}],y:{a:1,b:2},z:null});r.x[0].a=99;r.y.b=7;assert.equal(a.x[0].a,1);assert.equal(b.y.b,2);assert.deepEqual(api.deepMerge([1],[2]),[2]);assert.equal(api.deepMerge({},null),null);const special=JSON.parse(\'{"__proto__":{"polluted":true},"constructor":{"x":1}}\');const out=api.deepMerge({},special);assert.equal(Object.getPrototypeOf(out),Object.prototype);assert.equal(Object.hasOwn(out,\'__proto__\'),true);assert.equal(out.__proto__.polluted,true);assert.equal({}.polluted,undefined);',
    "const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);const copy=v=>Array.isArray(v)?v.map(copy):object(v)?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,copy(x)])):v;export function deepMerge(a,b){if(!object(a)||!object(b))return copy(b);const result=Object.fromEntries(Object.entries(a).map(([k,v])=>[k,copy(v)]));for(const [k,v] of Object.entries(b))Object.defineProperty(result,k,{value:Object.hasOwn(a,k)?deepMerge(a[k],v):copy(v),enumerable:true,writable:true,configurable:true});return result;}",
  ),

  task(
    "money",
    "Export sumMoney(values): sum decimal money strings exactly, returning canonical decimal with exactly two fractional digits. Accept optional leading '-', one or more decimal digits, optional '.' followed by 1 or 2 digits. Leading zeroes are allowed; plus signs, whitespace, exponent notation, missing digits and >2 fractional digits are invalid and throw TypeError. Support arbitrarily large magnitudes without floating-point rounding. Empty input returns '0.00'; negative zero becomes '0.00'.",
    "export const sumMoney = values => values.reduce((a,b)=>a+Number(b),0).toFixed(2);",
    "assert.throws(()=>api.sumMoney(['1.001']),TypeError);",
    "for(const [v,e] of [[[], '0.00'],[['0.1','0.2'],'0.30'],[['-1.25','0.2'],'-1.05'],[['999999999999999999999.99','0.01'],'1000000000000000000000.00'],[['-0','-0.00'],'0.00'],[['0001.2','-01.20'],'0.00']])assert.equal(api.sumMoney(v),e);for(const s of ['','1.',' .5','+1','1e2',' 1','1 ','--1','1.234','NaN'])assert.throws(()=>api.sumMoney([s]),TypeError);assert.throws(()=>api.sumMoney([1]),TypeError);",
    "export function sumMoney(values){let total=0n;for(const value of values){if(typeof value!=='string'||! /^-?\\d+(?:\\.\\d{1,2})?$/.test(value))throw new TypeError();const negative=value.startsWith('-');const [whole,fraction='']=value.replace(/^-/,'').split('.');total+=(negative?-1n:1n)*(BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0')));}const sign=total<0n?'-':'';if(total<0n)total=-total;return sign+(total/100n)+'.'+String(total%100n).padStart(2,'0');}",
  ),

  task(
    "byte-ranges",
    "Export parseRanges(header,size) returning inclusive [start,end] byte ranges in request order (do not merge). size is a nonnegative safe integer. Header syntax is 'bytes=' followed by one or more comma-separated ranges with optional whitespace around each range: digits-digits, digits-, or -digits. No internal whitespace or signs. Invalid syntax, unsafe integers or reversed explicit ranges throw TypeError. Clamp end to size-1, skip ranges starting beyond EOF, zero-length suffixes, and all ranges for size=0 (still validate syntax). Suffix -N selects last min(N,size) bytes.",
    "export const parseRanges = (header,size) => [[0,size-1]];",
    "assert.deepEqual(api.parseRanges('bytes=2-4,-2',10),[[2,4],[8,9]]);",
    "assert.deepEqual(api.parseRanges('bytes=0-100, 5-, -100, -0, 99-',10),[[0,9],[5,9],[0,9]]);assert.deepEqual(api.parseRanges('bytes=0-0',0),[]);for(const h of ['items=0-1','bytes=','bytes=-','bytes=3-2','bytes=1-2,','bytes=1 -2','bytes=9007199254740992-'])assert.throws(()=>api.parseRanges(h,10),TypeError);for(const n of [-1,1.5,Infinity])assert.throws(()=>api.parseRanges('bytes=0-',n),TypeError);assert.throws(()=>api.parseRanges('bytes=bad',0),TypeError);",
    "export function parseRanges(header,size){if(!Number.isSafeInteger(size)||size<0||!header.startsWith('bytes='))throw new TypeError();const out=[];for(const part of header.slice(6).split(',')){const m=/^(\\d*)-(\\d*)$/.exec(part.trim());if(!m||(!m[1]&&!m[2]))throw new TypeError();const a=m[1]?Number(m[1]):null,b=m[2]?Number(m[2]):null;if([a,b].some(n=>n!==null&&!Number.isSafeInteger(n))||(a!==null&&b!==null&&a>b))throw new TypeError();if(!size)continue;if(a===null){if(b>0)out.push([Math.max(0,size-b),size-1]);}else if(a<size)out.push([a,Math.min(b??size-1,size-1)]);}return out;}",
  ),

  task(
    "emitter",
    "Export class Emitter: on(event,fn) and once(event,fn) return an unsubscribe function specific to that registration; duplicate registrations are independent. emit(event,...args) synchronously calls a snapshot of listeners in registration order, returning number invoked. Removing/adding listeners during emit only affects subsequent emits, not the current snapshot. A once listener must be removed BEFORE its callback (including reentrant emits); a once listener already consumed by a nested emit must not be invoked twice by an outer snapshot. Exceptions propagate and abort the current emit; unused once listeners remain registered. Events may be strings or symbols.",
    "export class Emitter {constructor(){this.listeners=new Map();}on(e,f){this.listeners.set(e,f);return ()=>this.listeners.delete(e);}once(e,f){return this.on(e,f);}emit(e,...args){this.listeners.get(e)?.(...args);return 1;}}",
    "const e=new api.Emitter();let n=0;e.once('x',()=>n++);e.emit('x');e.emit('x');assert.equal(n,1);",
    "const e=new api.Emitter(),seen=[];const f=()=>seen.push('f');const off=e.on('x',f);e.on('x',f);off();assert.equal(e.emit('x'),1);assert.deepEqual(seen,['f']);assert.equal(e.emit('missing'),0);const q=new api.Emitter(),calls=[];let offB;q.on('e',()=>{calls.push('a');offB();q.on('e',()=>calls.push('c'));});offB=q.on('e',()=>calls.push('b'));q.emit('e');assert.deepEqual(calls,['a','b']);const r=new api.Emitter();let once=0;r.once('z',()=>{once++;r.emit('z');});r.emit('z');assert.equal(once,1);const s=new api.Emitter();let nested=false,n=0;s.on('x',()=>{if(!nested){nested=true;s.emit('x');}});s.once('x',()=>n++);s.emit('x');assert.equal(n,1);const t=new api.Emitter(),err=Error('listener');const cancel=t.on('x',()=>{throw err;});let pending=0;t.once('x',()=>pending++);assert.throws(()=>t.emit('x'),e=>e===err);cancel();t.emit('x');assert.equal(pending,1);",
    "export class Emitter {constructor(){this.events=new Map();}add(e,fn,once){const entry={fn,once,used:false};const list=this.events.get(e)??[];list.push(entry);this.events.set(e,list);return()=>{const i=list.indexOf(entry);if(i>=0)list.splice(i,1);};}on(e,f){return this.add(e,f,false);}once(e,f){return this.add(e,f,true);}emit(e,...args){const list=this.events.get(e)??[];let n=0;for(const entry of [...list]){if(entry.once){if(entry.used)continue;entry.used=true;const i=list.indexOf(entry);if(i>=0)list.splice(i,1);}n++;entry.fn(...args);}return n;}}",
  ),
];

export const coldTasks = [
  task(
    "cold-add",
    "Fix add(a,b) in solution.mjs to return a+b for finite numbers.",
    "export const add=(a,b)=>a-b;",
    "assert.equal(api.add(2,3),5);",
    "assert.equal(api.add(-4,7),3);assert.equal(api.add(0,0),0);",
    "export const add=(a,b)=>a+b;",
  ),
  task(
    "cold-dedupe",
    "Fix unique(values) to return a new array of unique values in first appearance order, using SameValueZero equality. Do not mutate input.",
    "export const unique=values=>values;",
    "assert.deepEqual(api.unique([1,1,2]),[1,2]);",
    "const x=[NaN,NaN,'a','a',0,-0];assert.deepEqual(api.unique(x),[NaN,'a',0]);assert.equal(x.length,6);",
    "export const unique=values=>[...new Set(values)];",
  ),
];
