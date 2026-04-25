import { strict as assert } from "node:assert";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NewSessionWorkspace } from "./NewSessionWorkspace";

afterEach(() => {
  cleanup();
});

describe("NewSessionWorkspace", () => {
  it("renders a minimal modern session start desk", () => {
    const html = renderToStaticMarkup(
      <NewSessionWorkspace
        workspacePath={"D:\\py\\yuanbao_agent"}
        hostStatusText="Runtime host online"
        modelLabel="MiniMax-M2.7-highspeed"
        modelOptions={[{ id: "minimax", label: "MiniMax-M2.7-highspeed" }]}
      />,
    );

    assert.match(html, /New Session/);
    assert.match(html, /Type a task in the command bar below/);
    assert.match(html, /D:\\py\\yuanbao_agent/);
    assert.match(html, /Runtime host online/);
    assert.match(html, /MiniMax-M2\.7-highspeed/);
    assert.doesNotMatch(html, /Runtime preview/);
    assert.doesNotMatch(html, /session idle/);
    assert.doesNotMatch(html, /Command readiness/);
    assert.doesNotMatch(html, /Recent sessions live in the session ledger/);
    assert.doesNotMatch(html, /Settings/i);
    assert.doesNotMatch(html, /Scheduled/i);
  });

  it("lets the user select the active model and workspace folder", async () => {
    const user = userEvent.setup();
    const onSelectModel = vi.fn();
    const onWorkspacePathChange = vi.fn();
    const onOpenWorkspace = vi.fn();

    function Harness() {
      const [workspacePath, setWorkspacePath] = useState("D:\\py\\yuanbao_agent");

      return (
        <NewSessionWorkspace
          workspacePath={workspacePath}
          hostStatusText="Runtime host online"
          modelLabel="MiniMax-M2.7-highspeed"
          modelOptions={[
            { id: "minimax", label: "MiniMax-M2.7-highspeed" },
            { id: "kimi", label: "Kimi" },
          ]}
          selectedModelId="minimax"
          onSelectModel={onSelectModel}
          onWorkspacePathChange={(nextPath) => {
            setWorkspacePath(nextPath);
            onWorkspacePathChange(nextPath);
          }}
          onOpenWorkspace={onOpenWorkspace}
        />
      );
    }

    render(
      <Harness />,
    );

    await user.selectOptions(screen.getByLabelText("选择模型"), "kimi");
    await user.clear(screen.getByLabelText("工作文件夹"));
    await user.type(screen.getByLabelText("工作文件夹"), "D:\\py\\doubao_client");
    await user.click(screen.getByRole("button", { name: "应用文件夹" }));

    expect(onSelectModel).toHaveBeenCalledWith("kimi");
    expect(onWorkspacePathChange).toHaveBeenLastCalledWith("D:\\py\\doubao_client");
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);
  });
});
