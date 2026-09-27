from core.time_estimator import FanucTimeEstimator
from core.safety_validator import FanucSafetyValidator
from core.compiler import StructuralCompiler
from core.ai_auditor import AIAuditor


class AuditPipeline:
    """
    Оркестратор: оценка времени → аудит ИИ → структурная правка →
    валидация → финальная оценка.

    Возвращает dict — его используют и CLI, и Streamlit.
    """

    def __init__(self,
                 model_name: str = "qwen2.5-coder:7b",
                 workpiece_diameter: float = 60.0,
                 workpiece_length: float = 150.0,
                 safe_x: float = None,
                 safe_z: float = None,
                 tool_change_time: float = 3.0,
                 rapid_speed_mpm: float = 15.0,
                 ai_timeout: int = 300,
                 ai_max_lines: int = 150,
                 skip_first_retract: bool = False):
        self.estimator = FanucTimeEstimator(
            tool_change_time=tool_change_time,
            rapid_speed_mpm=rapid_speed_mpm,
        )
        self.validator = FanucSafetyValidator(
            workpiece_diameter=workpiece_diameter,
            workpiece_length=workpiece_length,
        )
        self.compiler = StructuralCompiler(
            safe_x=safe_x,
            safe_z=safe_z,
            workpiece_diameter=workpiece_diameter,
            workpiece_length=workpiece_length,
            skip_first_retract=skip_first_retract,
        )
        self.auditor = AIAuditor(
            model_name=model_name,
            timeout=ai_timeout,
            max_lines=ai_max_lines,
        )

    def run(self, source_gcode, skip_ai: bool = False):
        result = {"source_gcode": list(source_gcode)}

        # 1. Базовое время
        result["baseline"] = self.estimator.estimate_cycle_time(source_gcode)

        # 1a. Диагностика T-кодов
        result["tools_debug"] = self.estimator.debug_tool_codes(source_gcode)

        # 2. Аудит ИИ
        if skip_ai:
            result["ai"] = {
                "analysis": "Аудит ИИ пропущен по запросу.",
                "recommendations": [],
                "estimated_savings_seconds": 0,
                "requires_human_review": True,
                "skipped": True,
            }
        else:
            result["ai"] = self.auditor.audit(source_gcode)

        # 3. Структурный компилятор
        compiled, compile_logs = self.compiler.compile(source_gcode)
        result["compile_logs"] = compile_logs
        result["compiled_gcode"] = compiled

        # 4. Валидация безопасности
        is_safe, safety_logs, fixed = \
            self.validator.validate_and_fix_program(compiled)
        result["is_safe"] = is_safe
        result["safety_logs"] = safety_logs
        result["final_gcode"] = fixed

        # 5. Финальная оценка
        result["final"] = self.estimator.estimate_cycle_time(fixed)

        # 5a. Проверка целостности
        integrity = []
        tools_found = set(result["baseline"]["tool_sequence"])
        if len(tools_found) < 3 and len(result["source_gcode"]) > 50:
            integrity.append(
                f"Найдено всего {len(tools_found)} инструментов при "
                f"{len(result['source_gcode'])} строках УП. "
                f"Проверьте целостность файла."
            )
        result["integrity_warnings"] = integrity

        # 6. Разложение дельты по фазам
        b, f = result["baseline"], result["final"]
        result["delta"] = {
            "total_seconds": round(
                f["total_time_seconds"] - b["total_time_seconds"], 2),
            "cutting_seconds": round(
                f["cutting_time_seconds"] - b["cutting_time_seconds"], 2),
            "rapid_seconds": round(
                f["rapid_time_seconds"] - b["rapid_time_seconds"], 2),
            "drilling_seconds": round(
                f["drilling_time_seconds"] - b["drilling_time_seconds"], 2),
            "tool_change_seconds": round(
                f["tool_change_time_seconds"] - b["tool_change_time_seconds"], 2),
        }

        return result
