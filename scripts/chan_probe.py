#!/usr/bin/env python3
"""判定一份 16kHz/2ch/float32le PCM 录音应使用 mid 还是 side 单声道下混。

背景见 scripts/ffmpeg_side_channel.sh 顶部注释：部分 iCourse 回放人声反相
分布在左右声道，默认 (L+R)/2 下混会抵消人声；改用 (L-R)/2 可保留人声、
抵消同相噪声。普通双单声道(dual-mono)录音 L==R，side 近似静音，不能误用。

只使用 Python 标准库（CI ubuntu 自带 /usr/bin/python3 无 numpy）。

用法: chan_probe.py <stereo.f32le.raw> [max_bytes]
输出: stdout 最后一行打印 "side" 或 "mid"；判定 RMS 写到 stderr。
"""
import os
import sys
import struct
import math

WIN = 16000 // 2          # 0.5s 窗，8000 采样
MIN_FRAMES = 16000 * 5    # 至少 5s 才判定


def main() -> int:
    path = sys.argv[1]
    need = int(sys.argv[2]) if len(sys.argv) > 2 else 30_720_000  # 120s
    with open(path, "rb") as fh:
        data = fh.read(min(need, os.path.getsize(path)))
    n = len(data) // 8  # 2ch * float32
    if n < MIN_FRAMES:
        print("mid")
        return 0

    nwin = n // WIN
    sL = [0.0] * nwin
    sR = [0.0] * nwin
    sM = [0.0] * nwin
    sS = [0.0] * nwin
    i = 0
    for l, r in struct.iter_unpack("<ff", data[: nwin * WIN * 8]):
        wno = i // WIN
        m, s = (l + r) * 0.5, (l - r) * 0.5
        sL[wno] += l * l
        sR[wno] += r * r
        sM[wno] += m * m
        sS[wno] += s * s
        i += 1

    def rms(arr):
        return [math.sqrt(x / WIN) for x in arr]

    rL, rR, rM, rS = rms(sL), rms(sR), rms(sM), rms(sS)
    content = [max(a, b) for a, b in zip(rL, rR)]
    floor = max(0.01, sorted(content)[len(content) // 2])
    idx = sorted((j for j, c in enumerate(content) if c >= floor),
                 key=lambda j: content[j], reverse=True)
    idx = idx[: max(1, int(len(idx) * 0.4))]
    if not idx:
        print("mid")
        return 0
    rm = sum(rM[j] for j in idx) / len(idx)
    rs = sum(rS[j] for j in idx) / len(idx)
    if rs > 1.8 * rm and rs > 0.015:
        sys.stderr.write("side  (probe RMS mid=%.4f side=%.4f)\n" % (rm, rs))
        print("side")
    else:
        sys.stderr.write("mid   (probe RMS mid=%.4f side=%.4f)\n" % (rm, rs))
        print("mid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
