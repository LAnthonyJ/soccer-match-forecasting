import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


RISK_DISCLAIMER = "本内容仅为数据分析，不构成投注建议。"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeepSeekAnalysisService:
    def __init__(
        self,
        cache_dir: Path | str,
        api_key: str,
        base_url: str,
        model: str,
        enabled: bool,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = enabled

    def _snapshot_key(self, payload: dict[str, Any]) -> str:
        if payload.get("prematch_snapshot_id"):
            return str(payload["prematch_snapshot_id"])
        generated_at = str(payload.get("generated_at", "unknown")).replace(":", "").replace("+", "_")
        return f"{payload.get('match_id', 'unknown')}_{generated_at}"

    def _cache_path(self, payload: dict[str, Any]) -> Path:
        return self.cache_dir / str(payload.get("match_id", "unknown")) / f"{self._snapshot_key(payload)}.json"

    def _build_prompt(self, payload: dict[str, Any]) -> str:
        probs = payload.get("probabilities", {})
        top_scores = payload.get("top_scorelines", [])[:5]
        return (
            "请根据以下世界杯赛前预测数据，输出一段简洁、克制的中文分析。"
            "内容必须覆盖胜平负概率、最可能比分、总进球倾向、置信度和风险提示，不要编造数据。\n"
            f"球队一: {payload.get('home_team')}\n"
            f"球队二: {payload.get('away_team')}\n"
            f"胜平负概率: {json.dumps(probs, ensure_ascii=False)}\n"
            f"最可能比分: {json.dumps(top_scores, ensure_ascii=False)}\n"
            f"Over2.5: {payload.get('over25')}\n"
            f"BTTS: {payload.get('btts')}\n"
            f"置信度: {payload.get('confidence')}\n"
            f"阵容状态: {payload.get('lineup_data_status')}\n"
            f"数据更新时间: {payload.get('data_updated_at')}\n"
            f"最后一行必须原样输出：{RISK_DISCLAIMER}"
        )

    def _fallback_summary(self, payload: dict[str, Any]) -> str:
        probs = payload.get("probabilities", {})
        top = payload.get("top_scorelines", [])
        first = top[0] if top else [None, None, 0]
        analysis = (
            f"{payload.get('home_team')} 对阵 {payload.get('away_team')} 的模型结果显示："
            f"主胜 {round((probs.get('home') or 0) * 100, 1)}%，"
            f"平局 {round((probs.get('draw') or 0) * 100, 1)}%，"
            f"客胜 {round((probs.get('away') or 0) * 100, 1)}%。"
            f"当前最可能比分是 {first[0]}-{first[1]}，"
            f"模型置信度为 {round((payload.get('confidence') or 0) * 100, 1)}%。"
        )
        return self._ensure_disclaimer(analysis)

    def _call_api(self, prompt: str) -> str:
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是足球赛前分析助手。输出中文，直接、具体、克制。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.5,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return self._ensure_disclaimer(payload["choices"][0]["message"]["content"].strip())

    def _ensure_disclaimer(self, analysis: str) -> str:
        analysis = (analysis or "").strip()
        if analysis.endswith(RISK_DISCLAIMER):
            return analysis
        if not analysis:
            return RISK_DISCLAIMER
        return f"{analysis}\n\n{RISK_DISCLAIMER}"

    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        cache_path = self._cache_path(payload)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        if self.enabled and self.api_key:
            try:
                analysis = self._ensure_disclaimer(self._call_api(self._build_prompt(payload)))
            except Exception:
                analysis = self._fallback_summary(payload)
        else:
            analysis = self._fallback_summary(payload)

        result = {
            "fixture_id": payload.get("match_id"),
            "analysis": analysis,
            "language": "zh-CN",
            "model": self.model,
            "generated_at": _now_iso(),
            "data_sources": ["sportmonks", "prediction_snapshot"],
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
