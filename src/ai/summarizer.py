"""LLM-based course lecture summarization via ModelScope API."""

import os
import re
import time

from openai import OpenAI

from src.runtime import config

# 输出长度目标 ≈ 输入字符数 / 该除数。1:3 的压缩比（原来 1:6）用于产出
# 覆盖全部知识点的详细复习笔记；可用环境变量临时覆盖。
OUTPUT_DIVISOR = max(1, int(os.environ.get("SUMMARY_OUTPUT_DIVISOR", "3")))
# 单次 LLM 调用最多承载多少输入字符；超出则按时段分段多次总结后拼接。
CHUNK_MAX_CHARS = int(os.environ.get("SUMMARY_CHUNK_MAX_CHARS", "14000"))
# bucketed 输入中，单次调用最多包含多少个 10 分钟时段桶。
MAX_BUCKETS_PER_CHUNK = int(os.environ.get("SUMMARY_MAX_BUCKETS", "6"))

# bucketer.assemble_bucketed() 生成的时段分隔行：=== 时间段 mm:ss – mm:ss ===
_BUCKET_MARK = re.compile(r"^=== 时间段 (.+?) – (.+?) ===\s*$", re.M)

SYSTEM_PROMPT= r"""你是一个专业的课程助教。你的任务是根据用户提供的课程录音文本和ppt文字ocr部分，生成用于学生自学和期末复习的详细笔记。
1. **直接输出**：不要包含任何"好的"、"没问题"、"以下是总结"等客套话，不要输出全局课程名称大标题（由系统自动生成），直接开始总结即可。
2. **文本清洗**：语言必须通顺、逻辑清晰，严格去除口语化表达、重复句和无意义的录音识别错误等。内容可能被识别成同音字，需要通过学术语境修复。
3. **格式严格**：
   - 必须使用 Markdown 格式排版。
   - 标题结构清晰与级别限制：只允许使用三级及后续级别的标题（即只能使用`###`、`####`或`#####`），禁止使用 `#` 和 `##`。用清晰的标题组织结构。
   - 不得使用超过两级的缩进和超过两级嵌套的bullet point。
   - 尽可能用完整的段落来组织老师的讲解，适量使用bullet point，不得过度使用bullet point
   - 合理使用加粗、列表、表格、分级标题、段首小标题等形式来组织信息，确保结构清晰。
4. **公式规范**：所有数学公式或科学变量必须使用规范的 LaTeX 语法（行内公式用 $...$，行间公式用 $$...$$）。由于图床限制，latex公式中不要出现中文。
5. **忠于原文与详尽**：笔记必须忠实覆盖老师讲授的全部知识点，宁可详尽、不可笼统：
   - 保留每个核心概念的**完整定义与提出动机**、每个公式中符号的含义与推导的关键步骤（推导链不要跳步）、每个例题/案例的具体设定、数据、结论，以及课程中提到的文献、应用背景、不同方法之间的对比。
   - 每个知识点都要用完整的自然段展开（是什么、为什么成立、直观含义、适用条件或前提、容易出错的地方），**禁止只用"介绍了 XX""讲解了 XX 的方法"这类一句话概括**，禁止把多个知识点压成一条短句 bullet。
   - 总体压缩比例约 1:3（输出长度约为输入的三分之一；例如 90 分钟课程约 8000 字，135 分钟课程约 12000 字）。允许写得更长，不要为了压缩而丢弃细节。
   - 只去除口头禅、重复语句、纯闲聊与识别噪声；严禁捏造录音和 PPT 中都没有提到的内容。
6. 你需要格外注意课程中是否提及了作业、考试、签到、组队等关键事项，如果有的话，用三级标题【课程事项提醒】标注在开头。
7. **文风示例**：以下是一个关于"梯度下降"的片段，展示了笔记总结过程中【错误的】和【正确的】的两种总结风格，请严格模仿后者。

【❌ 错误的风格】
## 梯度下降

**定义：**
- 梯度下降是一种优化算法
- 用于最小化损失函数
- 广泛应用于机器学习

**核心步骤：**
- 计算梯度
- 更新参数
- 重复迭代

**学习率：**
- 学习率决定步长
- 太大会发散
- 太小会收敛慢
- 需要调参

**类型：**
- 批量梯度下降（BGD）
- 随机梯度下降（SGD）
- 小批量梯度下降（Mini-batch GD）

---

【✅ 正确的风格】

### 梯度下降

梯度下降是最小化损失函数 $L(\theta)$ 的核心优化算法。其基本思想是沿着损失函数对参数 $\theta$ 的梯度的反方向迭代更新，每一步的更新公式为 $\theta \leftarrow \theta - \eta \nabla_\theta L(\theta)$，其中 $\eta$ 称为学习率，控制每次更新的步长大小。

**学习率的选取至关重要**：若 $\eta$ 过大，参数更新幅度过猛，损失函数可能在最优点附近震荡甚至发散；若 $\eta$ 过小，收敛速度极慢，训练成本大幅上升。实践中通常通过**学习率调度（learning rate schedule）**或**自适应方法（如 Adam）**来缓解这一问题。

根据每次更新时使用的样本量，梯度下降可分为三类：**批量梯度下降（BGD）** 每次使用全部训练数据，梯度估计准确但计算开销大；**随机梯度下降（SGD）** 每次仅用单个样本，更新频繁但噪声大；**小批量梯度下降（Mini-batch GD）** 则折中两者，是深度学习中最常用的形式。
---

8. **输入材料格式**：
   - 你收到的输入可能是两种格式之一：
     A) **带时间轴的分段格式**：用 `=== 时间段 mm:ss – mm:ss ===` 分隔每个 10 分钟段；段内有【音频转录】和【PPT 文字识别】两类内容。
     B) **平铺格式**：先一段【音频转录（无时间轴）】，后一段【PPT 文字识别（按出现顺序）】，每张 PPT 带 `[页 N @ mm:ss]` 标签。
   - 无论哪种格式，**PPT 文字是真实的板书 / 课件内容**，比音频识别噪声更可靠；遇到音频识别错的术语（同音字、专有名词），优先以 PPT 文字为准修正，但PPT的文字也可能有ocr的错误。
   - 但是，你的总结的组织的主线仍然应该以讲师的讲解为逻辑组织，同时把PPT的信息补充进去。
   - 同时，需要注意的是，由于该平台以屏幕截图的方式记录ppt，因此，尽管我们提供的ocr版本已经尽最大努力进行了清洗，你收到的ppt仍然可能有无关网页、导航栏、桌面或系统页面等无关噪音。这些噪音不应被用于课程内容的总结。
   - 输出仍按之前的格式要求，不要保留时间戳标签，只把这些信息当作上下文辅助理解，不用说哪些来自转写哪些来自ppt，自然地合并录音转写和ppt中的知识，生成高质量笔记。
9. **分段输入时的要求**：长篇课程会被切成多个部分分别提供给你。每次你只会看到课程的**一个部分**，此时只需整理该部分覆盖的内容，保持与独立笔记相同的详尽程度：
   - 不要输出课程总标题、开场客套或跨部分的结尾总括（拼接工作由系统完成）。
   - 该部分若提及作业、考试、签到、组队等事项，用三级标题【课程事项提醒】写在**该部分输出的开头**。
   - 各部分之间内容独立，不要因为"前面/后面可能讲过"而省略本部分的定义、公式和例题。"""


def _split_flat(text: str, max_chars: int) -> list[str]:
    """把平铺文本按段落边界切成不超过 max_chars 的块。"""
    paras = text.split("\n")
    chunks: list[str] = []
    buf: list[str] = []
    cur = 0
    for para in paras:
        piece = para + "\n"
        if buf and cur + len(piece) > max_chars:
            chunks.append("\n".join(buf).strip())
            buf, cur = [], 0
        # 单个段落超长时硬切，保证有界
        while len(piece) > max_chars:
            if buf:
                chunks.append("\n".join(buf).strip())
                buf, cur = [], 0
            chunks.append(piece[:max_chars].strip())
            piece = piece[max_chars:]
        if piece.strip():
            buf.append(piece.rstrip("\n"))
            cur += len(piece)
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if c]


def split_chunks(content: str,
                 max_chars: int = CHUNK_MAX_CHARS,
                 max_buckets: int = MAX_BUCKETS_PER_CHUNK,
                 ) -> list[tuple[str | None, str]]:
    """把组装好的提示词切成多次 LLM 调用。

    返回 ``[(time_range, text), ...]``：bucketed 输入按时段桶分组（每块
    至多 ``max_buckets`` 个 10 分钟桶且不超过 ``max_chars`` 字符），
    flat 输入在超长时按段落切块。短输入永远只有一块（保持原有单次
    调用行为）。
    """
    markers = list(_BUCKET_MARK.finditer(content))
    if not markers:
        if len(content) <= max_chars:
            return [(None, content)]
        return [(None, c) for c in _split_flat(content, max_chars)]

    raw: list[tuple[str | None, str]] = []
    start = 0
    first = 0
    for k in range(1, len(markers) + 1):
        if k == len(markers):
            rng = f"{markers[first].group(1)} – {markers[k - 1].group(2)}"
            raw.append((rng, content[start:].strip()))
            break
        cur_len = markers[k].start() - start
        bucket_count = k - first
        if cur_len > max_chars or bucket_count >= max_buckets:
            rng = f"{markers[first].group(1)} – {markers[k - 1].group(2)}"
            raw.append((rng, content[start:markers[k].start()].strip()))
            start = markers[k].start()
            first = k

    # 兜底：极端密集的单个桶仍可能超长，对这种块做段落级硬切。
    out: list[tuple[str | None, str]] = []
    for rng, text in raw:
        if len(text) <= max_chars:
            out.append((rng, text))
        else:
            out.extend((rng, c) for c in _split_flat(text, max_chars))
    return [(rng, t) for rng, t in out if t]


class Summarizer:
    """Course lecture summarizer with multi-provider fallback.

    Iterates config.MODEL_PROVIDERS in declared order. Within each provider,
    tries each model in declared order. Returns the first successful result.
    Setting only DASHSCOPE_API_KEY still works because the default
    MODEL_PROVIDERS list ships a modelscope entry that reads it.

    Long lectures are split into time-ordered chunks; each chunk is
    summarized with the same detailed-notes prompt and the results are
    concatenated, so detail does not get averaged away in one giant call.
    """

    def __init__(self):
        self.providers = config.resolve_model_providers()
        if not self.providers:
            raise ValueError(
                "No model provider available. "
                "Set at least one provider's API key (e.g. DASHSCOPE_API_KEY)."
            )
        self._clients = {
            p["name"]: OpenAI(api_key=p["api_key"], base_url=p["base_url"])
            for p in self.providers
        }

    def _build_user_text(self, title: str, content: str, target: int,
                         part: int, total_parts: int,
                         time_range: str | None) -> str:
        if total_parts <= 1:
            return (
                f"以下是课程《{title}》的录音文本与PPT识别内容，"
                f"你应该输出的字符数大约为{target}字（允许超过，不允许明显短于该目标），"
                f"请生成详细的复习笔记：\n\n{content}"
            )
        span = f"（约 {time_range}）" if time_range else ""
        return (
            f"以下是课程《{title}》课堂记录的第 {part}/{total_parts} 部分{span}。"
            f"请只整理这一部分，输出字符数大约为{target}字（允许超过，不允许明显短于该目标），"
            f"详尽程度与独立笔记相同，不要写课程总标题、开场客套或结尾总括。内容如下：\n\n{content}"
        )

    def _call_llm(self, client: OpenAI, model: str,
                  title: str, content: str,
                  part: int = 1, total_parts: int = 1,
                  time_range: str | None = None) -> str:
        target = max(800, len(content) // OUTPUT_DIVISOR)
        user_text = self._build_user_text(
            title, content, target, part, total_parts, time_range,
        )

        t0 = time.time()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            # temperature=0.3,
            timeout=300,
        )
        if not response.choices:
            raise ValueError("API returned empty choices — likely content filter or quota exceeded")
        result = response.choices[0].message.content

        # 长度护栏：模型偶尔无视字数目标、输出过度概括（实测可短至目标的
        # 2/3 以下）。此时带上原稿要求重写一次；仍不足也接受第二次结果，
        # 避免无限重试。
        min_chars = int(target * 0.75)
        if result and len(result) < min_chars:
            print(
                f"[Summarizer] {model} output too short "
                f"({len(result)} < {min_chars}); requesting an expanded rewrite"
            )
            span = f"（约 {time_range}）" if time_range else ""
            scope = (
                "该课程" if total_parts <= 1
                else f"第 {part}/{total_parts} 部分{span}"
            )
            expand_text = (
                f"你刚才为{scope}生成的笔记只有约 {len(result)} 字，"
                f"明显过于概括，一定遗漏或压缩了原始材料中的定义、公式推导、例题与结论。"
                f"请严格依据下面的原始材料，重新输出一份完整的详细笔记"
                f"（不是在旧稿后面简单补几段，而是从头组织、逐知识点展开完整段落），"
                f"字符数应达到 {target} 字以上。原始材料：\n\n{content}"
            )
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": expand_text},
                ],
                timeout=300,
            )
            if not response.choices:
                raise ValueError("API returned empty choices on expansion retry")
            expanded = response.choices[0].message.content
            if expanded and len(expanded) > len(result):
                result = expanded

        elapsed = time.time() - t0
        # Token usage helps explain run cost — every provider's billing is
        # token-based, and rate-limit decisions key off prompt size much
        # more than character count.  Some providers (OpenAI-compatible)
        # leave usage None on streaming or error paths, so fall back to
        # plain "no usage" line so the summary still prints.
        usage = getattr(response, "usage", None)
        part_tag = f"part {part}/{total_parts}, " if total_parts > 1 else ""
        if usage is not None:
            print(
                f"[Summarizer] Done ({model}) [{part_tag}"
                f"{len(content)} chars input → {len(result)} chars output"
                f" in {elapsed:.0f}s "
                f"(tokens: prompt={getattr(usage,'prompt_tokens','?')}, "
                f"completion={getattr(usage,'completion_tokens','?')})]"
            )
        else:
            print(
                f"[Summarizer] Done ({model}) [{part_tag}"
                f"{len(content)} chars input → {len(result)} chars output"
                f" in {elapsed:.0f}s]"
            )
        return result

    def summarize(self, title: str, content: str) -> tuple[str, str]:
        """Summarize lecture, trying providers in MODEL_PROVIDERS order.

        Long content is summarized in multiple chunks; a model must finish
        every chunk to win — on failure the whole lecture falls through to
        the next configured model.

        Returns (summary, model_used) where model_used is "{provider}/{model}".

        Raises:
            RuntimeError: if all providers/models fail.
        """
        if not content or not content.strip():
            return ("（内容为空）", "")

        chunks = split_chunks(content)
        n = len(chunks)
        print(
            f"[Summarizer] {title!r}: {len(content)} chars input split "
            f"into {n} chunk(s) for summarization"
        )

        errors = []
        for provider in self.providers:
            client = self._clients[provider["name"]]
            for model in provider["models"]:
                model_id = f"{provider['name']}/{model}"
                parts: list[str] = []
                try:
                    for idx, (time_range, chunk) in enumerate(chunks, start=1):
                        parts.append(
                            self._call_llm(
                                client, model, title, chunk,
                                part=idx, total_parts=n, time_range=time_range,
                            )
                        )
                    return ("\n\n".join(p.strip() for p in parts if p.strip()),
                            model_id)
                except Exception as e:
                    print(f"[Summarizer] {model_id} failed: "
                          f"{type(e).__name__}: {e}")
                    errors.append(f"{model_id}: {e}")

        raise RuntimeError(
            "All LLM models failed:\n" + "\n".join(errors)
        )
