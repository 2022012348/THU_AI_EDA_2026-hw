"""本地 API key 管理。

优先级：
1. 项目根目录的本地文件 `.api_key`（已加入 .gitignore）；
2. 只有该文件不存在时才读取环境变量。

文件存在但内容为空时视为配置错误，不会回退到环境变量。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

API_KEY_FILE = PROJECT_ROOT / ".api_key"
_ENV_NAMES = ("DEEPSEEK_API_KEY", "DSV3_KEY", "LLM_API_KEY")


def _read_file() -> tuple[bool, str]:
    """返回 (文件是否存在, 去除首尾空白后的内容)。"""
    if not API_KEY_FILE.is_file():
        return False, ""
    try:
        value = API_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"无法读取本地 API key 文件：{error}") from error
    return True, value


def _environment_key() -> str:
    for name in _ENV_NAMES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:3]}…{value[-4:]}"


def resolve_api_key() -> tuple[str, str]:
    """返回 (key, source)。source 为 `file` 或 `environment`。"""
    exists, file_key = _read_file()
    if exists:
        if not file_key:
            raise RuntimeError(
                f"本地 API key 文件为空：{API_KEY_FILE}。请删除该文件以回退环境变量，"
                "或在 API 参数页面重新保存 key。"
            )
        return file_key, "file"
    env_key = _environment_key()
    if env_key:
        return env_key, "environment"
    raise RuntimeError(
        f"未找到 API key。请在 API 参数页面保存到本地，或设置环境变量 DEEPSEEK_API_KEY。"
    )


def api_key_status() -> dict[str, Any]:
    """供网页显示的状态；不返回完整 key。"""
    exists, file_key = _read_file()
    if exists:
        return {
            "configured": bool(file_key),
            "source": "file",
            "masked": _mask(file_key),
            "file": str(API_KEY_FILE),
            "env_ignored": True,
        }
    env_key = _environment_key()
    return {
        "configured": bool(env_key),
        "source": "environment" if env_key else "none",
        "masked": _mask(env_key),
        "file": str(API_KEY_FILE),
        "env_ignored": False,
    }


def save_api_key(value: str) -> dict[str, Any]:
    """原子写入本地 key 文件，并设置 0600 权限。"""
    key = str(value or "").strip()
    if not key:
        raise ValueError("API key 不能为空")
    if any(char in key for char in "\r\n"):
        raise ValueError("API key 不能包含换行")
    API_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".api_key.", dir=str(API_KEY_FILE.parent), text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
        os.replace(temp_name, API_KEY_FILE)
        os.chmod(API_KEY_FILE, 0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return api_key_status()


def clear_api_key() -> dict[str, Any]:
    """删除本地文件；若环境变量存在则回到环境变量。"""
    if API_KEY_FILE.is_file():
        API_KEY_FILE.unlink()
    return api_key_status()
