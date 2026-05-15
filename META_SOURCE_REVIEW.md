# META_SOURCE_REVIEW.md — meta-mcp 核心源码复核

> 项目：meta-mcp（元 MCP Server 骨架生成器）
> 分支：feat/meta-mcp-core
> 生成时间：2026-05-15
> 描述：纯 stdio JSON-RPC 2.0 MCP Server，无 FastMCP 依赖，仅 pydantic 做参数校验

---

## 📁 文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `server.py` | 318 | MCP stdio 主入口，协议层，工具注册与分发 |
| `server_gen.py` | 369 | 代码生成逻辑，模板片段，骨架构建器 |
| `prompt_guide.md` | 1822 chars | 需求对齐 Prompt 模板（外置避免 Python 字符串引号冲突） |
| `requirements.txt` | 8 行 | 依赖清单 |

---

## 📄 `server.py`

```python
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
```

---

## 📄 `server_gen.py`

```python
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

_SETTINGS = """from pydantic import ConfigDict

class Settings(BaseModel):
    model_config = ConfigDict(extra='allow')

    @classmethod
    def from_env(cls):
        env_kv = {k: v for k, v in os.environ.items() if k.isupper()}
        return cls(**env_kv)

settings = Settings.from_env()"""

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
        model_fields.append(f'    {name}: {ptype} = Field(description="{desc}")')
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

    mcp_stdio = _MCP_STDIO.format(project_name=project_name)

    # ── 工具函数注册表（字典字面量 {}，避免前向引用 + dict() 构造器语法错误）──
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
        ),
    ]

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
```

---

## 📄 `prompt_guide.md`

```markdown
# mcp-developer-guide · MCP 开发需求对齐工作流

> **强制规则**：当用户要求开发一个 MCP Server 时，**必须**先调用本 Prompt 对齐需求，
> 获得用户明确「通过」确认后，才能调用 `scaffold_new_mcp` 生成代码。
> 禁止跳过需求对齐阶段直接生成代码。

---

## 第一步：混合式探针提问

向用户抛出以下问题组：

### 固化基础问题（每单必问）

1. **目标系统的官方文档或接口协议是什么？**
   → 提供文档链接有助于生成准确的认证头、请求格式、错误码处理。

2. **鉴权方式是什么？（必须提醒不可硬编码）**
   → 提醒用户：API Key / OAuth2 / JWT / Cookie … 无论哪种，都要通过环境变量注入，
     禁止把密钥写进代码。

---

### 动态灵活问题（根据用户需求追加 2-3 个）

| 领域 | 追加问题 |
|------|---------|
| **API 抓取** | ① 请求频率上限？（避免触发限流）<br>② 分页机制？（页大小/Cursor）<br>③ 错误重试策略？ |
| **数据库** | ① 连接池上限？（防止耗尽）<br>② 读写分离还是只读？<br>③ 迁移策略？ |
| **文件操作** | ① 允许操作的根目录白名单？<br>② 是否需要原子写入（防截断）？<br>③ 并发冲突处理？ |
| **CLI / 系统** | ① 子命令列表？<br>② 输出格式：JSON / Table / 纯文本？<br>③ 状态文件存哪里？ |
| **通用** | ① 单次请求超时上限？（建议 30s 内）<br>② 是否需要缓存？<br>③ 日志级别默认 INFO？ |

---

## 第二步：方案确认

收集用户回答后，输出**参数契约确认书**：

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 参数契约确认书
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【项目信息】
  项目名称：<你的回答>
  工具类型：<api | cli | db | file | generic>

【鉴权方式】
  类型：<API Key / OAuth2 / JWT / Cookie / 无>
  环境变量名：<YOUR_API_KEY / ...>

【核心参数】
  <参数1>  (<类型>) — <描述>
  <参数2>  (<类型>) — <描述>

【异常处理策略】
  ① HTTP 4xx → <处理方式>
  ② HTTP 5xx → <重试次数 / 降级策略>
  ③ 网络超时 → <超时时间 / 重试次数>
  ④ 鉴权失败 → <错误信息 / 检查哪些变量>

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
请回复「通过」确认以上契约，我将开始生成代码。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

> ⚠️ **强制规则**：只有用户明确回复「通过」后，才能进入第三步。

---

## 第三步：调用 scaffold_new_mcp

用户确认后，调用：

```
scaffold_new_mcp(
    project_name="<项目名>",
    tool_type="<api | cli | db | file | generic>",
    schema_definition="<JSON string>"
)
```

schema_definition 为 JSON string，格式示例：
```
{
  "参数名": {
    "type": "string|integer|number|boolean",
    "description": "描述",
    "required": true,
    "default": null
  }
}
```

---

## 禁止行为

- 跳过第一步直接生成代码
- 在 schema_definition 中硬编码密钥
- 生成后不告知用户如何挂载
```

---

## 📦 `requirements.txt`

```
fastmcp>=0.1.0
pydantic>=2.0
python-dotenv>=1.0
httpx>=0.27
sqlalchemy>=2.0
psycopg2-binary>=2.9
pytest>=8.0
pytest-asyncio>=0.23
```

---

## 📋 关键设计决策备注

### 1. 纯 stdio 而非 FastMCP
- **原因**：FastMCP ≥0.1.0 要求 Python ≥3.10，macOS 内置 Python 3.9.6 不满足
- **实现**：手动实现 JSON-RPC 2.0 协议，stdin 读请求，stdout 写响应，stderr 打日志

### 2. Prompt 外置为 `.md` 文件
- **原因**：Python 三引号 `"""..."""` 无法嵌套包含 `"""` 的内容（`schema_definition="""{..."""`）
- **解决**：将 Prompt 内容写入 `prompt_guide.md`，运行时由 `_load_prompt()` 加载到 `PROMPT_DEVELOPER_GUIDE` 变量

### 3. server_gen.py 独立于 server.py
- **原因**：避免 `globals()` 自省暴露 Path/BaseModel/Field 等导入函数；避免 `dict()` 构造器前向引用
- **解决**：生成逻辑完全下沉到 `server_gen.py`，`server.py` 通过 `importlib.util` 延迟导入

### 4. TOOLS 字典字面量 `{}` 而非 `dict()`
- **原因**：Python 中 `dict(key=value)` 是关键字参数语法，不接受字符串键；`dict({'key': value})` 是字典字面量传参，但 `dict('key': value)` 语法错误
- **解决**：使用 `TOOLS = {'key': value}` 字典字面量，且定义在所有 `@_safe_tool` 装饰函数之后（避免前向引用 NameError）

### 5. `_safe_tool` 全局异常包裹
- 所有工具函数均被 `@_safe_tool` 装饰，任何未捕获异常都返回结构化 `{"ok": false, "error": {...}}`
- 绝不向 stdout 写出异常堆栈，保证 MCP 协议响应不被污染

---

**✅ 源码复核文件已生成，以上为完整代码预览，请 review 逻辑是否符合预期。**
