"""Modifier 子 Agent：在持久多轮上下文中根据 Tester 失败报告修复 RTL。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import SubAgent
from .common import (
    MODIFIER_SYSTEM_PROMPT,
    extract_json_payload,
    report_feedback,
    safe_project_path,
    validate_modifier_files,
)


def _effective_changes(
    changed: dict[str, str], current: dict[str, str]
) -> dict[str, str]:
    """过滤掉返回内容与当前文件完全相同的空补丁。"""
    result: dict[str, str] = {}
    for path, content in changed.items():
        if content != current.get(path):
            result[path] = content
    return result


class ModifierAgent(SubAgent):
    name = "modifier"
    title = "05 · Modifier"
    node_type = "modifier"

    # 对话历史上限；保留 system 与第一条完整项目上下文，再保留最近几轮。
    MAX_HISTORY_CHARS = 220_000
    TAIL_MESSAGES = 6
    # Modifier 是代码修复步骤，不需要长思维链。思考模式会把输出预算消耗在
    # reasoning 上，极端情况下 60k 全部用于思考、最终 JSON 被截断。
    MODEL_OPTIONS = {"thinking": False}

    def _initial_message(
        self,
        effective_spec: str,
        plan: dict[str, Any],
        files: dict[str, str],
        report: dict[str, Any],
        attempt: int,
    ) -> str:
        architecture = plan.get("architecture")
        architecture_block = ""
        if isinstance(architecture, dict):
            architecture_block = (
                "\n\nPlanner Skill 架构参数（直接来自 Python Skill，修复时必须保持）：\n"
                + json.dumps(architecture.get("parameters") or {}, ensure_ascii=False, indent=2)
                + "\nCoder 指令：\n"
                + "\n".join(f"- {item}" for item in (architecture.get("coder_directives") or []))
                + "\n"
            )
        return (
            f"这是同一 RTL 项目的第 {attempt} 轮修复。下面先给出完整项目上下文；"
            "之后每轮只追加新的失败报告和当前文件快照，请沿用前面的对话，不要重新设计项目。\n\n"
            "修复策略：\n"
            "1. 先判断 stage：compile 时逐行阅读 stderr 的 hierarchy/port/width/语法错误；"
            "simulation 时先定位失败 check 和 trace 的期望/实际差异，再找根因。\n"
            "2. 必须保持 Planner 的模块文件名、模块名和端口列表不变；"
            "如果多个文件端口不一致，以计划中的 interfaces 为准统一接口。\n"
            "3. compile 常见修复：删除模块内重复的 net/logic 声明（端口列表已声明的信号不能在模块体重复声明）；"
            "为子模块补上缺失端口并在顶层实例化处连接；统一端口方向/位宽；"
            "遇到 Icarus 的 `All but the final index in a chain of indices must be a single value` 时，"
            "改用中间向量/单值索引或分开赋值，不要嵌套范围 part-select；"
            "不要把多个子模块粘到顶层，也不要把端口改名。\n"
            "4. simulation 常见修复：对照失败 check 的 expected/actual 修改数据通路，"
            "不要为了通过而删除检查或伪造 dbg 事件。\n"
            "5. 只修改必要的文件；每个返回文件必须是完整内容，不能返回 diff/片段。\n"
            "6. 修复后必须能编译并重新通过教师 TB；不要遗漏由修改引入的其他模块调用点。\n"
            "7. 本轮必须逐项覆盖上一轮报告中的全部失败检查；如果某个 check 已连续失败，不要重复上一轮同类修改，先重新定位根因。\n\n"
            f"项目计划：\n{json.dumps(plan, ensure_ascii=False, indent=2)}\n"
            + architecture_block
            + f"\n当前项目文件：\n{json.dumps(files, ensure_ascii=False, indent=2)}\n\n"
            f"Spec（长 Spec 截断到 45000 字符，完整内容在 artifact 中）：\n{effective_spec[:45000]}\n\n"
            f"第一次失败报告：\n{report_feedback(report)}\n\n"
            "请只返回约定的 JSON 对象。"
        )

    def _followup_message(
        self,
        attempt: int,
        files: dict[str, str],
        report: dict[str, Any],
    ) -> str:
        return (
            f"这是同一项目的第 {attempt} 轮修复，继续使用前面的对话上下文。"
            "不要重新规划项目，也不要改变模块/文件结构。\n\n"
            f"最新失败报告：\n{report_feedback(report)}\n\n"
            f"当前文件快照：\n{json.dumps(files, ensure_ascii=False, indent=2)}\n\n"
            "请基于上一轮修改结果继续修复；逐项覆盖最新报告的全部失败检查，"
            "同一 check 连续失败时不要重复上一轮同类修改。只返回需要修改文件的完整 JSON。"
        )

    def _load_conversation(self) -> list[dict[str, str]]:
        raw = self.context.get("conversation")
        history: list[dict[str, str]] = []
        if isinstance(raw, list):
            for item in raw:
                if (
                    isinstance(item, dict)
                    and item.get("role") in {"system", "user", "assistant"}
                    and isinstance(item.get("content"), str)
                ):
                    history.append({"role": item["role"], "content": item["content"]})
        if history and history[0].get("role") == "system":
            history[0] = {"role": "system", "content": MODIFIER_SYSTEM_PROMPT}
            return history
        return [{"role": "system", "content": MODIFIER_SYSTEM_PROMPT}]

    def _trim_history(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        total = sum(len(item.get("content") or "") for item in messages)
        if total <= self.MAX_HISTORY_CHARS or len(messages) <= self.TAIL_MESSAGES + 2:
            return messages
        # 保留 system + 第一条完整项目上下文 + 最近几轮。
        head = messages[:2]
        tail = messages[-self.TAIL_MESSAGES :]
        kept: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in head + tail:
            key = (item.get("role", ""), item.get("content", ""))
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        return kept

    def _save_conversation(self, messages: list[dict[str, str]], **extra: Any) -> None:
        self.save_context(conversation=messages, **extra)

    def run(self, tester_handoff: Path, attempt: int) -> Path:
        incoming = self.runtime.read_handoff(tester_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        plan_ref = artifacts.get("plan")
        manifest_ref = artifacts.get("rtl_manifest")
        report_ref = artifacts.get("report")
        if not all(isinstance(ref, str) for ref in (spec_ref, plan_ref, manifest_ref, report_ref)):
            raise ValueError("Tester → Modifier handoff 缺少必需 artifact")

        effective_spec = self.runtime.read_text_artifact(spec_ref)
        plan = self.runtime.read_json_artifact(plan_ref)
        manifest = self.runtime.read_json_artifact(manifest_ref)
        report = self.runtime.read_json_artifact(report_ref)
        entries = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(plan, dict) or not isinstance(report, dict) or not isinstance(entries, list):
            raise ValueError("Modifier 输入 artifact 格式无效")

        files: dict[str, str] = {}
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("path"), str)
                or not isinstance(entry.get("artifact"), str)
            ):
                raise ValueError("RTL manifest 含有无效文件引用")
            files[entry["path"]] = self.runtime.read_text_artifact(entry["artifact"])

        messages = self._load_conversation()
        if len(messages) == 1:
            user_prompt = self._initial_message(effective_spec, plan, files, report, attempt)
        else:
            user_prompt = self._followup_message(attempt, files, report)
        messages.append({"role": "user", "content": user_prompt})
        messages = self._trim_history(messages)
        self._save_conversation(messages, attempts=int(self.context.get("attempts", 0)))

        event = self.runtime.begin_event(
            "modifier", self.node_type, f"{self.title} · Round {attempt}", user_prompt, attempt
        )
        payload: dict[str, Any] = {}
        changed: dict[str, str] = {}
        last_error: ValueError | None = None

        for parse_attempt in range(1, 3):
            if parse_attempt > 1:
                correction = (
                    "上一次输出不能作为有效修复（"
                    f"{last_error}）。请继续保持同一项目上下文，"
                    "先比较当前文件快照，定位一个真正不同的根因，再修改代码；"
                    "如果内容和当前快照完全相同，必须换一种修复策略，不能复制原文件。"
                    "只输出一个 JSON 对象，包含 files 数组和 notes；不要输出 Markdown 或解释。"
                )
                messages.append({"role": "user", "content": correction})
                messages = self._trim_history(messages)
                self._save_conversation(messages, attempts=int(self.context.get("attempts", 0)))

            result = self.runtime.call_model_messages(
                event, messages, model_options=self.MODEL_OPTIONS
            )
            answer = result.get("answer") or ""
            messages.append({"role": "assistant", "content": answer})
            messages = self._trim_history(messages)
            self._save_conversation(
                messages,
                attempts=int(self.context.get("attempts", 0)),
                last_parse_error=str(last_error) if last_error else None,
            )
            try:
                payload = extract_json_payload(answer)
                changed = _effective_changes(validate_modifier_files(payload, plan), files)
                if not changed:
                    raise ValueError(
                        "Modifier 返回的文件内容与当前文件完全相同，"
                        "没有产生任何实际修改；必须重新分析失败根因并真正修改文件"
                    )
                last_error = None
                break
            except ValueError as error:
                last_error = error
                event["modifier_validation_error"] = str(error)

        if last_error is not None:
            raise ValueError(
                f"Modifier 修复无效（连续 2 次未返回合法 JSON）：{last_error}"
            ) from last_error

        for relative, content in changed.items():
            target = safe_project_path(self.runtime.run_dir, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            artifact_dir = self.runtime.run_dir / f"step-{event['step']:02d}-{event['node_id']}"
            (artifact_dir / "generated.sv").write_text(content, encoding="utf-8")

        notes = str(payload.get("notes") or "")
        files.update(changed)
        next_manifest_ref = self.runtime.write_json_artifact(
            f"rtl_manifest_attempt{attempt}.json",
            {
                "files": [
                    {
                        "path": path,
                        "artifact": self.runtime.artifact_ref(
                            safe_project_path(self.runtime.run_dir, path)
                        ),
                        "bytes": len(content.encode("utf-8")),
                    }
                    for path, content in files.items()
                ]
            },
        )
        self.save_context(
            attempts=int(self.context.get("attempts", 0)) + 1,
            plan_ref=plan_ref,
            rtl_manifest_ref=next_manifest_ref,
            report_ref=report_ref,
            notes=notes,
            conversation=messages,
        )
        self.finish(
            event,
            "info",
            response=f"修改了 {len(changed)} 个文件：" + "、".join(changed),
            reasoning=event.get("reasoning"),
            usage=event.get("usage"),
            conversation_turns=len(messages),
        )
        return self.runtime.write_handoff(
            "modifier",
            "tester",
            {
                "attempt": attempt,
                "changed_paths": list(changed),
                "notes": notes,
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    "rtl_manifest": next_manifest_ref,
                },
            },
            attempt=attempt,
            label="Modifier → Tester: updated RTL manifest",
        )
