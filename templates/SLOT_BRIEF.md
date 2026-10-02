# Shared brief for every graphic slot (read fully, then your slot's TASK.md)

You are building ONE transparent overlay animation for a branded video edit. Nothing else.
Work only inside your slot folder. Do not touch other slots, base.mp4, edl.json or anything else
in `{{EDIT_DIR}}`.

1. **Read the design system** `{{EDIT_DIR}}/brand/DESIGN.md`. It is binding: canvas, safe zone,
   palette (exact hex), fonts, logo rules, motion. Your `TASK.md` gives the window, duration, layout
   zone and the brief.
2. **Read `words.txt`** — what is said during your slot, in seconds from slot start. Timings are
   approximate (±0.15 s; long words are often listed late). Land each reveal ~0.15 s before its word.
3. **Look at `refs/*.jpg`** — the footage your overlay sits on (half scale). Don't cover the face
   unless the layout says so (full / split).
4. **Engine: HyperFrames** (HTML + CSS + GSAP rendered frame-by-frame). If the `hyperframes`,
   `hyperframes-core` and `hyperframes-animation` skills are available, load them for authoring rules.
   Scaffold in your slot folder:

   ```bash
   cd <slot_dir> && HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes init . --example blank --non-interactive
   ```

   - Composition at the exact canvas size and fps from TASK.md, duration EXACTLY the slot duration.
   - Transparent background: `html`, `body` and the root element all `background: transparent`.
   - Brand: `<link rel="stylesheet" href="brand.css">`; colours via `var(--brand-…)`, fonts via
     `var(--font-heading)` etc. Logos from `logos/` — never redraw, recolour or re-letter a logo.
   - **Assert the brand font loaded before anything renders** (DESIGN.md has the exact
     `document.fonts.check(...)` line). A silent fallback face is a failed deliverable.
   - One paused GSAP timeline, seek-safe, deterministic. Ease out, never linear. One new element at a
     time. First and last frame fully transparent.
5. **Lint and render**

   ```bash
   npx --yes hyperframes lint .
   npx --yes hyperframes render . --format mov -o render.mov      # ProRes 4444 with alpha
   ```

   For a quick look while iterating add `--quality draft`; the final render uses the default quality.
6. **QA — do not skip**

   ```bash
   uv run --project {{SKILL_DIR}} {{SKILL_DIR}}/helpers/slots.py check <slot_dir>
   ```

   It verifies size / fps / duration / alpha / transparent first+last frame / safe-zone spill and
   writes `check_sheet.jpg`: your render over the real footage with the safe zone (red) and caption
   band (yellow). Open it with the Read tool and look: everything inside the red box, the caption band
   quiet, text legible at phone size, the brand font actually used, logos crisp, timing sensible.
   Pull 2–3 full-size frames at key moments if anything is doubtful. Fix and re-render until it is
   genuinely good (max 3 passes).
7. **Write `NOTES.md`**: what it shows, its timeline in slot seconds, any deviation from the brief.
8. **Reply** with: the absolute path to `render.mov`, the check line, and a 3-line description of the
   timeline.

Do not ask questions. If anything is ambiguous, pick the most obvious interpretation and proceed.

Anti-list: no extra titles, watermarks or URLs; no stats or claims beyond what the brief gives; no
third-party logos unless the brief supplies the file; no drawing your own version of the brand logo;
no linear easing; nothing outside the safe zone; no emoji unless the brief asks; no colours outside
the palette (tints of palette colours are fine).
