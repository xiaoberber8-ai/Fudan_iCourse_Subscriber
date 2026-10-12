#!/usr/bin/env bash
# ffmpeg wrapper — adaptive mono downmix for phase-inverted lectures.
#
# 背景：部分 iCourse 教室的回放存在录课声道故障——讲课人声以反相方式
# 分布在左右声道并叠加同相环境噪声。ffmpeg 默认 `-ac 1` 做 (L+R)/2
# 下混时人声相互抵消，silero VAD / SenseVoice 收到近似静音（0 segments），
# 讲座被当成"空转录"静默标记为已处理，永不发邮件。改用 side 声道
# (FL-FR)/2 可抵消同相噪声、保留反相人声（实测每 60s 含 20~40s 语音）。
#
# 但不能对所有回放强制 side：普通双单声道(dual-mono)回放 L==R，side
# 恰好是静音，会把正常课程搞坏。因此本 wrapper 对每条 `-f f32le`
# 音频抽取命令：
#   1. 先抽取第 60~180 秒的立体声短探针；
#   2. 用 numpy 比较高能量窗上 mid(L+R)/2 与 side(L-R)/2 的 RMS，
#      仅当 side 能量显著占优（>1.8 倍且绝对值足够）才选 side，
#      其余一切情况保持原 mid 行为（与原生 -ac 1 等价）；
#   3. 判定结果按输出路径缓存，同一讲座不重复探测。
# 非 f32le 的 ffmpeg 调用原样透传；探测失败也安全回退 mid。
#
# 仅由 .github/workflows/recover_sidechannel.yml 与
# icourse_check_adaptive.yml 通过 PATH 前置启用；不改动任何原有代码。
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"

REAL_FFMPEG="$(which -a ffmpeg 2>/dev/null \
  | while read -r p; do readlink -f "$p"; done \
  | grep -v -F "$SELF" | head -n1 || true)"
if [ -z "$REAL_FFMPEG" ]; then
  REAL_FFMPEG=/usr/bin/ffmpeg
fi

args=("$@")

# ── 仅改写 f32le 音频抽取命令；其余直接透传 ──────────────────────────
is_f32le=0
input=""
headers=()
outpath=""
for ((i=0; i<${#args[@]}; i++)); do
  a="${args[$i]}"
  case "$a" in
    -f) [ "${args[$((i+1))]:-}" = "f32le" ] && is_f32le=1 ;;
    -i) input="${args[$((i+1))]:-}" ;;
    -headers) headers=("-headers" "${args[$((i+1))]:-}") ;;
  esac
  if [ "$a" = "-f" ] && [ "${args[$((i+1))]:-}" = "f32le" ]; then
    outpath="${args[$((i+2))]:-}"
  fi
done

if [ "$is_f32le" != "1" ]; then
  exec "$REAL_FFMPEG" "${args[@]}"
fi

# ── 缓存判定结果（pipe 输出按输入 URL 哈希） ──────────────────────────
CACHE_DIR="${TMPDIR:-/tmp}/icourse_chan_mode"
mkdir -p "$CACHE_DIR"
if [ "$outpath" != "-" ] && [ -n "$outpath" ]; then
  key="$(printf '%s' "$(basename "$outpath")" | sha1sum | cut -c1-16)"
else
  key="$(printf '%s' "$input" | sha1sum | cut -c1-16)"
fi
mode_file="$CACHE_DIR/$key"
mode=""
[ -f "$mode_file" ] && mode="$(cat "$mode_file")"

if [ -z "$mode" ]; then
  probe_wav="$CACHE_DIR/probe_$key.wav"
  # 探针：跳过开头 60s（通常是静场/准备），取 120s 立体声。
  # 对远端流 -ss 置于 -i 之前；探测失败不影响主流程，回退 mid。
  remote_args=()
  case "$input" in
    http://*|https://*)
      remote_args=(-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5) ;;
  esac
  probe_ok=0
  if timeout 240 "$REAL_FFMPEG" -hide_banner -loglevel error \
        "${headers[@]+"${headers[@]}"}" \
        "${remote_args[@]+"${remote_args[@]}"}" \
        -ss 60 -t 120 -i "$input" \
        -vn -ac 2 -ar 16000 -f wav "$probe_wav" 2>"$CACHE_DIR/probe_$key.err"; then
    probe_ok=1
  fi
  if [ "$probe_ok" = "1" ]; then
    # 纯标准库：0.5s 窗统计 L/R/mid/side 的 RMS，不依赖 numpy。
    py_prog='
import os, sys, wave, struct, math
try:
    w = wave.open(os.environ["MODE_WAV"], "rb")
    nch, sr, nfr = w.getnchannels(), w.getframerate(), w.getnframes()
    raw = w.readframes(nfr)
except Exception:
    print("mid"); sys.exit(0)
if nch != 2 or nfr < sr * 10:   # 非立体声或探针过短：不做判断
    print("mid"); sys.exit(0)
win = sr // 2
nwin = nfr // win
sL = [0.0]*nwin; sR = [0.0]*nwin; sM = [0.0]*nwin; sS = [0.0]*nwin
i = 0
for l, r in struct.iter_unpack("<hh", raw[: nwin*win*4]):
    wno = i // win
    lf, rf = l/32768.0, r/32768.0
    m, s = (lf+rf)*0.5, (lf-rf)*0.5
    sL[wno] += lf*lf; sR[wno] += rf*rf
    sM[wno] += m*m;  sS[wno] += s*s
    i += 1
rL = [math.sqrt(x/win) for x in sL]
rR = [math.sqrt(x/win) for x in sR]
rM = [math.sqrt(x/win) for x in sM]
rS = [math.sqrt(x/win) for x in sS]
content = [max(a, b) for a, b in zip(rL, rR)]
floor = max(0.01, sorted(content)[len(content)//2])
strong = sorted((c for c in content if c >= floor), reverse=True)
if not strong:
    print("mid"); sys.exit(0)
picked = strong[: max(1, int(len(strong)*0.4))]
# 找回对应窗的 rM/rS（content 可能有并列，按索引重建）
idx = sorted([j for j, c in enumerate(content) if c >= floor],
             key=lambda j: content[j], reverse=True)[:len(picked)]
rm = sum(rM[j] for j in idx)/len(idx)
rs = sum(rS[j] for j in idx)/len(idx)
if rs > 1.8*rm and rs > 0.015:
    sys.stderr.write("side  (probe RMS mid=%.4f side=%.4f)\n" % (rm, rs))
    print("side")
else:
    sys.stderr.write("mid   (probe RMS mid=%.4f side=%.4f)\n" % (rm, rs))
    print("mid")
'
    mode="$(MODE_WAV="$probe_wav" python3 -c "$py_prog" 2>/tmp/chan_probe_diag.log)" || mode=""
    cat /tmp/chan_probe_diag.log >&2 || true
  fi
  [ -z "$mode" ] && mode="mid"
  printf '%s' "$mode" > "$mode_file"
fi

pan="pan=mono|c0=0.5*FL+0.5*FR"
if [ "$mode" = "side" ]; then
  pan="pan=mono|c0=0.5*FL-0.5*FR"
fi
echo "[ffmpeg-adaptive] $key -> $mode channel" >&2

# ── 用选定声道重写参数：去掉 -ac 1，在 -f f32le 前插入 -af pan ───────
out=()
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  if [ "$a" = "-ac" ] && [ "${args[$((i+1))]:-}" = "1" ]; then
    i=$((i+2)); continue
  fi
  if [ "$a" = "-f" ] && [ "${args[$((i+1))]:-}" = "f32le" ]; then
    out+=("-af" "$pan")
  fi
  out+=("$a")
  i=$((i+1))
done

exec "$REAL_FFMPEG" "${out[@]}"
