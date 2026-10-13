/**
 * The Film component: controls, captions in a live region, scene jumps, the reduced-motion storyboard and the
 * chrome-less export mode. jsdom has no IntersectionObserver or ResizeObserver; the component guards for both.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";

import { Film } from "../../src/components/film/Film.tsx";
import { SCENES, TOTAL_MS } from "../../src/components/film/timeline.ts";

afterEach(() => {
  delete window.__domeFilm;
});

function filmTime(): number {
  return Number(document.querySelector("[data-film-time]")?.getAttribute("data-film-time"));
}

describe("Film", () => {
  it("renders the stage with play/pause, a progress bar, scene chips and the caption in a live region", () => {
    render(<Film reducedMotion={false} />);
    expect(screen.getByRole("group", { name: "DoMe demo film" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play the film" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Film position" })).toHaveAttribute("max", String(TOTAL_MS));
    const chips = within(screen.getByRole("list", { name: "Scenes" })).getAllByRole("button");
    expect(chips.map((c) => c.getAttribute("aria-label"))).toEqual(SCENES.map((s) => `Scene ${s.n}: ${s.title}`));
    expect(chips[0]).toHaveAttribute("aria-current", "true");
    const live = screen.getByRole("status");
    // silent until the visitor starts the film: an autoplaying film never talks over the page
    expect(live).toHaveAttribute("aria-live", "off");
    expect(live).toHaveTextContent("Your PC, in your hand.");
    expect(live).toHaveTextContent("Remote control · Available now, free");
    expect(screen.getByRole("group", { name: "DoMe demo film" })).toHaveAttribute("aria-describedby", live.id);
    expect(filmTime()).toBe(0);
  });

  it("shows scene 1's lit room as the poster before playback, and announces captions once played", async () => {
    render(<Film reducedMotion={false} />);
    // the clock reads 0, but the stage draws scene 1's still (the phone has arrived, the caption is up)
    expect(filmTime()).toBe(0);
    expect(document.querySelector(".film-canvas [data-film-beat]")).toHaveTextContent(/Tap Pause on the phone/);
    await userEvent.click(screen.getByRole("button", { name: "Play the film" }));
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("makes the end card's calls to action real links that navigate, without toggling the film", async () => {
    function Where() {
      return <p data-testid="where">{useLocation().pathname}</p>;
    }
    render(
      <MemoryRouter initialEntries={["/demo"]}>
        <Routes>
          <Route path="/demo" element={<Film reducedMotion={false} initialTime={64_000} />} />
          <Route path="/app" element={<Where />} />
          <Route path="/pricing" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: /Compare plans/ })).toHaveAttribute("href", "/pricing");
    const open = screen.getByRole("link", { name: /Open the app/ });
    expect(open).toHaveAttribute("href", "/app");
    await userEvent.click(open);
    expect(screen.getByTestId("where")).toHaveTextContent("/app");
  });

  it("jumps to a scene from its chip and shows that scene's caption and honest status chip", async () => {
    render(<Film reducedMotion={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Scene 3: Screen view" }));
    expect(filmTime()).toBe(SCENES[2]!.start);
    const live = screen.getByRole("status");
    expect(live).toHaveTextContent("See your PC. Tap what you see.");
    expect(live).toHaveTextContent("Screen view · Coming");
    expect(live).toHaveAttribute("aria-live", "polite");
    expect(screen.getByRole("button", { name: "Scene 3: Screen view" })).toHaveAttribute("aria-current", "true");
    await userEvent.click(screen.getByRole("button", { name: "Scene 4: Point-and-talk" }));
    expect(live).toHaveTextContent("Point-and-talk · Planned");
    expect(live).toHaveTextContent("the AI never sees your screen");
    await userEvent.click(screen.getByRole("button", { name: "Scene 5: Pick your plan" }));
    expect(live).toHaveTextContent("Pro and Pro Plus arrive at paid launch");
  });

  it("is keyboard operable: digits jump to scenes, Home goes back, the arrows scrub, Space toggles", async () => {
    render(<Film reducedMotion={false} />);
    const frame = screen.getByRole("group", { name: "DoMe demo film" });
    frame.focus();
    await userEvent.keyboard("4");
    expect(filmTime()).toBe(SCENES[3]!.start);
    await userEvent.keyboard("{ArrowRight}");
    expect(filmTime()).toBe(SCENES[3]!.start + 5_000);
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(filmTime()).toBe(SCENES[3]!.start - 5_000);
    await userEvent.keyboard("{Home}");
    expect(filmTime()).toBe(0);
    await userEvent.keyboard(" ");
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Play the film" })).not.toBeInTheDocument();
    await userEvent.keyboard(" ");
    expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  });

  it("starts from a given time and scrubs with the progress bar", () => {
    render(<Film reducedMotion={false} initialTime={SCENES[4]!.still} />);
    expect(filmTime()).toBe(SCENES[4]!.still);
    expect(screen.getByRole("button", { name: "Scene 5: Pick your plan" })).toHaveAttribute("aria-current", "true");
    // scene 5's still shows the three plan cards with their honest status lines and prices
    expect(screen.getAllByText("Coming at paid launch")).toHaveLength(2); // Pro and Pro Plus
    expect(screen.getByText("Available now")).toBeInTheDocument();
    expect(screen.getByText("$9.99")).toBeInTheDocument();
    expect(screen.getByText("$24.99")).toBeInTheDocument();
    const slider = screen.getByRole("slider", { name: "Film position" });
    act(() => {
      // a controlled range input: React listens to the native input event
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(slider, String(SCENES[1]!.start));
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(filmTime()).toBe(SCENES[1]!.start);
    expect(slider).toHaveAttribute("aria-valuetext", expect.stringContaining("scene 2: Just ask"));
  });

  it("shows a storyboard of stills with Previous / Next when motion is reduced, and can switch to the animation", async () => {
    render(<Film reducedMotion />);
    expect(screen.getByRole("group", { name: "Storyboard, scene 1 of 6: Across the room" })).toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Play" })).not.toBeInTheDocument();
    expect(filmTime()).toBe(SCENES[0]!.still);
    expect(screen.getByRole("button", { name: "Previous scene" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Next scene" }));
    expect(screen.getByRole("group", { name: "Storyboard, scene 2 of 6: Just ask" })).toBeInTheDocument();
    expect(filmTime()).toBe(SCENES[1]!.still);
    await userEvent.click(screen.getByRole("button", { name: "Scene 6: DoMe Remote" }));
    expect(screen.getByRole("button", { name: "Next scene" })).toBeDisabled();
    expect(filmTime()).toBe(SCENES[5]!.still);
    await userEvent.click(screen.getByRole("button", { name: "Play the animation" }));
    expect(screen.getByRole("group", { name: "DoMe demo film" })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Film position" })).toBeInTheDocument();
  });

  it("follows the device's reduced-motion preference when no override is given", () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query.includes("reduce"), media: query, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    try {
      render(<Film />);
      expect(screen.getByRole("group", { name: /^Storyboard/ })).toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });

  it("export mode hides every control and installs a deterministic handle for the export script", () => {
    render(<Film exportMode reducedMotion={false} layout="wide" />);
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Scenes" })).not.toBeInTheDocument();
    const frame = screen.getByRole("group", { name: "DoMe demo film" });
    expect(frame).toHaveAttribute("tabindex", "-1");
    expect(frame).toHaveAttribute("data-layout", "wide");
    const handle = window.__domeFilm;
    expect(handle).toBeDefined();
    expect(handle!.total).toBe(TOTAL_MS);
    expect(handle!.scenes.map((s) => s.n)).toEqual([1, 2, 3, 4, 5, 6]);
    act(() => handle!.seek(SCENES[3]!.still));
    expect(filmTime()).toBe(SCENES[3]!.still);
    expect(handle!.time()).toBe(SCENES[3]!.still);
    expect(screen.getByRole("status")).toHaveTextContent("Point-and-talk");
    // export draws the end card's buttons as pictures, never as links
    act(() => handle!.seek(64_000));
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    act(() => handle!.seek(TOTAL_MS + 5_000));
    expect(filmTime()).toBe(TOTAL_MS);
    // the captions are still rendered (the export must carry them)
    expect(screen.getByRole("status")).toHaveTextContent("Your PC, in your hand.");
  });

  it("restacks to the vertical layout when asked", () => {
    render(<Film reducedMotion={false} layout="vertical" />);
    expect(screen.getByRole("group", { name: "DoMe demo film" })).toHaveAttribute("data-layout", "vertical");
  });
});
