from mcp.server import FastMCP

app = FastMCP(name='local-kb')

@app.tool(name='lookup', description='Lookup anchored release guidance for the current task.')
def lookup(query: str) -> str:
    q = (query or '').strip()
    return (
        'Knowledge base result for ' + q + ': '\
        'Preserve anchor WORKTREE_MCP_SKILL_SMOKE, read README.md before editing, '\
        'keep changes inside the active worktree, and run python -m pytest -q before completion.'
    )

if __name__ == '__main__':
    app.run('stdio')
