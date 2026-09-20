#!/usr/bin/env bash
# ffmpeg wrapper — extract the SIDE channel (L-R)/2 instead of the default
# mono downmix (L+R)/2 for the pipeline's f32le audio extraction.
#
# 背景：少数 iCourse 回放存在录课声道故障——讲课人声以反相方式分布在
# 左右声道，并叠加同相环境噪声。ffmpeg 默认 `-ac 1` 做 (L+R)/2 下混时
# 人声相互抵消，SenseVoice/silero VAD 收到近似静音（0 segments），课程
# 被当成"空转录"静默标记为已处理，永远不发邮件。
#
# Side 声道 (FL-FR)/2 恰好抵消同相噪声、保留反相人声。已在故障课程
# sub_id=660467 上验证：标准下混全片 VAD 0 秒，side 声道每 60 秒有
# 7~41 秒语音，SenseVoice 可识别出通顺的讲课内容。
#
# 本 wrapper 只改写含 `-f f32le` 的音频抽取命令（scheduler.py 下载与
# transcriber.py 的兜底命令），其他 ffmpeg 调用原样透传。它仅由
# .github/workflows/recover_sidechannel.yml 通过 PATH 前置启用，日常
# iCourse Check 不受影响。
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SELF_DIR="$(dirname "$SELF")"

REAL_FFMPEG="$(which -a ffmpeg 2>/dev/null \
  | while read -r p; do readlink -f "$p"; done \
  | grep -v -F "$SELF" | head -n1 || true)"
if [ -z "$REAL_FFMPEG" ]; then
  REAL_FFMPEG=/usr/bin/ffmpeg
fi

args=("$@")
out=()
rewrite=0

for ((i=0; i<${#args[@]}; i++)); do
  a="${args[$i]}"
  nxt="${args[$((i+1))]:-}"
  # Drop an explicit "-ac 1": the pan filter below already defines a
  # mono output layout, and keeping both only adds a redundant resample.
  if [ "$a" = "-ac" ] && [ "$nxt" = "1" ]; then
    i=$((i+1))
    rewrite=1
    continue
  fi
  # This is an f32le PCM extraction — inject the side-channel pan.
  if [ "$a" = "-f" ] && [ "$nxt" = "f32le" ]; then
    out+=("-af" "pan=mono|c0=0.5*FL-0.5*FR")
    rewrite=1
  fi
  out+=("$a")
done

if [ "$rewrite" = "1" ]; then
  echo "[ffmpeg-side-channel] using (FL-FR)/2 side channel" >&2
fi
exec "$REAL_FFMPEG" "${out[@]}"
