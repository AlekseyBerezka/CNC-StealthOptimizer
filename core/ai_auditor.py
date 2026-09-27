import json
import re
import urllib.request


SYSTEM_PROMPT = (
    "Ты — ИИ-аудитор управляющих программ для токарных станков Fanuc.\n"
    "Задача — ДАТЬ РЕКОМЕНДАЦИИ технологу, а не менять код.\n"
    "\n"
    "ПРАВИЛА:\n"
    "1. Упоминай ТОЛЬКО те G-коды и T-коды, которые РЕАЛЬНО есть в тексте УП.\n"
    "2. Не выдумывай операции, которых нет.\n"
    "3. Повторный вызов инструмента — часто НОРМА, не предлагай удалять.\n"
    "4. Не давай общих рекомендаций — указывай конкретную строку.\n"
    "5. Если дефектов нет — напиши 'критических дефектов не выявлено'.\n"
    "\n"
    "Ответ строго JSON:\n"
    "{\n"
    '  "analysis": "краткий разбор (до 300 символов)",\n'
    '  "recommendations": ["конкретные рекомендации"],\n'
    '  "estimated_savings_seconds": 0,\n'
    '  "requires_human_review": true\n'
    "}"
)


class AIAuditor:
    def __init__(self,
                 model_name: str = "qwen2.5-coder:7b",
                 ollama_url: str = "http://localhost:11434/api/generate",
                 timeout: int = 300,
                 max_lines: int = 150):
        """
        timeout:    секунд на ответ. Для 7B на CPU — ставьте 300.
        max_lines:  если УП длиннее — отправляем только «голову» и «хвост»,
                    чтобы не перегружать контекст модели.
        """
        self.model_name = model_name
        self.ollama_url = ollama_url
        self.timeout = timeout
        self.max_lines = max_lines

    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_json(raw: str):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        cleaned = re.sub(r'```(?:json)?', '', raw).replace('```', '').strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None

    def _prepare_text(self, source_gcode):
        """
        Если УП длинная — сокращаем: берём первые 2/3 и последние 1/3
        до max_lines строк. Это сохраняет структуру (начало + финал + хвост),
        но уменьшает контекст.
        """
        if len(source_gcode) <= self.max_lines:
            return "\n".join(source_gcode)

        head_n = int(self.max_lines * 0.7)
        tail_n = self.max_lines - head_n
        head = source_gcode[:head_n]
        tail = source_gcode[-tail_n:]
        return ("\n".join(head) +
                "\n... (пропущено строк: "
                f"{len(source_gcode) - self.max_lines}) ...\n" +
                "\n".join(tail))

    # ------------------------------------------------------------------ #

    def audit(self, source_gcode):
        text = self._prepare_text(source_gcode)
        full_prompt = (f"System: {SYSTEM_PROMPT}\n\nUser: "
                       f"Проанализируй УП:\n{text}")
        payload = {
            "model": self.model_name,
            "prompt": full_prompt,
            "stream": False,
            "format": "json",
        }
        req = urllib.request.Request(
            self.ollama_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                body = json.loads(response.read().decode('utf-8'))
        except Exception as e:
            return {
                "error": f"{type(e).__name__}: {e}",
                "analysis": "",
                "recommendations": [],
                "estimated_savings_seconds": 0,
                "requires_human_review": True,
                "hint": (
                    f"Увеличьте timeout в AIAuditor или используйте модель "
                    f"быстрее. Текущий timeout={self.timeout} сек."
                ),
            }

        result = self._parse_json(body.get('response', ''))
        if not result:
            result = {
                "analysis": "",
                "recommendations": [],
                "estimated_savings_seconds": 0,
                "requires_human_review": True,
            }
        return result
