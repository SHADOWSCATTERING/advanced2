"""
ai_service.py
---------------
The AI / Innovation layer of the system.

Design principle (see fatigue_engine.py docstring): the AI NEVER computes
risk scores or rest-hour math itself. It is handed the deterministic
output of FatigueEngine and asked only to:
  1. Explain WHY a schedule is risky, in plain English a non-technical
     shift manager can understand.
  2. Summarize/rank the most urgent issues.
  3. Phrase rule-based "safer alternative" suggestions in a clear way.

This keeps the system auditable: every number the AI talks about can be
traced back to a specific rule in fatigue_rules.csv, and the AI cannot
invent a violation that the engine didn't actually find.

If ANTHROPIC_API_KEY is not set (or the API call fails for any reason -
network, rate limit, etc.), the service transparently falls back to a
template-based explanation generator so the product still works end to
end for grading/demo purposes. The response always includes a `source`
field ("ai" or "fallback_template") so callers/UI can be honest about
which path produced the text - this doubles as the project's required
"limitations / responsible-use" disclosure.
"""
import os
import json
import requests

GEMINI_MODEL = "gemini-1.5-flash-latest"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
REQUEST_TIMEOUT_SECONDS = 20

SYSTEM_PROMPT = (
    "You are a workforce safety assistant embedded in a shift-planning tool. "
    "You will be given a JSON object containing a fatigue-risk analysis that was "
    "already computed by deterministic rule-based code (rest hours, consecutive "
    "days, weekly hours, shift overlaps). Do NOT invent, recompute, or contradict "
    "any numbers in that JSON - treat them as ground truth. Your job is only to:\n"
    "1) Explain, in 2-4 plain-English sentences a shift manager (non-technical) "
    "would understand, why this employee's schedule is risky right now.\n"
    "2) List the single most urgent issue first.\n"
    "3) If safer_alternatives are provided, briefly recommend one and say why.\n"
    "Keep the tone calm and factual, never alarmist. If the data shows no "
    "violations, say clearly that the schedule looks safe. Respond with ONLY a "
    "JSON object (no markdown fences, no preamble) with this exact shape: "
    '{"explanation": "...", "most_urgent_issue": "...", "recommendation": "..."}'
)


def _call_gemini(analysis: dict, safer_alternatives: list = None) -> dict | None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"System Instructions: {SYSTEM_PROMPT}\n\nContext Data:\n" + json.dumps({
                    "fatigue_analysis": analysis,
                    "safer_alternatives": safer_alternatives or [],
                }, default=str)}]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2
        }
    }
    headers = {
        "content-type": "application/json",
    }

    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                  timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
            raw_text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
            raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw_text)
            parsed["source"] = "ai"
            return parsed
        except Exception as exc:
            print(f"[ai_service] Gemini API call failed (attempt {attempt+1}): {exc}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return None


def _fallback_explanation(analysis: dict, safer_alternatives: list = None) -> dict:
    """Deterministic, template-based explanation used when no API key is
    configured or the AI call fails. Ensures the feature always works."""
    violations = analysis.get("violations", [])
    name = analysis.get("employee_name", "This employee")
    risk_level = analysis.get("risk_level", "Low")

    if not violations:
        return {
            "explanation": f"{name}'s current schedule does not breach any fatigue-risk rules. "
                            f"Rest periods, consecutive working days, and weekly hours are all within safe limits.",
            "most_urgent_issue": "None detected.",
            "recommendation": "No changes needed. Continue monitoring as new shifts are added.",
            "source": "fallback_template",
        }

    # Sort by severity so the most urgent issue is genuinely most urgent
    severity_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    sorted_v = sorted(violations, key=lambda v: severity_rank.get(v.get("severity", "Low"), 3))
    top = sorted_v[0]

    other_count = len(violations) - 1
    other_clause = f" There {'is' if other_count == 1 else 'are'} also {other_count} additional issue{'s' if other_count != 1 else ''} flagged." if other_count > 0 else ""

    explanation = (
        f"{name}'s schedule is currently rated {risk_level} risk, mainly because of: "
        f"{top['detail']}{other_clause} Schedules like this increase the chance of "
        f"reduced alertness, errors, and burnout, and may also breach workplace safety guidelines."
    )

    if safer_alternatives:
        best = safer_alternatives[0]
        recommendation = (
            f"Consider this adjustment: {best['option']} (new shift: {best['shift_date']} "
            f"{best['start_time']}-{best['end_time']}), which is projected to bring the risk "
            f"level down to {best['projected_risk_level']}."
        )
    else:
        recommendation = (
            "Review the flagged shift(s) with the employee and adjust timing, add a rest day, "
            "or reassign part of the workload to reduce risk."
        )

    return {
        "explanation": explanation,
        "most_urgent_issue": f"[{top['rule_id']}] {top['rule_name']}: {top['detail']}",
        "recommendation": recommendation,
        "source": "fallback_template",
    }


def explain_fatigue_risk(analysis: dict, safer_alternatives: list = None) -> dict:
    """Main entry point used by the API layer. Returns a dict with
    explanation / most_urgent_issue / recommendation / source."""
    ai_result = _call_gemini(analysis, safer_alternatives)
    if ai_result is not None:
        return ai_result
    return _fallback_explanation(analysis, safer_alternatives)


def explain_conflict(conflict_detail: dict) -> dict:
    """Smaller, focused explanation for a single hard conflict (e.g. a
    double-booking) surfaced at shift-creation time."""
    analysis_stub = {
        "employee_name": conflict_detail.get("employee_name", "This employee"),
        "risk_level": "Critical",
        "violations": [conflict_detail],
    }
    return explain_fatigue_risk(analysis_stub)


def is_ai_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def chat_with_ai(analysis: dict, safer_alternatives: list, history: list, new_message: str) -> str:
    """Multi-turn conversation about the fatigue analysis."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "I'm sorry, the AI chat feature requires an active Gemini API key."

    system_prompt = (
        "You are a helpful workforce safety assistant embedded in a shift-planning tool. "
        "You help shift managers understand fatigue risks and suggest compliant schedules. "
        "Keep answers concise and professional. Do NOT invent or contradict any data from the provided JSON."
    )
    
    context_str = json.dumps({
        "fatigue_analysis": analysis,
        "safer_alternatives": safer_alternatives or [],
    }, default=str)
    
    first_msg_text = f"System Instructions: {system_prompt}\n\nContext Data:\n{context_str}\n\n"
    
    contents = []
    if not history:
        contents.append({"role": "user", "parts": [{"text": first_msg_text + f"User: {new_message}"}]})
    else:
        first_user_msg = history[0]
        contents.append({
            "role": "user",
            "parts": [{"text": first_msg_text + f"User: {first_user_msg['content']}"}]
        })
        for msg in history[1:]:
            role = "model" if msg["role"] == "assistant" else "user"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
        contents.append({"role": "user", "parts": [{"text": new_message}]})

    payload = {
        "contents": contents,
        "generationConfig": {
            "temperature": 0.5
        }
    }
    
    headers = {
        "content-type": "application/json",
    }
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    
    import time
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
            return data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
        except requests.exceptions.HTTPError as he:
            print(f"[ai_service] HTTP Error: {he.response.text}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return f"API Error ({he.response.status_code}): {he.response.text}"
        except Exception as exc:
            print(f"[ai_service] Chat API call failed (attempt {attempt+1}). Reason: {exc}")
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return f"Sorry, I encountered an error communicating with the AI service. Reason: {exc}"
