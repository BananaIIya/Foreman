"""Recover only public assistant output; never use reasoning as an answer."""
import json
from pathlib import Path

def recover_public_text(native,event_path):
 result=native.get('result')
 if isinstance(result,str) and result.strip():return result,'native_final_result'
 chunks=[]
 for line in Path(event_path).read_text().splitlines():
  try:event=json.loads(line)
  except ValueError:continue
  if event.get('type')!='assistant':continue
  message=event.get('message') or {}
  if message.get('role')!='assistant':continue
  for content in message.get('content',[]):
   if content.get('type')=='text' and isinstance(content.get('text'),str):chunks.append(content['text'])
 return ''.join(chunks),'public_assistant_stream_deltas'
