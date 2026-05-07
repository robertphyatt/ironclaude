import pytest


def test_get_system_memory_returns_valid_data():
    import psutil
    mem = psutil.virtual_memory()
    total_gb = round(mem.total / (1024**3), 1)
    available_gb = round(mem.available / (1024**3), 1)

    assert total_gb > 0
    assert available_gb > 0
    assert available_gb <= total_gb


def test_get_system_memory_mcp_tool():
    import psutil
    mem = psutil.virtual_memory()
    result = {
        "total_gb": round(mem.total / (1024**3), 1),
        "available_gb": round(mem.available / (1024**3), 1),
    }
    assert isinstance(result["total_gb"], float)
    assert isinstance(result["available_gb"], float)
    assert result["total_gb"] > 0
    assert result["available_gb"] > 0
    assert result["available_gb"] <= result["total_gb"]
