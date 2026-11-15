"""LLM 客户端与 LLM-as-Judge。

## 为什么要重写原来的裁判

原 `eval_rag_params.py` 的裁判有三个致命缺陷，直接导致那份评测报告不可信
（六组配置全部得到 5.0/5.0/5.0，没有任何区分度）：

1. **用"回答里最后三个数字"当评分**
   `nums = re.findall(r'(\\d+)', raw); int(nums[-3])`
   —— 模型只要在结尾补一句"以上共 3 项"就会污染分数；顺序也可能整体错位。

2. **0-10 连续打分**。4B 小模型对细粒度分数的分辨力接近随机，且系统性偏向
   中间值（这就是为什么到处都是 5.0）。改成**离散判定**（支持/不支持、通过/不通过）
   后，小模型的准确率会显著提升。

3. **无结构化约束**。靠正则从自由文本里抠数字，格式漂移即失效。

## 本模块的做法

* 强制 JSON 输出 + JSON Schema 约束，解析失败记为 `unknown` 而不是猜一个分数；
* 判定粒度离散化：蕴含判定 `supported / not_supported / unknown`，
  完成度判定 `pass / fail`；
* **自一致性**：同一判定采样 N 次取多数票，降低单次随机性；
* **结果缓存**：key = hash(任务, 模型, 输入)，避免重复调用；
* **双裁判后端**：`ollama`（本地）与 `dashscope`（阿里云百炼，OpenAI 兼容）。
  默认推荐 dashscope —— 被测对象是本地 4B 小模型，裁判用强模型，
  既避免"小模型裁判退化成同分"，也符合评测学惯例。
  关键在于：**裁判模型与被测模型相互独立**，这个结论才有说服力。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import requests

from config import settings
from resilience import Deadline, ErrorKind, classify

log = logging.getLogger("slope.judge")


# ══════════════════════════════════════════════════════════════════
# JSON 解析
# ══════════════════════════════════════════════════════════════════
def extract_json(raw: str) -> Optional[dict]:
    """从模型输出中稳健地抽取 JSON 对象。

    小模型与推理型模型都可能在 JSON 前后加解释或 markdown 围栏，
    这里逐级降级解析；全部失败返回 None（调用方据此记 unknown）。
    """
    if not raw:
        return None
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ══════════════════════════════════════════════════════════════════
# 客户端：Ollama（本地）
# ══════════════════════════════════════════════════════════════════
class OllamaClient:
    """Ollama HTTP API 封装（被测对象所在后端，也可作为裁判兜底）。"""

    provider = "ollama"

    def __init__(self, base_url: Optional[str] = None) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")

    def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system: str = "",
        json_schema: Optional[dict] = None,
        timeout: Optional[float] = None,
        temperature: Optional[float] = None,
        deadline: Optional[Deadline] = None,
    ) -> str:
        model = model or settings.effective_judge_model
        cap = timeout if timeout is not None else settings.timeout_llm_judge
        if deadline is not None:
            cap = deadline.budget(cap)

        payload: dict[str, Any] = {
            "model": model,
            "messages": ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": prompt}],
            "stream": False,
            "think": bool(settings.llm_think),
            "options": {
                "temperature": settings.judge_temperature if temperature is None else temperature,
                "num_predict": settings.judge_num_predict,
            },
        }
        if json_schema is not None:
            payload["format"] = json_schema

        return self._post_with_retry(f"{self.base_url}/api/chat", payload, cap).get(
            "message", {}
        ).get("content", "") or ""

    @staticmethod
    def _post_with_retry(url: str, payload: dict, timeout: float) -> dict:
        last: Optional[BaseException] = None
        for attempt in range(settings.retry_max_attempts + 1):
            try:
                resp = requests.post(url, json=payload, timeout=timeout)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                return resp.json()
            except BaseException as exc:  # noqa: BLE001
                last = exc
                if classify(exc) == ErrorKind.FATAL:
                    raise
                if attempt < settings.retry_max_attempts:
                    time.sleep(settings.retry_backoff_base * (2 ** attempt))
                    continue
                raise
        raise last if last else RuntimeError("请求失败")

    def chat_json(self, prompt: str, schema: dict, **kwargs) -> Optional[dict]:
        return extract_json(self.chat(prompt, json_schema=schema, **kwargs))

    def is_available(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/api/tags", timeout=5).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:  # noqa: BLE001
            return []


# ══════════════════════════════════════════════════════════════════
# 客户端：阿里云百炼 / 任意 OpenAI 兼容端点
# ══════════════════════════════════════════════════════════════════
class OpenAICompatClient:
    """OpenAI 兼容端点封装（默认阿里云百炼 DashScope）。

    两个必须注意的坑（实测得出）：

    1. **推理型模型会先消耗 reasoning tokens**。用 glm-5.3 时，
       即使只要求输出 `{"verdict":"supported"}`，`max_tokens` 给小了
       （例如 80）会得到被截断的 `'{"'`。因此 `JUDGE_MAX_TOKENS` 必须留足。
    2. **JSON 模式不能替代 schema 校验**。`response_format=json_object`
       只保证"是 JSON"，不保证字段正确，所以仍需 extract_json + 字段校验，
       校验不过一律记 unknown。
    """

    provider = "dashscope"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.base_url = (base_url or settings.effective_judge_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.dashscope_api_key
        if not self.api_key:
            raise RuntimeError(
                "缺少 API Key：请设置 DASHSCOPE_API_KEY（裁判后端需要）"
            )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        system: str = "",
        json_schema: Optional[dict] = None,
        timeout: Optional[float] = None,
        temperature: Optional[float] = None,
        deadline: Optional[Deadline] = None,
    ) -> str:
        model = model or settings.effective_judge_model
        cap = timeout if timeout is not None else settings.timeout_llm_judge
        if deadline is not None:
            cap = deadline.budget(cap)

        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": settings.judge_temperature if temperature is None else temperature,
            "max_tokens": settings.judge_max_tokens,
        }
        if json_schema is not None:
            # 百炼兼容端点支持 response_format=json_object；
            # schema 细节通过提示词约束，解析端再做字段校验。
            payload["response_format"] = {"type": "json_object"}

        last: Optional[BaseException] = None
        for attempt in range(settings.retry_max_attempts + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                    timeout=cap,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"响应缺少 choices: {str(data)[:200]}")
                return choices[0].get("message", {}).get("content", "") or ""
            except BaseException as exc:  # noqa: BLE001
                last = exc
                if classify(exc) == ErrorKind.FATAL:
                    raise
                if attempt < settings.retry_max_attempts:
                    time.sleep(settings.retry_backoff_base * (2 ** attempt))
                    continue
                raise
        raise last if last else RuntimeError("请求失败")

    def chat_json(self, prompt: str, schema: dict, **kwargs) -> Optional[dict]:
        return extract_json(self.chat(prompt, json_schema=schema, **kwargs))

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers, timeout=10)
            return r.status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers, timeout=15)
            return sorted(m.get("id", "") for m in r.json().get("data", []))
        except Exception:  # noqa: BLE001
            return []


def make_client(provider: Optional[str] = None):
    """按配置构造裁判客户端。

    OpenAI 兼容的那一支现已泛化：不限于百炼，也可指向 DeepSeek 等任意兼容端点，
    因此 provider 值接受 openai / deepseek / dashscope / bailian 等同义写法。
    """
    p = (provider or settings.judge_provider or "ollama").lower()
    if p in ("dashscope", "openai", "bailian", "deepseek", "openai_compat"):
        return OpenAICompatClient()
    return OllamaClient()


# ══════════════════════════════════════════════════════════════════
# 裁判结果缓存
# ══════════════════════════════════════════════════════════════════
class JudgeCache:
    """磁盘缓存。评测动辄上千次模型调用，缓存能把重跑成本降到接近零。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or (Path(settings.reports_dir) / "judge_cache.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {}
        self._dirty = False
        self._pending = 0
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                log.warning("裁判缓存损坏，已忽略：%s", self.path)
                self._data = {}

    @staticmethod
    def key(*parts: Any) -> str:
        payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, k: str):
        return self._data.get(k)

    def put(self, k: str, value: Any) -> None:
        self._data[k] = value
        self._dirty = True
        # **增量落盘**：每 20 条写一次磁盘。
        # 原先只在评测结束时 flush 一次，进程一旦卡住/被杀，
        # 整轮已完成的判定全部丢失（同类项目实测卡死 2.5 小时、上千次调用白跑）。
        self._pending += 1
        if self._pending >= 20:
            self.flush()
            self._pending = 0

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            # 先写临时文件再原子替换：避免写一半被中断导致缓存损坏，
            # 进而让下一次运行从头再算一遍。
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(self.path)
            self._dirty = False
        except Exception as exc:  # noqa: BLE001
            log.warning("裁判缓存写入失败：%s", exc)

    def __len__(self) -> int:
        return len(self._data)


# ══════════════════════════════════════════════════════════════════
# 陈述切分
# ══════════════════════════════════════════════════════════════════
def split_claims(text: str, max_claims: int = 12) -> list[str]:
    """把一段回答切成原子陈述。

    用规则切分而非让模型拆分：小模型拆句容易漏切或重组，规则法更稳定，
    且把宝贵的模型调用全部留给"判定"这一步。
    """
    if not text:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text)
    claims = []
    for p in parts:
        p = p.strip(" \t-•*·")
        if len(p) < 6:
            continue
        claims.append(p)
        if len(claims) >= max_claims:
            break
    return claims


# ══════════════════════════════════════════════════════════════════
# 裁判
# ══════════════════════════════════════════════════════════════════
_ENTAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["supported", "not_supported", "unknown"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict"],
}

_COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "unknown"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict"],
}

_REL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["relevant", "irrelevant", "unknown"]},
    },
    "required": ["verdict"],
}

_ENTAIL_SYSTEM = (
    "你是一个严格的文本蕴含判定器。"
    "只判断【证据】是否支持【陈述】，不要使用你自己的知识，不要脑补。"
    "证据中明确写出或可直接推出 → supported；"
    "证据中没有提到、或与证据冲突 → not_supported；"
    "证据是空白的 → unknown。"
    "必须只输出 JSON，形如 {\"verdict\":\"supported\"}。"
)

_ENTAIL_PROMPT = """【证据】
{context}

【陈述】
{claim}

请判断：上面的证据是否支持该陈述？只输出 JSON，不要解释。"""


class Judge:
    """LLM-as-Judge。

    所有判定都返回结构化结果，并带 `unknown` 通道：
    模型给不出有效答案时记 unknown，**绝不用默认分蒙混**——
    这正是原实现最大的问题。
    """

    def __init__(
        self,
        client=None,
        cache: Optional[JudgeCache] = None,
        model: Optional[str] = None,
        samples: int = 1,
    ) -> None:
        self.client = client or make_client()
        self.cache = cache or JudgeCache()
        self.model = model or settings.effective_judge_model
        self.samples = max(1, int(samples))
        self.stats = {"calls": 0, "cache_hits": 0, "parse_failures": 0, "unknowns": 0}

    # -- 底层：多次采样取多数 --
    def _vote(self, prompt: str, schema: dict, field: str, system: str = "") -> tuple[Any, float]:
        ck = JudgeCache.key("vote2", self.client.provider, self.model, self.samples, prompt)
        cached = self.cache.get(ck)
        if cached is not None:
            self.stats["cache_hits"] += 1
            return cached.get("value"), cached.get("agreement", 1.0)

        votes: list[Any] = []
        for _ in range(self.samples):
            self.stats["calls"] += 1
            try:
                obj = self.client.chat_json(
                    prompt,
                    schema,
                    model=self.model,
                    system=system,
                    temperature=settings.judge_temperature,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("裁判调用失败：%s: %s", type(exc).__name__, exc)
                obj = None
            if not obj or field not in obj:
                self.stats["parse_failures"] += 1
                continue
            votes.append(obj[field])

        if not votes:
            self.stats["unknowns"] += 1
            # **不缓存失败结果**。
            # 踩过的坑：曾把 value=None 也写进缓存，导致后续重跑直接命中
            # "失败的缓存"而立刻返回 unknown —— 即使端点/模型已经修好，
            # 也永远得不到判定，且表面看是"缓存命中"（很快），极难察觉。
            # 实测：改成 DeepSeek 后 judge-test 仍全 unknown、calls=0，就是它。
            return None, 0.0

        counts: dict[Any, int] = {}
        for v in votes:
            counts[v] = counts.get(v, 0) + 1
        best = max(counts, key=lambda k: counts[k])
        agreement = counts[best] / len(votes)
        self.cache.put(ck, {"value": best, "agreement": agreement})
        return best, agreement

    # -- 蕴含判定 --
    def entailment(self, context: str, claim: str) -> tuple[str, float]:
        if not context.strip():
            return "unknown", 1.0
        prompt = _ENTAIL_PROMPT.format(context=context[:4000], claim=claim[:500])
        verdict, agreement = self._vote(prompt, _ENTAIL_SCHEMA, "verdict", _ENTAIL_SYSTEM)
        if verdict not in ("supported", "not_supported", "unknown"):
            return "unknown", agreement
        return verdict, agreement

    # -- 任务完成度判定 --
    def task_completion(
        self, question: str, answer: str, criteria: str, context: str = ""
    ) -> tuple[str, float]:
        system = (
            "你是评测员。严格按【验收标准】判断【回答】是否达标，"
            "不要因为回答语气好就给通过。"
            "达标 → pass；未达标或答非所问 → fail；无法判断 → unknown。"
            "必须只输出 JSON，形如 {\"verdict\":\"pass\"}。"
        )
        prompt = f"""【用户问题】
{question}

【待评回答】
{answer[:1500]}

【参考资料】（可能为空）
{context[:1500] if context else "（无）"}

【验收标准】
{criteria}

只输出 JSON，不要解释。"""
        verdict, agreement = self._vote(prompt, _COMPLETE_SCHEMA, "verdict", system)
        if verdict not in ("pass", "fail", "unknown"):
            return "unknown", agreement
        return verdict, agreement

    # -- 片段相关性 --
    def relevance(self, question: str, chunk: str) -> tuple[str, float]:
        system = (
            "你判断一段检索结果是否与用户问题相关（有助于回答该问题）。"
            "相关 → relevant；无关 → irrelevant；无法判断 → unknown。"
            "必须只输出 JSON，形如 {\"verdict\":\"relevant\"}。"
        )
        prompt = f"""【用户问题】
{question}

【检索到的片段】
{chunk[:1200]}

只输出 JSON，不要解释。"""
        verdict, agreement = self._vote(prompt, _REL_SCHEMA, "verdict", system)
        if verdict not in ("relevant", "irrelevant", "unknown"):
            return "unknown", agreement
        return verdict, agreement

    def flush(self) -> None:
        self.cache.flush()


# ══════════════════════════════════════════════════════════════════
# 自检
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    """裁判连通性自检。评测前先跑这个，避免跑到一半才发现 key/模型不对。"""
    ok, desc = settings.judge_ready()
    print(f"裁判配置 : {desc}")
    if not ok:
        print("  ✗ 配置不完整")
        return 1

    try:
        client = make_client()
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 客户端构造失败：{exc}")
        return 1

    print(f"  · 端点可用性 ...", end=" ", flush=True)
    print("OK" if client.is_available() else "不可达")

    judge = Judge(client=client, samples=1)
    print("  · 蕴含判定 ...", end=" ", flush=True)
    verdict, agreement = judge.entailment(
        "设备通过 ESP8266 连接 WiFi 上传数据。",
        "设备具备联网能力。",
    )
    print(f"{verdict} (agree={agreement})")

    print("  · 任务完成度 ...", end=" ", flush=True)
    verdict2, _ = judge.task_completion(
        "传感器掉线怎么办",
        "先检查供电，再确认 ESP8266 热点是否正常，最后查看串口日志。",
        "必须给出至少两个可操作排查步骤。",
    )
    print(verdict2)

    judge.flush()
    print(f"  · 缓存条目 : {len(judge.cache)}")
    print(f"  · 调用统计 : {judge.stats}")
    good = verdict in ("supported", "not_supported") and verdict2 in ("pass", "fail")
    print("自检结果 :", "通过" if good else "异常（裁判未给出有效判定）")
    return 0 if good else 1


__all__ = [
    "OllamaClient", "OpenAICompatClient", "make_client",
    "Judge", "JudgeCache", "extract_json", "split_claims", "self_test",
]
