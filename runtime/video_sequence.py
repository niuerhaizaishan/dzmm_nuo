"""Bounded video sequence checkpoints and continuous-frame MP4 composition."""
from contextlib import closing
from fractions import Fraction
from io import BytesIO
from pathlib import Path
import sqlite3
import time

MAX_DURATION = 120
MAX_SEGMENT_DURATION = 15
MAX_BYTES = 50 * 1024 * 1024


class SequenceError(ValueError):
    """Safe message for the video command."""


def segment_durations(duration):
    if isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= MAX_DURATION:
        raise SequenceError("视频时长需要在 1～120 秒之间。")
    # Distribute evenly: a 16-second request becomes 8+8, rather than 15+1.
    count = (duration + MAX_SEGMENT_DURATION - 1) // MAX_SEGMENT_DURATION
    base, extra = divmod(duration, count)
    return [base + (i < extra) for i in range(count)]


class SequenceStore:
    """Persist every paid request boundary and clip separately from public media."""
    def __init__(self, media_path):
        self.path = Path(media_path).with_name("videogen_sequences.db")

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS sequences (cid TEXT, mid TEXT, duration INTEGER, "
                     "prompt TEXT, created REAL, PRIMARY KEY(cid,mid))")
        conn.execute("CREATE TABLE IF NOT EXISTS segments (cid TEXT, mid TEXT, part INTEGER, "
                     "duration INTEGER, status TEXT, request_id TEXT, data BLOB, updated REAL, "
                     "PRIMARY KEY(cid,mid,part))")
        conn.commit()
        return conn

    def begin(self, cid, mid, duration, prompt):
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO sequences VALUES(?,?,?,?,?)", (cid, mid, duration, prompt, time.time()))
            row = conn.execute("SELECT duration,prompt FROM sequences WHERE cid=? AND mid=?", (cid, mid)).fetchone()
            if (row["duration"], row["prompt"]) != (duration, prompt):
                raise SequenceError("视频任务参数不一致，本次未重新生成。")

    def claim(self, cid, mid, part, duration):
        with closing(self.connect()) as conn, conn:
            changed = conn.execute("INSERT OR IGNORE INTO segments VALUES(?,?,?,?,'submitting',NULL,NULL,?)",
                                   (cid, mid, part, duration, time.time())).rowcount
            if changed != 1:
                raise SequenceError("这一段视频已提交或结果待核对，为避免重复扣费，不会重新提交。")

    def submitted(self, cid, mid, part, request_id):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE segments SET status='generating',request_id=?,updated=? WHERE cid=? AND mid=? AND part=?",
                         (request_id, time.time(), cid, mid, part))

    def save(self, cid, mid, part, data):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE segments SET status='ready',data=?,updated=? WHERE cid=? AND mid=? AND part=?",
                         (data, time.time(), cid, mid, part))

    def get(self, cid, mid, part):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM segments WHERE cid=? AND mid=? AND part=?", (cid, mid, part)).fetchone()
            return dict(row) if row else None


def clip_info(data, *, expected=None, last_frame=False):
    """Validate duration and decode the final frame for the next paid request."""
    import av
    try:
        if not data or len(data) > MAX_BYTES:
            raise SequenceError("视频片段为空或超过 50 MB，本次未继续生成。")
        with av.open(BytesIO(data), options={"protocol_whitelist": "pipe", "enable_drefs": "0"}) as container:
            video = container.streams.video[0]
            if video.codec_context.name != "h264" or not 0 < video.width * video.height <= 4096 * 2160:
                raise SequenceError("视频片段编码或尺寸不支持，本次未继续生成。")
            video.codec_context.thread_count = 1
            duration = float(video.duration * video.time_base) if video.duration else float(container.duration or 0) / av.time_base
            if duration <= 0 or (expected is not None and abs(duration - expected) > 0.75):
                raise SequenceError("视频片段实际时长与请求不符，片段已保留，本次未继续生成。")
            if not last_frame:
                return {"duration": duration, "width": video.width, "height": video.height}
            final = None
            for count, frame in enumerate(container.decode(video)):
                if count > 2400:
                    raise SequenceError("视频片段帧数异常，本次未继续生成。")
                final = frame
            if final is None:
                raise SequenceError("视频片段没有画面，本次未继续生成。")
            image = final.to_image()
            image.thumbnail((1536, 1536))
            output = BytesIO(); image.save(output, format="PNG")
            return output.getvalue(), "image/png"
    except SequenceError:
        raise
    except (av.FFmpegError, OSError, ValueError, IndexError, TypeError):
        raise SequenceError("视频片段损坏或无法解码，片段已保留，本次未继续生成。") from None


def compose(parts, output_path, duration):
    """Stream at most eight clips to a size-bounded H.264/AAC MP4 on disk.

    Re-encode to one timeline and fixed dimensions. Keep each clip's audio; pad
    absent/short audio with silence. No subprocesses or remote media are opened.
    """
    import av
    if not parts or len(parts) > 8 or sum(seconds for _, seconds in parts) != duration:
        raise SequenceError("视频片段不完整，暂时无法合成。")
    infos = [clip_info(data, expected=seconds) for data, seconds in parts]
    width, height = infos[0]["width"], infos[0]["height"]
    scale = min(1, 854 / max(width, height), 480 / min(width, height))
    width = max(2, int(width * scale) // 2 * 2)
    height = max(2, int(height * scale) // 2 * 2)
    fps, sample_rate = 24, 48000
    output_path = Path(output_path)
    try:
        with av.open(str(output_path), "w", format="mp4", options={"movflags": "+faststart"}) as output:
            video_out = output.add_stream("libx264", rate=fps)
            video_out.width, video_out.height, video_out.pix_fmt = width, height, "yuv420p"
            video_out.bit_rate = 1_200_000
            video_out.codec_context.thread_count = 1
            video_out.options = {"preset": "veryfast", "maxrate": "1600k", "bufsize": "3200k"}
            audio_out = output.add_stream("aac", rate=sample_rate)
            audio_out.layout = "stereo"; audio_out.bit_rate = 96000
            video_index = audio_index = 0

            def mux(packet):
                output.mux(packet)
                if output_path.stat().st_size > MAX_BYTES:
                    raise SequenceError("合成视频超过 50 MB，片段已保留，本次未发送。")

            for (data, seconds), info in zip(parts, infos):
                video_end = video_index + seconds * fps
                audio_end = audio_index + seconds * sample_rate
                # Decode one stream at a time to bound memory. The muxer handles
                # audio/video interleaving; timestamps remain globally increasing.
                with av.open(BytesIO(data)) as source:
                    stream = source.streams.video[0]; stream.codec_context.thread_count = 1
                    first_time, pending = None, None
                    start_index = video_index
                    for frame in source.decode(stream):
                        stamp = float(frame.pts * frame.time_base) if frame.pts is not None else 0
                        if first_time is None: first_time = stamp
                        target = start_index + min(seconds * fps, max(0, round((stamp - first_time) * fps)))
                        while pending is not None and video_index < min(target, video_end):
                            emitted = pending.reformat(width, height, format="yuv420p")
                            emitted.pts = video_index; emitted.time_base = Fraction(1, fps)
                            for packet in video_out.encode(emitted): mux(packet)
                            video_index += 1
                        pending = frame
                    if pending is None: raise SequenceError("片段没有可合成的画面。")
                    while video_index < video_end:
                        emitted = pending.reformat(width, height, format="yuv420p")
                        emitted.pts = video_index; emitted.time_base = Fraction(1, fps)
                        for packet in video_out.encode(emitted): mux(packet)
                        video_index += 1
                with av.open(BytesIO(data)) as source:
                    audio_stream = next(iter(source.streams.audio), None)
                    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=sample_rate)
                    def encode_audio(frame):
                        nonlocal audio_index
                        if audio_index >= audio_end: return
                        available = min(frame.samples, audio_end - audio_index)
                        emitted = av.AudioFrame(format="fltp", layout="stereo", samples=available)
                        for destination, plane in zip(emitted.planes, frame.planes):
                            raw = bytes(plane)[:available * 4]
                            destination.update(raw + bytes(destination.buffer_size - len(raw)))
                        emitted.sample_rate = sample_rate; emitted.pts = audio_index
                        emitted.time_base = Fraction(1, sample_rate)
                        for packet in audio_out.encode(emitted): mux(packet)
                        audio_index += available
                    if audio_stream is not None:
                        audio_stream.codec_context.thread_count = 1
                        for frame in source.decode(audio_stream):
                            frame.pts = None
                            for resampled in resampler.resample(frame): encode_audio(resampled)
                        for resampled in resampler.resample(None): encode_audio(resampled)
                    while audio_index < audio_end:
                        samples = min(1024, audio_end - audio_index)
                        silence = av.AudioFrame(format="fltp", layout="stereo", samples=samples)
                        for plane in silence.planes: plane.update(bytes(plane.buffer_size))
                        encode_audio(silence)
            for packet in video_out.encode(): mux(packet)
            for packet in audio_out.encode(): mux(packet)
        if output_path.stat().st_size > MAX_BYTES:
            raise SequenceError("合成视频超过 50 MB，片段已保留，本次未发送。")
        clip_info(output_path.read_bytes(), expected=duration)
        return output_path.read_bytes()
    except SequenceError:
        raise
    except (av.FFmpegError, OSError, ValueError, TypeError):
        raise SequenceError("视频合成未完成，已生成片段保留，无需重复生成。") from None
