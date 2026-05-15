#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meta-mcp — 元 MCP Server 骨架生成器（纯 stdio 实现）

协议：JSON-RPC 2.0 over stdin/stdout
依赖：仅 pydantic（参数校验），无其他第三方框架依赖。

生成逻辑下沉到同目录 server_gen.py（MCP 协议与生成逻辑完全解耦）。
"""
from __future__ import annotations

import sys, json, os, logging
from pathlib import Path
from pydantic import BaseModel, Field, field_validator

# ── 日志（走 stderr，MCP 协议走 stdout）────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("meta-mcp")

# ══════════════════════════════════════════════════════════════════════════════
# Prompt 模板（从同目录文件加载，避免 Python 字符串引号地狱）
# ══════════════════════════════════════════════════════════════════════════════

def _load_prompt() -> str:
    script_dir = Path(__file__).parent.resolve()
    prompt_path = script_dir / "prompt_guide.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    return "(prompt_guide.md not found — please create it beside server.py)"

PROMPT_DEVELOPER_GUIDE = _load_prompt()

# ══════════════════════════════════════════════════════════════════════════════
# Pydantic 输入模型
# ══════════════════════════════════════════════════════════════════════════════

class ScaffoldInput(BaseModel):
    project_name: str      = Field(..., min_length=1, max_length=64)
    tool_type: str        = Field(...)
    schema_definition: str = Field(..., min_length=4)

    @field_validator("tool_type")
    @classmethod
    def tool_type_enum(cls, v: str) -> str:
        if v not in {"api", "cli", "db", "file", "generic"}:
            raise ValueError("tool_type 必须是 api|cli|db|file|generic，当前：" + repr(v))
        return v

    @field_validator("project_name")
    @classmethod
    def safe_name(cls, v: str) -> str:
        illegal = set("..~/$&|;\\ \n\t")
        bad = [c for c in illegal if c in v]
        if bad:
            raise ValueError("project_name 禁止包含：" + repr("".join(bad)) + "，当前：" + repr(v))
        return v

# ══════════════════════════════════════════════════════════════════════════════
# 生成逻辑（委托给 server_gen.py）
# ══════════════════════════════════════════════════════════════════════════════

# 延迟导入（避免 server_gen.py 的 __main__ 被执行）
_server_gen = None

def _get_server_gen():
    global _server_gen
    if _server_gen is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("server_gen", Path(__file__).parent / "server_gen.py")
        _server_gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_server_gen)
    return _server_gen

def _parse_schema(schema_str: str) -> dict:
    """解析 schema_definition：优先 JSON，失败则按自然语言行解析。"""
    try:
        return json.loads(schema_str)
    except Exception:
        result = {}
        for line in schema_str.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ": " in line:
                k, desc = line.split(": ", 1)
                result[k.strip()] = {"type": "string", "description": desc.strip()}
            elif line.startswith("- "):
                parts = line[2:].split(" - ", 1)
                if len(parts) == 2:
                    result[parts[0].strip()] = {"type": "string", "description": parts[1].strip()}
        return result

def _build_scaffold(project_name: str, tool_type: str, schema_definition: str) -> dict:
    """对外暴露的 build_scaffold（统一入口）。"""
    sg = _get_server_gen()
    # server_gen.build_scaffold 内部会 json.loads(schema_definition)
    return sg.build_scaffold(project_name, tool_type, schema_definition)

# ══════════════════════════════════════════════════════════════════════════════
# 工具实现
# ══════════════════════════════════════════════════════════════════════════════

def mcp_developer_guide_impl() -> dict:
    logger.info("mcp_developer_guide 被调用")
    return {
        "ok": True,
        "prompt": PROMPT_DEVELOPER_GUIDE,
        "instructions": [
            "1. 向用户抛出固化基础问题 + 动态灵活问题",
            "2. 输出参数契约确认书，等待用户回复「通过」",
            "3. 用户确认后，调用 scaffold_new_mcp 生成代码",
        ],
    }


def scaffold_new_mcp_impl(
    project_name: str,
    tool_type: str,
    schema_definition: str,
) -> dict:
    logger.info("[scaffold_new_mcp] project=%s type=%s", project_name, tool_type)

    # ── 参数校验 ────────────────────────────────────────
    try:
        validated = ScaffoldInput(
            project_name=project_name,
            tool_type=tool_type,
            schema_definition=schema_definition,
        )
    except Exception as exc:
        logger.warning("参数校验失败: %s", exc)
        return {
            "ok": False, "project_dir": None, "files": None,
            "mount_hint": None,
            "error": {
                "type": type(exc).__name__, "message": str(exc),
                "hint": "检查 project_name 字符（仅 a-zA-Z0-9_-）和 tool_type 枚举值。",
            },
        }

    # ── 确定输出目录 ────────────────────────────────────
    workspace = Path(os.environ.get(
        "META_MCP_WORKSPACE",
        "/Users/frank_bot/.openclaw/workspace",
    ))
    project_dir = (workspace / ("mcp-" + validated.project_name)).resolve()

    if project_dir.exists() and any(project_dir.iterdir()):
        logger.warning("目录已存在且非空: %s", project_dir)
        return {
            "ok": False, "project_dir": str(project_dir), "files": None,
            "mount_hint": None,
            "error": {
                "type": "DirectoryExists",
                "message": "目录已存在: " + str(project_dir),
                "hint": "换一个 project_name，或先清空目录。",
            },
        }

    # ── 生成骨架 ────────────────────────────────────────
    try:
        files = _build_scaffold(
            project_name="mcp-" + validated.project_name,
            tool_type=validated.tool_type,
            schema_definition=validated.schema_definition,
        )
        project_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for fname, content in files.items():
            fpath = project_dir / fname
            fpath.write_text(content, encoding="utf-8")
            written.append(fname)
            logger.info("生成: %s", fpath)

        rel = str(project_dir.relative_to(Path.home()))
        mount_hint = (
            "### 挂载为 OpenClaw MCP Server\n\n"
            "在 `~/.openclaw/config.yml` 的 `plugins.entries.mcp.servers` 下添加：\n\n"
            "```yaml\n"
            "plugins:\n"
            "  entries:\n"
            "    mcp:\n"
            "      servers:\n"
            "        " + validated.project_name + ":\n"
            "          type: local\n"
            "          command: python3\n"
            "          args:\n"
            "            - " + rel + "/server.py\n"
            "```\n\n"
            "重启 Gateway：`openclaw gateway restart`\n\n"
            "验证：\n"
            "```bash\n"
            "cd " + rel + " && python3 server.py\n"
            "```\n"
        )

        logger.info("[scaffold_new_mcp] ✅ 完成 | files=%d", len(written))
        return {
            "ok": True,
            "project_dir": str(project_dir),
            "files": sorted(written),
            "mount_hint": mount_hint,
            "error": None,
        }

    except Exception as exc:
        logger.exception("生成失败: %s", exc)
        return {
            "ok": False, "project_dir": str(project_dir), "files": None,
            "mount_hint": None,
            "error": {
                "type": type(exc).__name__, "message": str(exc),
                "hint": "workspace 无写权限或 schema_definition 格式有误。",
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
# MCP stdio 主循环
# ══════════════════════════════════════════════════════════════════════════════

TOOL_MAP = {
    "mcp_developer_guide": mcp_developer_guide_impl,
    "scaffold_new_mcp":    scaffold_new_mcp_impl,
}


def _respond(data: dict) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _handle(req: dict) -> None:
    mid    = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {})

    if method == "initialize":
        _respond({
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "meta-mcp", "version": "1.0.0"},
                "instructions": (
                    "meta-mcp: 元 MCP 骨架生成器。"
                    "先调用 mcp_developer_guide 对齐需求，"
                    "再调用 scaffold_new_mcp 生成代码。"
                ),
            },
        })
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        tool_list = []
        for name, fn in TOOL_MAP.items():
            doc = (fn.__doc__ or "").strip().split("\n")[0]
            tool_list.append({
                "name": name,
                "description": doc or name,
                "inputSchema": {"type": "object", "properties": {}},
            })
        _respond({"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list}})
        return

    if method == "tools/call":
        tname = params.get("name", "")
        targs = params.get("arguments", {})
        if tname not in TOOL_MAP:
            _respond({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32601, "message": "未知工具: " + tname}})
            return
        try:
            result = TOOL_MAP[tname](**targs)
            is_err = not result.get("ok", True)
            _respond({
                "jsonrpc": "2.0", "id": mid,
                "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                    "isError": is_err,
                },
            })
        except Exception as exc:
            logger.exception("工具执行异常: %s", exc)
            _respond({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32603, "message": "执行失败: " + str(exc)}})
        return

    if mid:
        _respond({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "未知方法: " + method}})


def main() -> None:
    logger.info("meta-mcp 启动 | stdio 模式 | Python %s", sys.version.split()[0])
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            _handle(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("无效 JSON: %s", line[:80])


if __name__ == "__main__":
    main()
