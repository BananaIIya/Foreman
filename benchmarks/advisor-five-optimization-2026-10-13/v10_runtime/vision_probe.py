"""Native worker vision-input fixture. Synthetic pixels, no benchmark content."""
import asyncio,base64,json,os,struct,zlib
from pathlib import Path
from .sdk_worker import worker_turn

def fixture_png():
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    row=b'\x00'+bytes([255,0,0])*64+bytes([0,0,255])*64
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',128,64,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(row*64))+chunk(b'IEND',b'')

async def main():
    root=Path(__file__).resolve().parent.parent/'raw/preflight/cursor-worker-native-image'
    root.mkdir(parents=True,exist_ok=False)
    os.environ['CURSOR_API_KEY']=json.loads(Path(os.environ["CURSOR_AUTH_JSON"]).read_text())['apiKey']
    png=fixture_png();(root/'fixture.png').write_bytes(png)
    result=await worker_turn(root/'phases/worker-0','Inspect the attached synthetic two-panel image. Return only JSON with left_color and right_color using basic color names. No tools or other actions.',
        'low',[{'data':base64.b64encode(png).decode(),'mimeType':'image/png'}])
    answer=result.get('patch',{})
    passed=result['status']=='completed' and answer.get('left_color','').lower()=='red' and answer.get('right_color','').lower()=='blue' and result['usage']['cost_usd'] is not None
    report={'passed':passed,'benchmark_result':False,'fixture':'synthetic pixels only','model':'grok-4.7','effort':'low','cost_usd':result['usage']['cost_usd'],'answer':answer}
    (root/'probe.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));assert passed

if __name__=='__main__':asyncio.run(main())
