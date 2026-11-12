"""编译期机械修复：重复声明、双驱动。

模型在整份重写或锚点补丁里反复制造同一类语法错误时，不必再花一轮调用去猜：
错误位置就在 stderr 里，删掉后出现的那条声明/驱动即可，而且可验证（重新展开通过才采纳）。
"""

from __future__ import annotations

import re

DUPLICATE_DECLARATION = re.compile(
    r"^(?P<path>\S+?):(?P<line>\d+):\s*error:\s*'(?P<name>\w+)' has already been declared in this scope",
    re.M,
)
DUPLICATE_DRIVER = re.compile(
    r"^(?P<path>\S+?):(?P<line>\d+):\s*error:\s*Cannot perform procedural assignment to "
    r"(?:variable|array word) '(?P<name>\w+)' because it is also continuously assigned",
    re.M,
)
DUPLICATE_PORT = re.compile(
    r"^(?P<path>\S+?):(?P<line>\d+):\s*error:\s*port ``(?P<name>\w+)'' already bound",
    re.M,
)
INVALID_LVALUE = re.compile(
    r"error:\s*'(?P<name>\w+)' is not a valid l-value for a procedural assignment",
)
INSTANTIATION = re.compile(r"^\s*\w+(?:\s*#\s*\(.*\))?\s+u_\w+\s*\(")


def _split(combined: str) -> tuple[list[str], dict[str, list[str]]]:
    """把 combine_files 的产物拆成（有顺序的路径表，路径 -> 行）。"""

    order: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in combined.splitlines():
        if line.startswith("// file: "):
            current = line[len("// file: "):].strip()
            order.append(current)
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return order, sections


def _join(order: list[str], sections: dict[str, list[str]]) -> str:
    return "\n\n".join(f"// file: {path}\n" + "\n".join(sections[path]) for path in order) + "\n"


def _locate(order: list[str], sections: dict[str, list[str]], line: int) -> tuple[str, int] | None:
    """把合并文件里的行号映射回（模块路径，模块内行号）。"""

    cursor = 0
    for path in order:
        span = len(sections[path]) + 2  # ``// file:`` 头 + 内容 + 空行
        if cursor < line <= cursor + span:
            local = line - cursor - 1
            if 1 <= local <= len(sections[path]):
                return path, local
            return None
        cursor += span
    return None


def _drop_duplicate_port(lines: list[str], port: str) -> int | None:
    """在同一个实例化里删掉后出现的同名端口连接；返回删掉的行号（0 基）。"""

    for index, line in enumerate(lines):
        if not INSTANTIATION.match(line):
            continue
        end = len(lines) - 1
        for probe in range(index, len(lines)):
            if lines[probe].strip() == ");":
                end = probe
                break
        hits = [i for i in range(index, end + 1) if re.search(rf"\.\s*{re.escape(port)}\s*\(", lines[i])]
        if len(hits) < 2:
            continue
        target = hits[-1]
        depth = lines[target].count("(") - lines[target].count(")")
        stop = target
        while depth > 0 and stop + 1 < len(lines):
            stop += 1
            depth += lines[stop].count("(") - lines[stop].count(")")
        del lines[target:stop + 1]
        # 删掉的是逗号分隔列表里的一项：若它原本是最后一项，前一行末尾的逗号也要去掉。
        for probe in range(target, len(lines)):
            if lines[probe].strip():
                if lines[probe].strip().startswith(")"):
                    previous = target - 1
                    while previous >= 0 and not lines[previous].strip():
                        previous -= 1
                    if previous >= 0 and lines[previous].rstrip().endswith(","):
                        lines[previous] = lines[previous].rstrip()[:-1]
                break
        return target
    return None


def repair(files: dict[str, str], stderr: str) -> tuple[dict[str, str], list[str]]:
    """删除后出现的重复声明/重复驱动；返回新文件与人类可读的改动说明。"""

    combined = "\n\n".join(f"// file: {path}\n{content}" for path, content in files.items()) + "\n"
    order, sections = _split(combined)
    notes: list[str] = []
    for pattern in (DUPLICATE_DECLARATION, DUPLICATE_DRIVER):
        for match in pattern.finditer(stderr):
            name = match.group("name")
            located = _locate(order, sections, int(match.group("line")))
            if located is None:
                continue
            path, local = located
            lines = sections[path]
            index = local - 1
            if not 0 <= index < len(lines):
                continue
            line = lines[index]
            if name not in line:
                continue
            # 连续赋值语句：整行删掉即可；声明行也一样（保留更早的那条）。
            if pattern is DUPLICATE_DRIVER and "assign" not in line and "<=" not in line and "=" not in line:
                continue
            del lines[index]
            notes.append(f"{path}:{local} 删除重复{'驱动' if pattern is DUPLICATE_DRIVER else '声明'} {name}")
    for match in DUPLICATE_PORT.finditer(stderr):
        for path in order:
            removed = _drop_duplicate_port(sections[path], match.group("name"))
            if removed is not None:
                notes.append(f"{path}:{removed + 1} 删除重复端口连接 .{match.group('name')}()")
                break
    for match in INVALID_LVALUE.finditer(stderr):
        name = match.group("name")
        # 该信号既然在实例化端口上被连线，说明由子模块驱动：删掉本模块里的过程赋值。
        for path in order:
            lines = sections[path]
            if not any(re.search(rf"\.\s*{re.escape(name)}\s*\(", line) for line in lines):
                continue
            removed = []
            index = 0
            while index < len(lines):
                if re.match(rf"\s*{re.escape(name)}\s*(?:<=\s*|=)(?!=)", lines[index]):
                    stop = index
                    while stop < len(lines) and ";" not in lines[stop]:
                        stop += 1
                    del lines[index:stop + 1]
                    removed.append(index + 1)
                    continue
                index += 1
            if removed:
                notes.append(f"{path}: 删除 {name} 的过程赋值（由子模块驱动）{removed[:4]}")
        for path in order:
            lines = sections[path]
            for index, line in enumerate(lines):
                if not re.search(rf"\b{re.escape(name)}\b", line):
                    continue
                if not re.match(r"\s*(?:output|inout)\b", line):
                    continue
                if re.search(r"\b(?:logic|reg)\b", line):
                    continue
                lines[index] = re.sub(r"(\s*(?:output|inout))\s+", r"\1 logic ", line, count=1)
                notes.append(f"{path}:{index + 1} {name} 端口补 logic（有过程赋值）")
                break
        for path in order:
            lines = sections[path]
            for index, line in enumerate(lines):
                if not re.search(rf"\bwire\b", line) or not re.search(rf"\b{re.escape(name)}\b", line):
                    continue
                if line.lstrip().startswith("assign"):
                    continue
                lines[index] = re.sub(r"\bwire\b", "logic", line, count=1)
                notes.append(f"{path}:{index + 1} {name} 由 wire 改为 logic（有过程赋值）")
                break
    if not notes:
        return files, []
    repaired = {
        path: "\n".join(sections[path]).strip("\n")
        for path in order
    }
    return repaired, notes
