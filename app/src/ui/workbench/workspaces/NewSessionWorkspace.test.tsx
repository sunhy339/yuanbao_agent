import { strict as assert } from "node:assert";
import "@testing-library/jest-dom/vitest";
import { open } from "@tauri-apps/plugin-dialog";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NewSessionWorkspace } from "./NewSessionWorkspace";

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

afterEach(() => {
  vi.mocked(open).mockReset();
  cleanup();
});

describe("NewSessionWorkspace", () => {
  it("renders a compact new-session empty state", () => {
    const html = renderToStaticMarkup(
      <NewSessionWorkspace
        workspacePath={"D:\\py\\yuanbao_agent"}
        hostStatusText="Runtime host online"
        sessionTitle="Frontend V2"
        modelLabel="MiniMax-M2.7-highspeed"
        modelOptions={[{ id: "minimax", label: "MiniMax-M2.7-highspeed" }]}
      />,
    );

    assert.match(html, /新建会话/);
    assert.match(html, /开始一个新的编码会话/);
    assert.match(html, /D:\\py\\yuanbao_agent/);
    assert.match(html, /Runtime host online/);
    assert.match(html, /MiniMax-M2\.7-highspeed/);
    assert.match(html, /创建会话/);
    assert.match(html, /应用工作区/);
    assert.match(html, /会话设置/);
    assert.doesNotMatch(html, /工作区启动器/);
    assert.doesNotMatch(html, /启动检查/);
    assert.doesNotMatch(html, /会话模板/);
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

    await user.click(screen.getByText("会话设置"));
    await user.selectOptions(screen.getByLabelText("选择模型"), "kimi");
    await user.clear(screen.getByLabelText("会话标题"));
    await user.type(screen.getByLabelText("会话标题"), "Plan frontend V2");
    await user.clear(screen.getByLabelText("工作区文件夹"));
    await user.type(screen.getByLabelText("工作区文件夹"), "D:\\py\\doubao_client");
    await user.click(screen.getByRole("button", { name: "应用工作区" }));
    await user.click(screen.getByRole("button", { name: "创建会话" }));

    expect(onSelectModel).toHaveBeenCalledWith("kimi");
    expect(onSessionTitleChange).toHaveBeenLastCalledWith("Plan frontend V2");
    expect(onWorkspacePathChange).toHaveBeenLastCalledWith("D:\\py\\doubao_client");
    expect(onOpenWorkspace).toHaveBeenCalledTimes(1);
    expect(onCreateSession).toHaveBeenCalledTimes(1);
  });

  it("opens the native folder picker and applies the selected workspace path", async () => {
    const user = userEvent.setup();
    const onWorkspacePathChange = vi.fn();
    vi.mocked(open).mockResolvedValue("D:\\picked-workspace");

    render(
      <NewSessionWorkspace
        workspacePath="D:\\py\\yuanbao_agent"
        hostStatusText="Runtime host online"
        onWorkspacePathChange={onWorkspacePathChange}
      />,
    );

    await user.click(screen.getByText("会话设置"));
    await user.click(screen.getByRole("button", { name: "浏览" }));

    expect(open).toHaveBeenCalledWith({
      directory: true,
      multiple: false,
      title: "选择工作区文件夹",
    });
    expect(onWorkspacePathChange).toHaveBeenCalledWith("D:\\picked-workspace");
  });

  it("shows visible feedback when the folder picker cannot open", async () => {
    const user = userEvent.setup();
    vi.mocked(open).mockRejectedValue(new Error("dialog plugin unavailable"));

    render(
      <NewSessionWorkspace
        workspacePath="D:\\py\\yuanbao_agent"
        hostStatusText="Runtime host online"
        onWorkspacePathChange={vi.fn()}
      />,
    );

    await user.click(screen.getByText("会话设置"));
    await user.click(screen.getByRole("button", { name: "浏览" }));

    expect(screen.getByRole("alert")).toHaveTextContent("无法打开系统文件夹选择器");
    expect(screen.getByRole("alert")).toHaveTextContent("dialog plugin unavailable");
  });
});
