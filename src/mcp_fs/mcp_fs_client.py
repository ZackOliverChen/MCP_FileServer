# mcp_client.py
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

async def main():
    # 1. Connect over HTTP
    async with streamablehttp_client(
        "http://127.0.0.1:8123/mcp"
    ) as (read, write, _):
        # 2. Start a session
        async with ClientSession(read, write) as session:
            # 3. Handshake (initialize)
            await session.initialize()

            # 4. List tools
            tools = await session.list_tools()
            print("Tools:", [t.name for t in tools.tools])

            # 5. Call a tool
            res = await session.call_tool(
                "write_file",
                {"path": "from-python-client.txt", "content": "hi from a raw client"}
            )
            print("write:", res.content[0].text)

            res = await session.call_tool("read_file", {"path": "from-python-client.txt"})
            print("read:", res.content[0].text)

asyncio.run(main())