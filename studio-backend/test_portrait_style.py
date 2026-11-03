"""Check portrait style transport and mode boundaries without submitting pool jobs."""
import asyncio
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import server


async def main():
    refs=dict(outfit='/costume/a.png',pose='/poses/a.png',face='/costume/face.png')
    item=server.Item(mode='portrait',prompt='按所选画风重新绘制人物',style='FA-036',**refs)
    prompt,images=server.compile_item(item)
    assert len(images)==4 and images[:3]==[server.ORIGIN+x for x in refs.values()]
    assert '/styles/' in images[3] and '第 4 张图片仅作画风参考。' in prompt
    assert server.REDRAW_INSTRUCTION_ZH in prompt and server.REFERENCE_ISOLATION_ZH in prompt
    strong=server.Item(mode='portrait',prompt='人物',style='FA-001',**refs)
    assert len(server.compile_item(strong)[1])==3
    assert len(server.compile_item(server.Item(mode='portrait',prompt='人物',**refs))[1])==3
    for bad in [dict(mode='general',style='FA-036'),dict(mode='portrait',layout='SB-001'),dict(mode='portrait',color='C-01')]:
        try:
            server.compile_item(server.Item(prompt='人物',**bad))
            raise AssertionError('Mode boundary was bypassed')
        except ValueError:
            pass
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder)
        with patch.object(server,'DATA',root),patch.object(server,'UPLOADS',root/'uploads'),patch.object(server,'DB',root/'db.sqlite'):
            server.initialize()
            item.compiled_prompt='我修改的人物提示词'
            result=await server.create_batch(server.Batch(items=[item]))
            job=server.rows('SELECT * FROM jobs WHERE batch_id=?',(result['batch_id'],))[0]
            assert len(json.loads(job['refs']))==4
            assert json.loads(job['config'])['style']=='FA-036'
            assert job['prompt'].startswith('我修改的人物提示词')
            assert server.REFERENCE_ISOLATION_ZH in job['prompt'] and server.REDRAW_INSTRUCTION_ZH in job['prompt']
    print('Portrait optional style, reference order, edited prompt and mode boundaries passed.')


asyncio.run(main())
