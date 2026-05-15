# -*- coding: utf-8 -*-
"""
server_gen.py — meta-mcp 的代码生成逻辑（与 MCP 协议完全解耦）
"""
from __future__ import annotations
import json

# ══════════════════════════════════════════════════════════════════════════════
# 模板片段（使用普通字符串拼接，不用三引号嵌套）
# ══════════════════════════════════════════════════════════════════════════════

_TOOL_FUNCTION = """
@_safe_tool
def {tool_name}({params_sig}) -> dict:
    \"\"\"
    执行 {tool_name} 操作。
    {description}
    \"\"\"
    logger.info("[{tool_name}] 被调用 | args: %s", locals())
    # ── TODO: 在下方填入业务逻辑 ────────────────────────
    raise NotImplementedError("请在 server.py 中实现 {tool_name} 的业务逻辑")
    # ──────────────────────────────────────────────────
"""

_SETTINGS = """from pydantic import ConfigDict\n\nclass Settings(BaseModel):\n    model_config = ConfigDict(extra='allow')\n\n    @classmethod\n    def from_env(cls):\n        env_kv = {k: v for k, v in os.environ.items() if k.isupper()}\n        return cls(**env_kv)\n\nsettings = Settings.from_env()"""

_EXTRAS_DB = """
# ── 数据库（SQLAlchemy 连接池）─────────────────────────
# from sqlalchemy import create_engine
# from sqlalchemy.orm import sessionmaker
# from sqlalchemy.pool import QueuePool
# engine = create_engine(
#     os.environ["DATABASE_URL"],
#     poolclass=QueuePool, pool_size=5, max_overflow=10,
#     pool_pre_ping=True, pool_recycle=3600,
# )
# SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
"""

_EXTRAS_FILE = """
# ── 文件操作安全工具 ──────────────────────────────────
from pathlib import Path

def _safe_path(path: str, base: str = None) -> Path:
    p = Path(path).expanduser().resolve()
    if base and not str(p).startswith(str(Path(base).resolve())):
        raise ValueError("路径越界: " + str(path))
    if ".." in Path(path).parts:
        raise ValueError("禁止路径穿越: " + str(path))
    return p

def _atomic_write(path: Path, content: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content); tmp.rename(path)

def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
"""

_SAFE_TOOL = """
def _safe_tool(func):
    def wrapper(*args, **kwargs):
        try:
            return {"ok": True, "data": func(*args, **kwargs), "error": None}
        except Exception as e:
            logger.exception("Tool '%s' 异常", func.__name__)
            return {
                "ok": False, "data": None,
                "error": {
                    "type": type(e).__name__, "message": str(e),
                    "hint": "请检查参数或查看日志。",
                },
            }
    wrapper.__name__ = func.__name__
    return wrapper
"""

_MCP_STDIO = """
# ═══════════════════════════════════════════════════════════════
# MCP stdio 协议入口（模块级注册，if __name__ 只做主循环）
# ═══════════════════════════════════════════════════════════════

def _send(data: dict) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\\n")
    sys.stdout.flush()

def respond(req: dict) -> None:
    mid    = req.get("id")
    method = req.get("method", "")
    params = req.get("params", {{}})

    if method == "initialize":
        _send({{
            "jsonrpc": "2.0", "id": mid,
            "result": {{
                "protocolVersion": "2024-11-05",
                "capabilities": {{"tools": {{}}}},
                "serverInfo": {{"name": "{project_name}", "version": "1.0.0"}},
            }},
        }})
        return

    if method == "tools/list":
        _send({{
            "jsonrpc": "2.0", "id": mid,
            "result": {{
                "tools": [
                    {{
                        "name": name,
                        "description": (fn.__doc__ or name).strip().split(chr(10))[0],
                        "inputSchema": {{"type": "object", "properties": {{}}}},
                    }}
                    for name, fn in TOOLS.items()
                ]
            }},
        }})
        return

    if method == "tools/call":
        tname = params.get("name", "")
        targs = params.get("arguments", {{}})
        if tname in TOOLS:
            result = TOOLS[tname](**targs)
            _send({{
                "jsonrpc": "2.0", "id": mid,
                "result": {{
                    "content": [{{
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False),
                    }}],
                }},
            }})
        else:
            _send({{
                "jsonrpc": "2.0", "id": mid,
                "error": {{"code": -32601, "message": "未知工具: " + tname}},
            }})
        return

    if mid:
        _send({{
            "jsonrpc": "2.0", "id": mid,
            "error": {{"code": -32601, "message": "未知方法: " + method}},
        }})

if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            respond(json.loads(line))
        except Exception as e:
            logger.exception("请求处理异常: %s", e)
"""


# ══════════════════════════════════════════════════════════════════════════════
# 内部工具函数
# ══════════════════════════════════════════════════════════════════════════════

def _ptype(ftype: str) -> str:
    return {"string": "str", "number": "float", "integer": "int", "boolean": "bool"}.get(ftype, "str")


def _generate_tools(schema: dict) -> tuple[str, str]:
    """生成工具函数代码 + RequestModel 代码。"""
    model_fields, tool_funcs = [], []

    for name, meta in schema.items():
        desc  = meta.get("description", "")
        ftype = meta.get("type", "string")
        dflt  = meta.get("default", ...)
        ptype = _ptype(ftype)
        if dflt is ...:
            params_sig = f'{name}: {ptype} = Field(description="{desc}")'
        else:
            params_sig = f'{name}: {ptype} = Field(default={repr(dflt)}, description="{desc}")'
        model_fields.append(f"    {name}: {ptype} = Field(description=\"{desc}\")")
        tool_funcs.append(_TOOL_FUNCTION.format(
            tool_name=name,
            params_sig=params_sig,
            description=desc,
        ))

    if model_fields:
        model_code = "class RequestModel(BaseModel):\n" + "\n".join(model_fields) + "\n"
    else:
        model_code = "# 无显式 schema\nclass RequestModel(BaseModel): ...\n"

    return "".join(tool_funcs), model_code


def _generate_server_py(project_name: str, tool_type: str, schema: dict) -> str:
    tools_code, model_code = _generate_tools(schema)
    extras = {"db": _EXTRAS_DB, "file": _EXTRAS_FILE}.get(tool_type, "")

    # 工具注册表
    if schema:
        registry_lines = ",\n".join(f"        {repr(n)}: {n}" for n in schema.keys())
    else:
        registry_lines = "        # 无显式工具（请在 server.py 中定义）"

    mcp_stdio = _MCP_STDIO.format(
        project_name=project_name,
    )

    # 拼装所有片段
    # ── 工具函数注册表（字典字面量 {}，避免 dict() 构造器语法错误）──
    if schema:
        registry_items = [("    " + repr(n) + ": " + n) for n in schema.keys()]
        registry_lines = ",\n".join(registry_items)
    else:
        registry_lines = "    # 无显式工具（请在 server.py 中定义）"

    tools_section = (
        (tools_code.rstrip() if tools_code else "# （根据 schema_definition 自动生成工具）")
        + "\n\n"
        + "# ── 工具注册表（在 def 之后，避免前向引用）──\n"
        + "TOOLS = {\n"
        + registry_lines
        + "\n}"
    )

    parts = [
        "#!/usr/bin/env python3",
        "# -*- coding: utf-8 -*-",
        '"""',
        f"{project_name} -- MCP Server",
        "自动生成骨架 · 请在 TODO 标记处填入业务逻辑",
        '"""',
        "from __future__ import annotations",
        "import os, sys, json, logging",
        "from pathlib import Path",
        "",
        "# ── Pydantic ────────────────────────────────────────",
        "from pydantic import BaseModel, Field",
        "",
        "# ── 日志 ────────────────────────────────────────────",
        'logging.basicConfig(',
        '    level=logging.INFO,',
        '    format="%(asctime)s [%(levelname)s] %(name)s -- %(message)s",',
        '    datefmt="%Y-%m-%d %H:%M:%S",',
        ")",
        f'logger = logging.getLogger("{project_name}")',
        "",
        extras,
        "",
        "# ── 全局异常包裹（绝不 Crash）───────────────────────",
        _SAFE_TOOL.strip(),
        "",
        "# ── Settings ────────────────────────────────────────",
        _SETTINGS.strip(),
        "",
        "# ── 请求模型 ────────────────────────────────────────",
        model_code.rstrip(),
        "",
        "# ── 工具实现 ────────────────────────────────────────",
        tools_section,
        "",
        mcp_stdio.lstrip().replace(
            "TOOLS = dict(\n{tool_registry}\n)",
            "TOOLS_REGISTRY_PLACEHOLDER"
        ),  # TOOLS 已在上方注册，此处占位替换为空
    ]

    # 去掉占位行（TOOLS 已在工具区段注册）
    result_lines = []
    for line in "\n".join(parts).split("\n"):
        if "TOOLS_REGISTRY_PLACEHOLDER" in line:
            continue
        result_lines.append(line)

    return "\n".join(result_lines)


def _generate_readme(project_name: str, tool_type: str, schema: dict) -> str:
    rows = []
    for k, meta in schema.items():
        t = meta.get("type", "str")
        d = meta.get("description", "")
        rows.append(f"| `{k}` | `{t}` | {d} |")
    rows_str = "\n".join(rows) or "| _(根据 schema_definition 自动生成)_ | | |"

    extra_sections = {
        "api":  '\n## API 特殊配置\n\n```bash\nAPI_KEY=your_key_here\nBASE_URL=https://api.example.com\nTIMEOUT=30\n```\n',
        "db":   '\n## 数据库特殊配置\n\n```bash\nDATABASE_URL=postgresql://user:pass@localhost:5432/mydb\n```\n',
        "file": '\n## 文件操作特殊配置\n\n```bash\nWORK_DIR=/path/to/allowed/dir\n```\n',
    }
    extra = extra_sections.get(tool_type, "")

    return f"""\
# {project_name}

> Auto-generated MCP Server (tool_type={tool_type})
> 由 meta-mcp 生成 · 请在 `server.py` 的 `TODO` 处填入业务逻辑

## 快速启动

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填入真实值
python server.py        # stdio 模式
```

## 工具列表

| 工具名 | 类型 | 说明 |
|--------|------|------|
{rows_str}
{extra}
## 注意事项

- **严禁硬编码**任何密钥，统一走环境变量
- 所有 Tool 均被 `_safe_tool` 装饰器包裹，异常不 Crash
- `.env` 不会提交到 Git（已写入 `.gitignore`）
"""


# ══════════════════════════════════════════════════════════════════════════════
# 公开 API
# ══════════════════════════════════════════════════════════════════════════════

def build_scaffold(
    project_name: str,
    tool_type: str,
    schema_definition: str,
) -> dict:
    """
    解析 schema_definition，生成完整的工程骨架文件。
    schema_definition 可以是 JSON string 或自然语言多行文本。
    """
    # 解析 schema
    try:
        schema = json.loads(schema_definition)
    except Exception:
        schema = {}
        for line in schema_definition.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ": " in line:
                k, desc = line.split(": ", 1)
                schema[k.strip()] = {"type": "string", "description": desc.strip()}
            elif line.startswith("- "):
                parts = line[2:].split(" - ", 1)
                if len(parts) == 2:
                    schema[parts[0].strip()] = {"type": "string", "description": parts[1].strip()}

    files = {
        "server.py":        _generate_server_py(project_name, tool_type, schema),
        "requirements.txt": "fastmcp>=0.1.0\npydantic>=2.0\npython-dotenv>=1.0\nhttpx>=0.27\nsqlalchemy>=2.0\npsycopg2-binary>=2.9\npytest>=8.0\npytest-asyncio>=0.23\n",
        ".env.example": (
            f"# {project_name} 环境变量配置\n"
            "# 复制为 .env（.gitignore 已排除）\n"
            "LOG_LEVEL=INFO\n"
            "# API_KEY=your_key_here\n"
            "# BASE_URL=https://api.example.com\n"
            "# TIMEOUT=30\n"
            "# DATABASE_URL=postgresql://user:pass@localhost:5432/mydb\n"
            "# WORK_DIR=/path/to/allowed/dir\n"
        ),
        ".gitignore": ".env\n__pycache__/\n*.pyc\n.venv/\n.eggs/\n*.egg-info/\n.pytest_cache/\n.DS_Store\n",
        "README.md": _generate_readme(project_name, tool_type, schema),
    }

    return files
