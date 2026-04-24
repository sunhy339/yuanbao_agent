import { strict as assert } from "node:assert";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, it } from "vitest";
import { NewSessionWorkspace } from "./NewSessionWorkspace";

describe("NewSessionWorkspace", () => {
  it("renders a minimal modern session start desk", () => {
    const html = renderToStaticMarkup(
      <NewSessionWorkspace
        workspacePath={"D:\\py\\yuanbao_agent"}
        hostStatusText="Runtime host online"
      />,
    );

    assert.match(html, /New Session/);
    assert.match(html, /Type a task in the command bar below/);
    assert.match(html, /D:\\py\\yuanbao_agent/);
    assert.match(html, /Runtime host online/);
    assert.doesNotMatch(html, /Runtime preview/);
    assert.doesNotMatch(html, /session idle/);
    assert.doesNotMatch(html, /Command readiness/);
    assert.doesNotMatch(html, /Recent sessions live in the session ledger/);
    assert.doesNotMatch(html, /Settings/i);
    assert.doesNotMatch(html, /Scheduled/i);
  });
});
