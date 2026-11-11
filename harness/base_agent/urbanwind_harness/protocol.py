import json

from .api import action_from_text


def function(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


TOOLS = [
    function("shell", "Inspect files, edit configuration and run general Linux tools.",
        {"command": {"type": "string"}, "timeout_s": {"type": "number"}}, ["command"]),
    function("run", "Execute a real foreground stage and bind its output files to its actual inputs.",
        {"stage": {"type": "string", "enum": ["mesh", "solve", "postprocess", "deliver"]},
         "case": {"type": "string"}, "command": {"type": "string"},
         "outputs": {"type": "array", "items": {"type": "string"}},
         "inputs": {"type": "array", "items": {"type": "string"}},
         "timeout_s": {"type": "number"}}, ["stage", "case", "command", "outputs"]),
    function("inspect", "Inspect the public workspace using this condition's enabled checks.", {}, []),
    function("recover", "Recommend recomputation for one actual case from enabled execution-bound evidence.",
        {"case": {"type": "string"}}, ["case"]),
    function("finish", "Declare saved task artifacts ready for independent final grading.",
        {"summary": {"type": "string"}}, ["summary"]),
]


def action_from_response(response):
    message = response.get("message", {})
    calls = message.get("tool_calls") or []
    if not calls:
        return action_from_text(response["content"])
    if len(calls) != 1:
        raise ValueError("exactly_one_tool_call_per_turn_required")
    return action_from_call(calls[0])


def action_from_call(call):
    if call.get("type") != "function" or call["function"]["name"] not in {t["function"]["name"] for t in TOOLS}:
        raise ValueError("unsupported_tool")
    arguments = json.loads(call["function"]["arguments"])
    if not isinstance(arguments, dict) or "action" in arguments:
        raise ValueError("invalid_tool_arguments")
    return action_from_text(json.dumps({"action": call["function"]["name"], **arguments}))


def feedback_messages(response, observation):
    message = response.get("message") or {"role": "assistant", "content": response["content"]}
    content = json.dumps(observation, ensure_ascii=False)
    calls = message.get("tool_calls") or []
    if calls:
        return [message] + [{"role": "tool", "tool_call_id": call["id"], "content": content} for call in calls]
    return [message, {"role": "user", "content": content}]
