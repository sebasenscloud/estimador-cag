import importlib


def test_litellm_tool_calling_path_can_be_imported():
    # LiteLLM importa su handler de MCP en cualquier llamada con `tools`, que es
    # como Instructor obtiene la salida estructurada. Sin el paquete `mcp`, todas
    # las llamadas reales fallan con "No module named 'mcp'".
    importlib.import_module("litellm.responses.mcp.chat_completions_handler")
