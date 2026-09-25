import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot
from bot import _get_perf_content_full, _perf_m_name, _novel_save_path, _extract_novel_title, _load_member_names
from brains import generate_perf_novel_chunked

cid = "bd7fe6a4-0065-4551-ad3d-94116dba8f9c"
start_id = "2a02438f-2752-4630-a51f-ae50e9e61f41"
bot._ACTIVE_CID = cid
_load_member_names()

try:
    content = _get_perf_content_full(cid, start_id)
    n_lines = len([l for l in content.split("\n") if l.strip()])
    print("公演内容", n_lines, "条", flush=True)

    m_name = _perf_m_name(content)
    if m_name:
        content = "【被调教方（M）】：" + m_name + "\n\n" + content
    print("M方:", m_name, flush=True)

    print("开始生成小说（可能 2-5 分钟）...", flush=True)
    novel = generate_perf_novel_chunked(content)
    print("生成完成，字数:", len(novel), flush=True)

    path = _novel_save_path(cid, novel)
    with open(path, "w", encoding="utf-8") as f:
        f.write(novel)
    print("标题:", _extract_novel_title(novel), flush=True)
    print("保存到:", path, flush=True)
    print("DONE", flush=True)
except Exception:
    traceback.print_exc()
