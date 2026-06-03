import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CleanCapabilityNotes } from "./CleanCapabilityNotes";

afterEach(() => {
  cleanup();
});

describe("CleanCapabilityNotes", () => {
  it("shows Yuanbao-facing capability copy", () => {
    render(<CleanCapabilityNotes />);

    expect(screen.getByRole("heading", { name: "Yuanbao 能力接入记录" })).toBeInTheDocument();
    expect(screen.getByLabelText("Yuanbao 能力接入记录")).toBeInTheDocument();
    expect(screen.queryByText(/haha-cc/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/haha 能力接入记录/i)).not.toBeInTheDocument();
  });
});
