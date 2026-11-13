"""Real MP4 stitching, paid boundaries, duration parsing and persistence."""
import asyncio
from fractions import Fraction
from io import BytesIO

import av
from PIL import Image
import pytest

from runtime import videogen as V, features
from runtime.video_sequence import SequenceError, SequenceStore, clip_info, compose, segment_durations


def movie(seconds=1, color="red", audio=False):
    output = BytesIO()
    with av.open(output, "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=12)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.codec_context.thread_count = 1
        sound = container.add_stream("aac", rate=48000) if audio else None
        if sound: sound.layout = "stereo"
        for i in range(seconds * 12):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 48), color))
            frame.pts = i; frame.time_base = Fraction(1, 12)
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
        if sound:
            for i in range(0, seconds * 48000, 1024):
                frame = av.AudioFrame(format="fltp", layout="stereo", samples=min(1024, seconds * 48000 - i))
                for plane in frame.planes: plane.update(bytes(plane.buffer_size))
                frame.pts = i; frame.sample_rate = 48000; frame.time_base = Fraction(1, 48000)
                for packet in sound.encode(frame): container.mux(packet)
            for packet in sound.encode(): container.mux(packet)
    return output.getvalue()


@pytest.mark.parametrize("payload,expected", [("猫走路", (6,"猫走路")), ("15秒 猫走路",(15,"猫走路")),
    ("2分钟 猫走路",(120,"猫走路")),("1.5分钟 猫走路",(90,"猫走路")),("120s 猫",(120,"猫")),
    ("120只猫跑步",(6,"120只猫跑步"))])
def test_duration_is_an_explicit_leading_argument(payload, expected):
    assert V.duration_prompt(payload) == expected


@pytest.mark.parametrize("payload", ["121秒 猫", "3分钟 猫", "0秒 猫", "0.5秒 猫", "-1秒 猫"])
def test_out_of_range_duration_rejected(payload):
    with pytest.raises(V.VideogenError): V.duration_prompt(payload)


def test_segments_sum_to_requested_duration_and_checkpoint_blocks_rebilling(tmp_path):
    for duration in range(1,121):
        parts = segment_durations(duration)
        assert sum(parts)==duration and max(parts)<=15 and min(parts)>=1 and len(parts)<=8
    store = SequenceStore(tmp_path/"media.db")
    store.begin("g","m",120,"cat");store.claim("g","m",0,15)
    store.submitted("g","m",0,"remote-123");store.save("g","m",0,b"paid-result")
    restarted = SequenceStore(tmp_path/"media.db")
    assert restarted.get("g","m",0)["request_id"] == "remote-123"
    with pytest.raises(SequenceError): restarted.claim("g","m",0,15)
    with pytest.raises(SequenceError): restarted.begin("g","m",90,"cat")


def test_real_mp4_composition_keeps_order_duration_audio_and_last_frame(tmp_path):
    a,b=movie(2,"red",audio=True),movie(2,"blue")
    data=compose([(a,2),(b,2)],tmp_path/"composed.mp4",4)
    assert abs(clip_info(data)["duration"]-4)<.1
    frame,mime=clip_info(data,expected=4,last_frame=True)
    assert mime=="image/png" and Image.open(BytesIO(frame)).getpixel((32,24))[2]>200
    with av.open(BytesIO(data)) as container:
        assert container.streams.audio and container.streams.audio[0].codec_context.name=="aac"
        first=next(container.decode(video=0)).to_image().getpixel((32,24))
        assert first[0]>200 and first[2]<30


def test_full_two_minutes_is_real_timeline_and_remains_below_upload_limit(tmp_path):
    clip=movie(15)
    data=compose([(clip,15)]*8,tmp_path/"two-minutes.mp4",120)
    assert abs(clip_info(data)["duration"]-120)<.1
    assert len(data)<50*1024*1024


def test_mismatched_clip_duration_stops_composition(tmp_path):
    with pytest.raises(SequenceError,match="时长"):
        compose([(movie(1),15)],tmp_path/"bad.mp4",15)


def test_invalid_duration_is_rejected_before_reservation_or_paid_call(tmp_path, monkeypatch):
    from test_videogen import build, message, submit
    async def scenario():
        monkeypatch.setattr(features,"_OVERRIDES",{V.PRIMARY_CID+"|videogen":True})
        monkeypatch.setenv("DZMM_FEATURES","on")
        service, platform, client, sender = build(tmp_path, movie(1))
        msg = message()
        msg["content"]["text"] = "/生视频 121秒 猫走路"
        await submit(service,platform,sender,msg)
        assert not client.calls and not service.tasks
        assert service.store.get(V.PRIMARY_CID,"cmd1") is None
        assert any("最长2分钟" in text for text in sender.notices)
    asyncio.run(scenario())


def test_sequence_uses_previous_last_frame_persists_and_does_not_recharge(monkeypatch,tmp_path):
    from runtime.imagegen_store import Store
    async def scenario():
        monkeypatch.setattr(features,"_OVERRIDES",{V.PRIMARY_CID+"|videogen":True})
        monkeypatch.setenv("DZMM_FEATURES","on")
        calls=[];data=movie(8)
        class Client:
            async def generate(self,prompt,source,**kwargs):
                calls.append((prompt,source,kwargs['duration']))
                kwargs['on_submitted']('remote-'+str(len(calls)))
                return data
        class Platform:
            async def member(self,uid): return "测试成员"
        class Sender:
            async def send(self,*args): return True
        store=Store(tmp_path/"media.db");service=V.Service(store,Platform(),Client())
        result=await service.generate_sequence(Sender(),V.PRIMARY_CID,"m","u","猫回头",None,16)
        assert len(calls)==2 and calls[0][1] is None and calls[1][1][1]=="image/png"
        assert "自然接续" in calls[1][0] and all(x[2]==8 for x in calls)
        assert abs(clip_info(result)["duration"]-16)<.1
        assert service.sequences.get(V.PRIMARY_CID,"m",1)['status']=='ready'
        await service.generate_sequence(Sender(),V.PRIMARY_CID,"m","u","猫回头",None,16)
        assert len(calls)==2
    asyncio.run(scenario())


def test_ambiguous_second_segment_never_resubmits_or_publishes(monkeypatch,tmp_path):
    from runtime.imagegen_store import Store
    async def scenario():
        monkeypatch.setattr(features,"_OVERRIDES",{V.PRIMARY_CID+"|videogen":True})
        monkeypatch.setenv("DZMM_FEATURES","on")
        calls=[]
        class Client:
            async def generate(self,prompt,source,**kwargs):
                calls.append(1);kwargs['on_submitted']('id-'+str(len(calls)))
                if len(calls)==2:raise V.VideogenError("结果不确定")
                return movie(8)
        class Platform:
            async def member(self,uid):return "成员"
        class Sender:
            async def send(self,*args):return True
        service=V.Service(Store(tmp_path/"media.db"),Platform(),Client())
        with pytest.raises(V.VideogenError):
            await service.generate_sequence(Sender(),V.PRIMARY_CID,"m","u","cat",None,16)
        restarted=V.Service(Store(tmp_path/"media.db"),Platform(),Client())
        with pytest.raises(V.VideogenError,match="待核对"):
            await restarted.generate_sequence(Sender(),V.PRIMARY_CID,"m","u","cat",None,16)
        assert len(calls)==2 and restarted.sequences.get(V.PRIMARY_CID,"m",0)['data']
    asyncio.run(scenario())
