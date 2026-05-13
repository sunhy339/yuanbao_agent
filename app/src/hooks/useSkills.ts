import { useState } from "react";
import type { SkillPresetRecord } from "@shared";
import type { SkillDraft } from "../ui/workbench/workspaces/skills/SkillsWorkspace";
import { RuntimeClient } from "../lib/runtimeClient";
import { buildSkillPayload } from "../state/mcpSkillPayloads";
import type { HookDeps } from "./types";

const runtimeClient = new RuntimeClient();

export function useSkills(deps: HookDeps) {
  const { addToast, toastError, setError } = deps;

  const [skills, setSkills] = useState<SkillPresetRecord[]>([]);
  const [skillBusyId, setSkillBusyId] = useState<string | null>(null);

  async function refreshSkills() {
    setError(null);
    try {
      const result = await runtimeClient.listSkills();
      setSkills(result.skills);
      addToast("success", "技能已刷新");
    } catch (reason) {
      toastError(reason);
    }
  }

  async function handleCreateSkill(draft: SkillDraft) {
    setSkillBusyId("create");
    setError(null);
    try {
      const result = await runtimeClient.createSkill(buildSkillPayload(draft));
      setSkills((current) => [result.skill, ...current.filter((skill) => skill.id !== result.skill.id)]);
      addToast("success", `技能已创建：${result.skill.name}`);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleUpdateSkill(skillId: string, draft: SkillDraft) {
    setSkillBusyId(skillId);
    setError(null);
    try {
      const result = await runtimeClient.updateSkill({
        skillId,
        ...buildSkillPayload(draft),
      });
      setSkills((current) =>
        current.map((skill) => skill.id === result.skill.id ? result.skill : skill),
      );
      addToast("success", `技能已更新：${result.skill.name}`);
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleDeleteSkill(skillId: string) {
    setSkillBusyId(skillId);
    setError(null);
    try {
      await runtimeClient.deleteSkill({ skillId });
      setSkills((current) => current.filter((skill) => skill.id !== skillId));
      addToast("success", "技能已删除");
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  async function handleImportSkills(filePath: string) {
    setSkillBusyId("import");
    setError(null);
    try {
      const result = await runtimeClient.importSkills({ filePath });
      if (result.imported.length > 0) {
        await refreshSkills();
        addToast("success", `已导入 ${result.imported.length} 个技能`);
      }
      if (result.skipped.length > 0) {
        addToast("info", `已跳过 ${result.skipped.length} 个同名技能：${result.skipped.join("、")}`);
      }
      if (result.errors.length > 0) {
        const errorNames = result.errors.map((e) => e.name || "未知").join("、");
        addToast("error", `导入失败：${errorNames}`);
      }
    } catch (reason) {
      toastError(reason);
    } finally {
      setSkillBusyId(null);
    }
  }

  return {
    skills,
    setSkills,
    skillBusyId,
    setSkillBusyId,
    refreshSkills,
    handleCreateSkill,
    handleUpdateSkill,
    handleDeleteSkill,
    handleImportSkills,
  };
}
