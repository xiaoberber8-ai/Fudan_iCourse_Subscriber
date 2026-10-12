#!/usr/bin/env bash
# ffmpeg wrapper — adaptive mono downmix for phase-inverted lectures.
#
# 背景：部分 iCourse 教室的回放存在录课声道故障——讲课人声以反相方式
# 分布在左右声道并叠加同相环境噪声。ffmpeg 默认 `-ac 1` 做 (L+R)/2
# 下混时人声相互抵消，silero VAD / SenseVoice 收到近似静音（0 segments），
# 讲座被当成"空转录"静默标记为已处理，永不发邮件。改用 side 声道
# (FL-FR)/2 可抵消同相噪声、保留反相人声（实测每 60s 含 19~41s 语音）。
#
# 但不能对所有回放强制 side：普通双单声道(dual-mono)回放 L==R，side
# 恰好是静音，会把正常课程搞坏。因此需要对每条音频做 mid/side 自适应。
#
# 关键约束：iCourse 的签名播放 URL（经 WebVPN 中继）**只允许打开一次**，
# 任何额外请求（带 -ss 的探测、同一 URL 的第二次连接）都会得到 403 并
# 使后续打开全部失败（2026-10-12 三节恢复课实证：探针+主下载同 token
# 双双 403）。因此本 wrapper 绝不对 URL 做第二次打开，而是把一次顺序
# 下载拆成「生产者—消费者」两段，全程只有一次网络连接：
#
#   签名URL ─[ffmpeg#1 单次连接,顺序下载,与原命令同参数]─> stereo.raw
#                                                            │ tail -f
#                          本地读前120s做能量判据(不触网)    ▼（不触网）
#                                          [ffmpeg#2 读本地管道 pan 选声道]
#                                                            ▼
#                                         原命令的 f32le 输出路径/stdout
#
# 判据由同目录 chan_probe.py 执行（纯标准库）：0.5s 窗 RMS，高能量窗
# side>1.8×mid 且 >0.015 才选 side；其余（含 dual-mono 的 side≈0、探针
# 数据不足、一切异常）保持 mid。判定按输出名缓存。ffmpeg#1 的错误
# stderr 会透传，保证 403/无音频流等错误仍被原 pipeline 现有逻辑捕获。
# 仅对 `-f f32le` 命令生效，其余透传。
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SELF_DIR="$(dirname "$SELF")"
PROBE="${IC_CHAN_PROBE:-$SELF_DIR/chan_probe.py}"

REAL_FFMPEG="$(which -a ffmpeg 2>/dev/null \
  | while read -r p; do readlink -f "$p"; done \
  | grep -v -F "$SELF" | head -n1 || true)"
[ -z "$REAL_FFMPEG" ] && REAL_FFMPEG=/usr/bin/ffmpeg

args=("$@")

# ── 仅处理 f32le 音频抽取；其余直接透传 ──────────────────────────────
is_f32le=0
for ((i=0; i<${#args[@]}-1; i++)); do
  [ "${args[$i]}" = "-f" ] && [ "${args[$((i+1))]}" = "f32le" ] && is_f32le=1
done
if [ "$is_f32le" != "1" ]; then
  exec "$REAL_FFMPEG" "${args[@]}"
fi

# 输出目标（f32le 后的参数）；"-" 表示 stdout 管道。
outpath=""
for ((i=0; i<${#args[@]}-1; i++)); do
  if [ "${args[$i]}" = "-f" ] && [ "${args[$((i+1))]}" = "f32le" ]; then
    outpath="${args[$((i+2))]:-}"
  fi
done

CACHE_DIR="${TMPDIR:-/tmp}/icourse_chan_mode"
mkdir -p "$CACHE_DIR"
if [ "$outpath" != "-" ] && [ -n "$outpath" ]; then
  key="$(printf '%s' "$(basename "$outpath")" | sha1sum | cut -c1-16)"
else
  inp=""
  for ((i=0; i<${#args[@]}-1; i++)); do
    [ "${args[$i]}" = "-i" ] && inp="${args[$((i+1))]}"
  done
  key="$(printf '%s' "$inp" | sha1sum | cut -c1-16)"
fi
mode_file="$CACHE_DIR/$key"
stereo="$CACHE_DIR/$key.stereo.raw"
prod_err="$CACHE_DIR/$key.prod.err"
diag="$CACHE_DIR/$key.diag"
NEED=30720000   # 120s 立体声 16k f32

# ── 生产者参数：保持原命令的单次顺序打开，仅 -ac 1→2、输出换 stereo ──
prod_args=()
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  if [ "$a" = "-ac" ] && [ "${args[$((i+1))]:-}" = "1" ]; then
    prod_args+=("-ac" "2"); i=$((i+2)); continue
  fi
  if [ "$a" = "-f" ] && [ "${args[$((i+1))]:-}" = "f32le" ]; then
    prod_args+=("-f" "f32le" "$stereo")
    # 跳过原 -f / f32le / 输出名
    i=$((i+1)); i=$((i+1))
    [ "$i" -lt "${#args[@]}" ] && i=$((i+1))
    continue
  fi
  prod_args+=("$a"); i=$((i+1))
done
# 原命令没有 -ac 时，在 -f f32le 前补 -ac 2
if ! printf '%s\0' "${prod_args[@]}" | grep -q -z -- '-ac'; then
  rebuilt=(); j=0
  while [ "$j" -lt "${#prod_args[@]}" ]; do
    if [ "${prod_args[$j]}" = "-f" ] && [ "${prod_args[$((j+1))]:-}" = "f32le" ]; then
      rebuilt+=("-ac" "2")
    fi
    rebuilt+=("${prod_args[$j]}"); j=$((j+1))
  done
  prod_args=("${rebuilt[@]}")
fi

rm -f "$stereo" "$prod_err" "$diag"

PROD_PID=""; TAIL_ERR_PID=""; CONSUMER_PID=""
cleanup() {
  # 消费者以 setsid 独立进程组运行，按组终止确保 tail|ffmpeg 都被收掉
  [ -n "$CONSUMER_PID" ] && kill -TERM "-$CONSUMER_PID" 2>/dev/null || true
  [ -n "$PROD_PID" ] && kill "$PROD_PID" 2>/dev/null || true
  [ -n "$TAIL_ERR_PID" ] && kill "$TAIL_ERR_PID" 2>/dev/null || true
  rm -f "$stereo" "$prod_err" "$diag" 2>/dev/null || true
}
trap cleanup EXIT TERM INT

# 原 pipeline 最多等 60s 看到输出文件，先建空文件占位（-y 会被截断重写）
if [ "$outpath" != "-" ] && [ -n "$outpath" ]; then
  mkdir -p "$(dirname "$outpath")"
  : > "$outpath"
fi

"$REAL_FFMPEG" "${prod_args[@]}" 2>"$prod_err" &
PROD_PID=$!
# 生产者 stderr 在其结束后完整透传（scheduler 靠它报 403/无流错误）。
# 不用 tail -f --pid：它可能在进程死讯与最后几行落盘之间提前退出丢字。
{
  while kill -0 "$PROD_PID" 2>/dev/null; do sleep 1; done
  cat "$prod_err" 2>/dev/null
} >&2 &
TAIL_ERR_PID=$!

# 等立体声数据够 120s，或生产者提前退出
deadline=$((SECONDS+220))
while :; do
  sz=$(stat -c%s "$stereo" 2>/dev/null || echo 0)
  [ "$sz" -ge "$NEED" ] && break
  kill -0 "$PROD_PID" 2>/dev/null || break
  [ "$SECONDS" -ge "$deadline" ] && break
  sleep 2
done

# 生产者存活状态与已落盘字节数
sz=$(stat -c%s "$stereo" 2>/dev/null || echo 0)
prod_dead=0
kill -0 "$PROD_PID" 2>/dev/null || prod_dead=1

# 生产者已死且几乎没产出：直接按其退出码失败，并把完整错误 stderr 透传
if [ "$prod_dead" = "1" ] && [ "$sz" -lt 640000 ]; then
  wait "$PROD_PID"; rc=$?
  [ -n "$TAIL_ERR_PID" ] && { sleep 1; kill "$TAIL_ERR_PID" 2>/dev/null || true; }
  cat "$prod_err" >&2 2>/dev/null || true
  echo "[ffmpeg-adaptive] producer exited with no audio (rc=$rc)" >&2
  exit "$rc"
fi

# ── 本地判据（缓存优先；数据不足只对本次运行默认 mid，不落盘） ───────
mode=""
[ -f "$mode_file" ] && mode="$(cat "$mode_file" 2>/dev/null || true)"
if [ -z "$mode" ] && [ "$sz" -ge 1280000 ] && [ -f "$PROBE" ]; then
  mode="$(python3 "$PROBE" "$stereo" "$NEED" 2>"$diag")" || mode=""
  cat "$diag" >&2 || true
  if [ -n "$mode" ]; then
    printf '%s' "$mode" > "$mode_file"
  fi
fi
[ -z "$mode" ] && mode="mid"

pan="pan=mono|c0=0.5*FL+0.5*FR"
[ "$mode" = "side" ] && pan="pan=mono|c0=0.5*FL-0.5*FR"
echo "[ffmpeg-adaptive] $key -> $mode channel" >&2

# ── 消费者：tail 本地 stereo.raw（--pid 保证生产者死后收束），不触网 ──
# 独立进程组，便于 wrapper 被 SIGTERM 时整组回收。
setsid bash -s "$PROD_PID" "$stereo" "$REAL_FFMPEG" "$pan" "$outpath" <<'CONS' &
set -o pipefail
tail -n +1 -f --pid="$1" "$2" 2>/dev/null \
  | "$3" -hide_banner -loglevel error -y \
      -f f32le -ar 16000 -ac 2 -i pipe:0 \
      -af "$4" -ar 16000 \
      -f f32le "$5"
CONS
CONSUMER_PID=$!
wait "$CONSUMER_PID"; crc=$?

wait "$PROD_PID" 2>/dev/null && prc=0 || prc=$?
[ -n "$TAIL_ERR_PID" ] && kill "$TAIL_ERR_PID" 2>/dev/null || true

[ "$crc" -ne 0 ] && exit "$crc"
exit "$prc"
