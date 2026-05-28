import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CleanRuntimeBlock } from "./CleanConversation";

afterEach(() => cleanup());

describe("CleanConversation", () => {
  it("does not treat patch titles as changed file paths", () => {
    render(
      <CleanRuntimeBlock
        item={{
          id: "patch:1",
          kind: "patch",
          title: "Update snake_game/README.md",
          status: "applied",
          code: "Update snake_game/README.md\nmodified snake_game/README.md (+2/-4)\nmodified snake_game/rules.py (+0/-2)",
        }}
      />,
    );

    expect(screen.getByText("snake_game/README.md")).toBeInTheDocument();
    expect(screen.getByText("snake_game/rules.py")).toBeInTheDocument();
    expect(screen.queryByText("Update snake_game/README.md", { selector: "code" })).not.toBeInTheDocument();
  });
});
