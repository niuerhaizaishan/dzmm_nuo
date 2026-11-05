"""独立测试 xAI 文/图生视频；不连接群聊，密钥仅来自环境或隐藏输入。"""
import argparse
import asyncio
import getpass
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.gallery import GalleryError, inspect_image, inspect_media
from runtime.videogen import MODEL, VideogenError, XaiVideoClient


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out = args.out.with_suffix(".mp4")
    if args.out.exists():
        parser.error("输出文件已存在，请更换路径")
    source = None
    if args.image:
        data = args.image.read_bytes()
        _, _, mime, _ = inspect_image(data)
        if mime == "image/gif":
            parser.error("原图请使用 JPG、PNG 或 WebP 静态图片")
        source = data, mime
    client = XaiVideoClient()
    if not client.key:
        client = XaiVideoClient(getpass.getpass("xAI key (hidden): "))
    started = time.monotonic()
    data = await client.generate(args.prompt, source)
    # 先保存付费结果，解码工具缺失/异常时仍能保留原始文件供核验。
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("xb") as stream:
        stream.write(data)
    width, height, mime, _ = inspect_media(data, "result.mp4", "video/mp4")
    print(f"SUCCESS model={MODEL} size={width}x{height} mime={mime} seconds={time.monotonic()-started:.1f} file={args.out.resolve()}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (VideogenError, GalleryError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
