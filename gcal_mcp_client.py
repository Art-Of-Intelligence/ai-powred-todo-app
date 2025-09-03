import os, sys, json, asyncio
from contextlib import AsyncExitStack
from typing import Any, Dict, List

from dotenv import load_dotenv
from groq import Groq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

dotenv_path = ".env"
load_dotenv(dotenv_path, override=True)

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")  # good tool-calling model


def _convert_mcp_tools_to_groq(tools) -> List[Dict[str, Any]]:
    """MCP -> Groq/OpenAI tool schema adapter."""
    converted = []
    for t in tools:
        # The SDK currently exposes `inputSchema`; keep a fallback for `input_schema` just in case.
        params = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None) or {"type": "object"}
        converted.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or "No description"),
                "parameters": params,
            },
        })
    return converted


def _flatten_tool_result(result) -> str:
    """
    Turn MCP CallToolResult.content (list of content blocks) into a plain string.
    Handles both dict-like and typed content blocks.
    """
    parts = []
    content = getattr(result, "content", None) or []
    for block in content:
        # Typed content (pydantic models) often have attributes
        if hasattr(block, "type") and getattr(block, "type") == "text":
            parts.append(getattr(block, "text", ""))
        # Dict-style fallbacks
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    # If nothing detected, try a generic string cast
    if not parts:
        return str(result)
    return "\n".join(p for p in parts if p)


async def run_client(server_path: str):
    # Spawn the MCP server via stdio (`python gcal_mcp_server.py stdio`)
    server_params = StdioServerParameters(command="python", args=[server_path, "stdio"])

    async with AsyncExitStack() as stack:
        (read, write) = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # Discover tools from the server
        discovered = await session.list_tools()
        groq_tools = _convert_mcp_tools_to_groq(discovered.tools)
        print("Connected. Tools:", [t["function"]["name"] for t in groq_tools])

        # Groq client
        groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

        # Seed system prompt to nudge safe defaults (timezone, ISO times, etc.)
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a calendar assistant. You can create, list, and cancel Google Calendar events "
                    "using the provided tools. Prefer ISO 8601 for times; default timezone is Asia/Colombo. "
                    "Ask for any missing required fields before creating or canceling events."
                ),
            }
        ]

        print("\nType your request (e.g., “Create a 1-hour Meet ‘OU workshop prep’ today 3pm, invite a@x.com”).")
        print("Type 'quit' to exit.\n")

        while True:
            user = input("> ").strip()
            if user.lower() in {"quit", "exit"}:
                break

            messages.append({"role": "user", "content": user})

            # Up to a few tool-roundtrips in case the model chains calls
            for _ in range(5):
                response = groq_client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="auto",
                    temperature=0,
                )
                msg = response.choices[0].message
                tool_calls = getattr(msg, "tool_calls", None)

                # If the model asked to call tools, execute them via MCP
                if tool_calls:
                    # Append the assistant message (with tool_calls) to the transcript
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    })

                    # Execute each tool call via MCP
                    for tc in tool_calls:
                        name = tc.function.name
                        args = {}
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            # If the model produced non-JSON, pass raw string as a last resort
                            args = {"_raw": tc.function.arguments}

                        result = await session.call_tool(name, args)
                        tool_text = _flatten_tool_result(result)

                        # Return tool output to the LLM
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "name": name,
                            "content": tool_text,
                        })

                    # Loop to let the model read tool results and finish / ask next call
                    continue

                # No tool calls → final answer
                print("\n" + (msg.content or "").strip() + "\n")
                break


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python groq_mcp_client.py <path\\to\\gcal_mcp_server.py>")
        sys.exit(1)
    asyncio.run(run_client(sys.argv[1]))
