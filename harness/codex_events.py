from __future__ import annotations
import json
from dataclasses import dataclass, field

@dataclass
class CodexEvents:
    events: list[dict] = field(default_factory=list)
    session_id: str|None = None
    codex_turns: int|None = None
    agent_cycles: int|None = None
    underlying_model_requests: int|None = None
    response_boundary_events: list[dict] = field(default_factory=list)
    usage_snapshots: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    tool_events: list[dict] = field(default_factory=list)
    final_text: str = ""

def parse_jsonl(raw: str) -> CodexEvents:
    out=CodexEvents(); turns=0; text_chunks=[]

    def extract_text(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts=[extract_text(item) for item in value]
            return "".join(part for part in parts if part)
        if isinstance(value, dict):
            for key in ("text", "output_text", "content"):
                text=extract_text(value.get(key))
                if text:
                    return text
        return ""

    for line in raw.splitlines():
        try: e=json.loads(line)
        except json.JSONDecodeError: continue
        if not isinstance(e,dict): continue
        out.events.append(e); typ=e.get("type","")
        if typ in {"thread.started","thread.created"}:
            out.session_id=e.get("thread_id") or e.get("thread",{}).get("id") or out.session_id
        if typ == "turn.started": turns += 1
        if typ in {"response.started","response.completed"}:
            out.response_boundary_events.append(e)
        if typ in {"item.started","item.completed"}:
            item=e.get("item",{})
            if isinstance(item,dict) and item.get("type") in {"command_execution","mcp_tool_call","web_search_call","tool_call"}: out.tool_events.append(e)
        u=e.get("usage")
        if isinstance(u,dict):
            out.usage=u
            out.usage_snapshots.append(u)
        if typ in {"item.completed","message.completed","response.output_text.done"}:
            item=e.get("item",e)
            if isinstance(item,dict):
                text=extract_text(item)
                if text: out.final_text=text
        if typ in {"response.output_text.delta", "output_text.delta"}:
            text=extract_text(e)
            if text: text_chunks.append(text)
        if isinstance(e.get("text"),str): out.final_text=e["text"]
    if not out.final_text and text_chunks:
        out.final_text="".join(text_chunks)
    out.codex_turns=turns if turns else None
    # Backward-compatible alias; callers must label this as Codex turns, not
    # underlying model requests or a scientific neural-budget unit.
    out.agent_cycles=out.codex_turns
    return out
