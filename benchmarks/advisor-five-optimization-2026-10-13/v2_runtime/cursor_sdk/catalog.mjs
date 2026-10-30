import fs from 'node:fs';
import { Cursor } from '@cursor/sdk';
const apiKey=JSON.parse(fs.readFileSync(process.env.CURSOR_AUTH_JSON,'utf8')).apiKey;
const models=await Cursor.models.list({apiKey});
const selected=models.filter(m=>JSON.stringify(m).toLowerCase().includes('grok-4.7'));
if(!selected.length)throw new Error('Grok 4.7 unavailable; do not substitute');
fs.writeFileSync(new URL('../../raw/preflight/cursor-sdk-models.json',import.meta.url),JSON.stringify(selected,null,2)+'\n');
console.log(JSON.stringify(selected));
