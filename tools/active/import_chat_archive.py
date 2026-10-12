"""手动导入认证群 JSONL，失败时整批回滚，不发送群消息。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.chat_archive import import_jsonl
from runtime.ctx import PRIMARY_CID


def main():
    parser = argparse.ArgumentParser(description="只导入妹子认证群的历史消息；无自动清理")
    parser.add_argument("file", type=Path)
    parser.add_argument("--group", required=True, choices=[PRIMARY_CID], help="明确确认无群 ID 导出文件的归属")
    parser.add_argument("--db", type=Path, help="默认 data/chat_archive.db")
    arguments = parser.parse_args()
    try:
        result = import_jsonl(arguments.file, group_id=arguments.group, path=arguments.db)
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print("导入失败：" + reason, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
