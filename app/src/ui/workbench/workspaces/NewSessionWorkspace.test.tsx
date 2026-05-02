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
  it("renders a V2 session launch desk", () => {
    const html = renderToStaticMarkup(
      <NewSessionWorkspace
        workspacePath={"D:\\py\\yuanbao_agent"}
        hostStatusText="Runtime host online"
        sessionTitle="Frontend V2"
        modelLabel="MiniMax-M2.7-highspeed"
        modelOptions={[{ id: "minimax", label: "MiniMax-M2.7-highspeed" }]}
      />,
    );

    assert.match(html, /Workspace Launcher/);
    assert.match(html, /Select a workspace, confirm the runtime profile/);
    assert.match(html, /D:\\py\\yuanbao_agent/);
    assert.match(html, /Runtime host online/);
    assert.match(html, /MiniMax-M2\.7-highspeed/);
    assert.match(html, /Create session/);
    assert.match(html, /Apply workspace/);
    assert.match(html, /Startup Checks/);
    assert.match(html, /Session Templates/);
    assert.doesNotMatch(html, /Runtime preview/);
    assert.doesNotMatch(html, /Command readiness/);
  });

  it("lets the user select model, edit workspace, and create a session", async () => {
    const user = userEvent.setup();
    const onSelectModel = vi.fn();
    const onSessionTitleChange = vi.fn();
    const onWorkspacePathChange = vi.fn();
    const onOpenWorkspace = vi.fn();
    const onCreateSession = vi.fn();

    function Harness() {
      const [workspacePath, setWorkspacePath] = useState("D:\\py\\yuanbao_agent");
      const [sessionTitle, setSessionTitle] = useState("Investigate frontend");

      return (
        <NewSessionWorkspace
          workspacePath={workspacePath}
          hostStatusText="Runtime host online"
          sessionTitle={sessionTitle}
          modelLabel="MiniMax-M2.7-highspeed"
          modelOptions={[
            { id: "minimax", label: "MiniMax-M2.7-highspeed" },
            { id: "kimi", label: "Kimi" },
          ]}
          selectedModelId="minimax"
          onSelectModel={onSelectModel}
          onSessionTitleChange={(nextTitle) => {
            setSessionTitle(nextTitle);
            onSessionTitleChange(nextTitle);
          }}
          onWorkspacePathChange={(nextPath) => {
            setWorkspacePath(nextPath);
            onWorkspacePathChange(nextPath);
          }}
          onOpenWorkspace={onOpenWorkspace}
          onCreateSession={onCreateSession}
        />
      );
    }

    render(<Harness />);

    await user.selectOptions(screen.getByLabelText("Select model"), "kimi");
    await user.clear(screen.getByLabelText("Session title"));
    await user.type(screen.getByLabelText("Session title"), "Plan frontend V2");
    await user.clear(screen.getByLabelText("Workspace folder"));
    await user.type(screen.getByLabelText("Workspace folder"), "D:\\py\\doubao_client");
    await user.click(screen.getByRole("button", { name: "Apply workspace" }));
    await user.click(screen.getByRole("button", { name: "Create session" }));

    expect(onSelectModel).toHaveBeenCalledWith("kimi");
    expect(onSessionTitleChange).toHaveBeenLastCalledWith("Plan frontend V2");
    expect(onWorkspacePathChange).toHaveBeenLastCalledWith("D:\\py\\doubao_client");
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);
    expect(onCreateSession).toHaveBeenCalledTimes(1);
  });
});
