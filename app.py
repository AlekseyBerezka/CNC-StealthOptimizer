import sys

from core.pipeline import AuditPipeline


def print_header(title):
    print("\n" + "=" * 70)
    print(f" 🛠  {title.upper()} ")
    print("=" * 70)


def print_status(category, message, status="INFO"):
    icons = {"INFO": "🔹", "SUCCESS": "🟢", "WARNING": "⚠️",
             "ERROR": "❌", "ALERT": "🔥"}
    print(f"{icons.get(status, '🔹')} [{category:<12}] {message}")


SAMPLE = [
    "O1000",
    "G50 S2500",
    "G97 S1500 M03 T0101",
    "G00 X60.0 Z2.0",
    "G01 Z-30.0 F0.25",
    "G00 X80.0 Z50.0",
    "G97 S2000 M03 T0202",
    "G00 X60.0 Z2.0",
    "G01 X40.0 F0.1",
    "G00 X80.0 Z50.0",
    "M30",
]


def main():
    args = sys.argv[1:]
    skip_ai = "--skip-ai" in args
    debug = "--debug" in args
    args = [a for a in args if a not in ("--skip-ai", "--debug")]

    if args:
        try:
            with open(args[0], encoding="utf-8", errors="replace") as fp:
                source_gcode = [ln.rstrip("\n") for ln in fp if ln.strip()]
        except FileNotFoundError:
            print(f"Файл не найден: {args[0]}")
            sys.exit(1)
    else:
        source_gcode = SAMPLE

    # timeout 300 сек — на 7B-модель на CPU для 222-строчной УП.
    pipeline = AuditPipeline(model_name="qwen2.5-coder:7b",
                             ai_timeout=300,
                             ai_max_lines=150)
    r = pipeline.run(source_gcode, skip_ai=skip_ai)

    print_header("CNC-StealthOptimizer v3.3 [АУДИТОР УП]")

    if debug:
        print_header("Диагностика T-кодов")
        for idx, tool, raw, clean in r["tools_debug"]:
            print(f"  строка {idx:>3}: T{tool}   ←  {raw}")

    b = r["baseline"]
    print_status(
        "БАЗОВОЕ ВРЕМЯ",
        f"{b['total_time_seconds']} сек. "
        f"(резание {b['cutting_time_seconds']} + "
        f"rapid {b['rapid_time_seconds']} + "
        f"сверление {b['drilling_time_seconds']} + "
        f"смены {b['tool_change_time_seconds']})",
        "INFO",
    )
    print_status(
        "ПОСЛЕДОВАТЕЛЬНОСТЬ T",
        f"{b['tool_sequence']} (это норма, если так задумано технологом)",
        "INFO",
    )
    for w in b.get("warnings", []):
        print_status("ПРЕДУПРЕЖДЕНИЕ", w, "WARNING")
    for w in r.get("integrity_warnings", []):
        print_status("ЦЕЛОСТНОСТЬ", w, "WARNING")

    ai = r["ai"]
    if not ai.get("skipped"):
        print_status("ИИ-АУДИТОР", "Анализируем...", "INFO")
    if ai.get("error"):
        print_status("ИИ", f"Ошибка: {ai['error']}", "ERROR")
        if ai.get("hint"):
            print_status("ИИ", ai["hint"], "INFO")
    elif ai.get("skipped"):
        print_status("ИИ", "Аудит ИИ пропущен.", "INFO")
    else:
        print_status("ИИ АНАЛИЗ", (ai.get("analysis") or "—")[:250], "SUCCESS")
        recs = ai.get("recommendations", [])
        if recs:
            for rec in recs:
                print_status("РЕКОМЕНДАЦИЯ", rec, "INFO")
        else:
            print_status(
                "РЕКОМЕНДАЦИЯ",
                "Автоматических рекомендаций нет — нужен ручной аудит.",
                "INFO",
            )

    print_header("Структурный компилятор")
    for m in r["compile_logs"]:
        print_status("СТРУКТУРА", m, "INFO")

    print_header("Валидация безопасности")
    for log in r["safety_logs"]:
        status = ("SUCCESS" if "[УСПЕХ" in log else
                  "ERROR" if "КРИТИЧЕСК" in log else
                  "WARNING" if "ПРЕДУПРЕЖ" in log or "ПЕРЕХВАТ" in log
                  else "INFO")
        print_status("БЕЗОПАСНОСТЬ", log, status)

    if not r["is_safe"]:
        print_status("ФИНАЛ", "Критическая ошибка. Код заблокирован.", "ERROR")
        return

    print_header("Экономический отчёт")
    f = r["final"]
    print_status(
        "ВРЕМЯ ПОСЛЕ АУДИТА",
        f"{f['total_time_seconds']} сек. (было {b['total_time_seconds']})",
        "SUCCESS",
    )
    d = r["delta"]
    print_status(
        "РАЗЛОЖЕНИЕ ДЕЛЬТЫ",
        f"резание {d['cutting_seconds']:+} | "
        f"rapid {d['rapid_seconds']:+} | "
        f"сверление {d['drilling_seconds']:+} | "
        f"смены {d['tool_change_seconds']:+}",
        "INFO",
    )
    if d["rapid_seconds"] > 0.01 and abs(d["cutting_seconds"]) < 0.01:
        print_status(
            "ПОЯСНЕНИЕ",
            f"+{d['rapid_seconds']} сек. — добавленные явные кадры отхода "
            f"в безопасную позицию. В исходной УП этих кадров не было "
            f"в явном виде.",
            "INFO",
        )

    potential = ai.get("estimated_savings_seconds", 0)
    if potential:
        hours = (potential * 1_000_000) / 3600.0
        print_status(
            "ПОТЕНЦИАЛ ИИ",
            f"~{potential} сек./дет. ({hours:.0f} маш/час на 1 млн). "
            f"Требует подтверждения технолога.",
            "INFO",
        )

    out_path = "optimized_fanuc.nc"
    with open(out_path, "w") as fp:
        fp.write("\n".join(r["final_gcode"]))
    print_status("ФИНАЛ", f"Безопасная УП записана в {out_path}", "SUCCESS")


if __name__ == "__main__":
    main()
