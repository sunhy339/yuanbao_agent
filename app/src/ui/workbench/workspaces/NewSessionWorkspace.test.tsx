import { strict as assert } from "node:assert";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, it } from "vitest";
import { NewSessionWorkspace } from "./NewSessionWorkspace";

describe("NewSessionWorkspace", () => {
  it("renders new-session metadata without unrelated workspace labels", () => {
    const html = renderToStaticMarkup(
      <NewSessionWorkspace
        workspacePath={"D:\\py\\yuanbao_agent"}
        hostStatusText="Runtime host online"
      />,
    );

    assert.match(html, /New Session/);
    assert.match(html, /D:\\py\\yuanbao_agent/);
    assert.match(html, /Runtime host online/);
    assert.doesNotMatch(html, /Settings/i);
    assert.doesNotMatch(html, /Scheduled/i);
  });
});
