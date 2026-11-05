"""只测试 xAI 生图/编辑，不连接群聊；密钥来自进程环境或隐藏输入。"""
import argparse
import asyncio
import getpass
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.gallery import inspect_image
from runtime.imagegen import ImagegenError, XaiClient


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", type=Path, help="输入原图；不传则文生图")
    parser.add_argument("--out", required=True, type=Path, help="新文件路径，不覆盖已有文件")
    args = parser.parse_args()
    if args.out.exists() or any(args.out.with_suffix(ext).exists() for ext in (".jpg", ".png", ".webp", ".gif")):
        parser.error("输出文件已存在，请更换文件名")
    source = None
    if args.image:
        data = args.image.read_bytes()
        _, _, mime, _ = inspect_image(data)
        source = data, mime
    client = XaiClient()
    if not client.key:
        client = XaiClient(getpass.getpass("xAI key (hidden): "))
    started = time.monotonic()
    data = await client.generate(args.prompt, source)
    width, height, mime, extension = inspect_image(data)
    # Preserve the actual encoded format without another paid request.
    args.out = args.out.with_suffix(extension)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as stream:
        stream.write(data)
    print("SUCCESS model={} mode={} size={}x{} seconds={:.1f} file={}".format(
        "grok-imagine-image-2.0", "edit" if source else "generate", width, height,
        time.monotonic() - started, args.out.resolve()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ImagegenError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
