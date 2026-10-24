"""阶段 0 真机验证：连上手机里的 Intiface，列出设备，发一次短脉冲再停止，并测延迟。

用法（手机装好 Intiface Central、连上玩具、手机与电脑/服务器同网或同 tailnet）：

    python tools/active/haptics_probe.py ws://<手机地址>:12345
    python tools/active/haptics_probe.py ws://<手机地址>:12345 --level 0.25 --hold-ms 800

它会：连接 → 打印 ServerInfo → 扫描并列出设备与可用维度 → 发一次振动 → 保持 N 毫秒 →
停止，并打印「下发往返」「停止往返」的耗时。全程只发一条低强度短脉冲，不写入任何数据。
"""
import argparse
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from runtime.haptics.protocol import ButtplugClient, HapticsUnavailable


async def wait_for_device(client, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if client.devices:
            return client.device()
        await asyncio.sleep(0.3)
    return None


async def timed_message(client, message_type, **payload):
    """发一条消息并等它被 Ok/Error 回应，返回往返毫秒。"""
    message_id = client._next_id()
    future = asyncio.get_running_loop().create_future()
    client._ok_waiters[message_id] = future
    started = time.perf_counter()
    await client._send(message_id, message_type, **payload)
    await asyncio.wait_for(future, 5)
    return (time.perf_counter() - started) * 1000


async def main(args):
    client = ButtplugClient("nuonuo-probe")
    print("连接 " + args.url + " …")
    started = time.perf_counter()
    info = await client.connect(args.url)
    print("  握手完成 %.1f ms | 服务端 %s | 协议 v%s.%s | MaxPingTime %sms" % (
        (time.perf_counter() - started) * 1000, info.get("ServerName"),
        info.get("ProtocolVersionMajor"), info.get("ProtocolVersionMinor"), info.get("MaxPingTime")))

    device = await wait_for_device(client)
    if device is None:
        print("没扫到设备。检查：Intiface 里是否已连接玩具、玩具是否保持默认蓝牙名 LVS-*、是否已被系统蓝牙占用。")
        await client.close()
        return 2
    print("设备: %s（索引 %d）" % (device.display, device.index))
    for feature in device.features:
        for kind, (low, high) in feature.outputs.items():
            print("  维度 %s  特征 %d  区间 %d..%d" % (kind, feature.index, low, high))

    feature = device.preferred_feature("Vibrate")
    if feature is None:
        print("这台设备没有 Vibrate 维度，可用维度: " + ",".join(device.kinds()))
        await client.close()
        return 3
    low, high = feature.range_of("Vibrate")
    value = int(round(low + (high - low) * max(0.0, min(1.0, args.level))))
    print("下发 %d%%（设备值 %d），保持 %d ms …" % (args.level * 100, value, args.hold_ms))
    rtt = await timed_message(client, "OutputCmd", DeviceIndex=device.index,
                              FeatureIndex=feature.index, Command={"Vibrate": {"Value": value}})
    print("  下发往返 %.1f ms" % rtt)
    await asyncio.sleep(args.hold_ms / 1000.0)
    stop_rtt = await timed_message(client, "StopCmd", Outputs=True, Inputs=False)
    print("  停止往返 %.1f ms" % stop_rtt)
    await client.close()
    print("完成：设备应已在 %d ms 后停止。若没有停，立刻在 Intiface / 玩具上手动关闭。" % stop_rtt)
    return 0


def run():
    parser = argparse.ArgumentParser(description="Intiface/Buttplug 真机验证探针")
    parser.add_argument("url", help="例如 ws://192.168.1.23:12345")
    parser.add_argument("--level", type=float, default=0.25, help="0..1，默认 0.25")
    parser.add_argument("--hold-ms", type=int, default=800, help="脉冲持续毫秒，默认 800")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(main(args)))
    except HapticsUnavailable as exc:
        print("连接失败：" + str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    run()
