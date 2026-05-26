# Windowed-Mode Support — Feasibility Analysis

## 1  Current fullscreen assumptions

The codebase assumes that the WuWa game occupies the **entire monitor** from
pixel (0, 0).  This assumption is embedded in three places:

| Layer | File(s) | Assumption |
|-------|---------|------------|
| **Screenshot capture** | `game/screen.py` — `capture_full`, `capture_region` | The `mss` grab region starts at `monitors[i]['top']`, `monitors[i]['left']` — i.e. the top-left of the monitor, not the window. |
| **Mouse / keyboard input** | `game/input_controller.py` — `move`, `click` | Absolute screen coordinates are computed as `x + monitor['left']`, `y + monitor['top']`.  This maps game-relative coordinates directly to monitor-relative coordinates, which only works when the two coincide. |
| **ROI coordinates** | `game/game_roi.py` — `COORDINATES` dict | Every `Coordinates(x, y, w, h)` value is expressed as an offset from the **top-left of the game viewport**.  In fullscreen this is the same as the monitor origin; in windowed mode these offsets must be applied relative to the window's **client-area** origin. |

No other code (OCR, assemblers, validators, scanning workflows) makes
direct screen-position assumptions — they all receive pre-cropped images.

---

## 2  Challenges

### 2.1  Client-area geometry

Win32 distinguishes the **window rect** (including title bar + borders) from
the **client rect** (the drawable area the game renders into).

```
┌─── window rect ─────────────────────────────┐
│ ╔═══ title bar ═══════════════════════════╗  │
│ ║                                         ║  │
│ ║  ┌─── client rect (game viewport) ──┐   ║  │
│ ║  │                                   │   ║  │
│ ║  │                                   │   ║  │
│ ║  └───────────────────────────────────┘   ║  │
│ ╚═════════════════════════════════════════╝  │
└──────────────────────────────────────────────┘
```

`pywinctl` reports the outer window rect.  The difference between the outer
and inner rect depends on:

* Windows theme and DWM composition (classic borders vs. modern DWM frame).
* DPI scaling level — border and title bar sizes are in physical pixels.
* Whether the game uses `WS_POPUP` (borderless windowed) or a standard
  caption style.

**Impact:**  
- All ROI coordinates and input offsets must be shifted by the client-area
  origin, not the window origin.
- `ScreenLayout` (and the `COORDINATES` table) must be resolved against the
  **client width × client height**, which is smaller than the full window
  dimensions.

**Mitigation:**  
- Use `win32gui.GetClientRect(hwnd)` + `win32gui.ClientToScreen(hwnd, (0, 0))`
  to compute the client-area origin and size at scan time.
- Expose `client_origin` and `client_size` properties on `GameWindow` and use
  them everywhere instead of the monitor origin.

### 2.2  Window position is not fixed

In fullscreen the game always occupies the same pixels.  In windowed mode the
user (or the OS) can **move or resize** the window at any time — even during a
scan.

**Impact:**  
- A stale cached position makes both screenshots and mouse input land on the
  wrong pixels.
- Scanning a 4-page echo inventory (≈ 96 cells × ~200 ms each) takes
  roughly 20 seconds; the position could change mid-sweep.

**Mitigation:**  
- Re-query `ClientToScreen` before every `capture_full` and before every
  `InputController.click`.
- The cost per call is negligible (a single Win32 syscall) but adds code
  complexity.  Alternatively, query once per grid cell (every ~200 ms).
- Consider pinning the foreground (`SetForegroundWindow` + `SetWindowPos`
  `TOPMOST`) for the scan duration and warning users not to move the window.

### 2.3  Screenshot occlusion

`mss` does **screen-space** captures — it grabs whatever pixels are on the
display, including overlapping windows.  In fullscreen nothing can overlap.
In windowed mode this is no longer guaranteed.

**Impact:**  
- A notification, taskbar tooltip, or misplaced tool window overlapping the
  game viewport corrupts the screenshot, leading to OCR failures.

**Mitigation options** (from easiest to hardest):

| Option | Pros | Cons |
|--------|------|------|
| Keep using `mss` + warn users to keep game unobscured | Zero code change | Fragile; one stray tooltip breaks a scan |
| `win32gui.SetWindowPos(HWND_TOPMOST)` for scan duration | Low effort | Aggressive; may surprise the user |
| `PrintWindow(hwnd)` | Captures the window's own rendering buffer; immune to occlusion | Some games (including GPU-accelerated ones) return a black frame.  WuWa uses DirectX — needs testing. |
| `IDXGIOutputDuplication` (DXGI Desktop Dup) | Fast GPU-side copy; same pixels as mss but API-level | More complex; does not help with occlusion (still screen-space) |
| **BitBlt with `PW_RENDERFULLCONTENT`** | Best chance for non-occluded DX capture | Win10 1903+; must be tested with WuWa specifically |

**Recommendation:** Start by testing `PrintWindow` / `PW_RENDERFULLCONTENT`
with the WuWa process.  If it returns a valid frame, adopt it; otherwise,
fall back to `mss` + `TOPMOST` during scans.

### 2.4  Resolution and aspect-ratio variability

In fullscreen the resolution matches a monitor native mode (typically
1920 × 1080 or a standard 16:9 variant).  `COORDINATES` has hand-tuned
entries for common resolutions and a scaling fallback for unknown ones.

In windowed mode:
- The client area can be **any** size, including non-standard ones
  (e.g. 1600 × 924 after subtracting a title bar from 1600 × 900 display).
- The game's internal render resolution may differ from the window size if
  WuWa applies its own scaling.
- Very small windows (e.g. 1280 × 720 or less) reduce UI-element pixel sizes,
  which degrades OCR accuracy.

**Impact:**  
- The coordinate-scaling code in `ScreenInfo._scaleScreen` already handles
  unknown resolutions by proportionally scaling from the closest reference.
  This should generalize, but accuracy degrades for resolutions far from any
  reference.
- OCR on small crops (sub-20 px text height) is unreliable with both
  RapidOCR and Tesseract.

**Mitigation:**  
- Enforce a **minimum client-area size** (e.g. 1280 × 720) at scan start and
  abort with a clear error if the window is too small.
- Optionally upscale crops before OCR (2× bicubic) when the render height is
  below a threshold — trades CPU time for accuracy.

### 2.5  DPI interaction

`GameWindow.dpi_scale` already divides the reported window size by the DPI
factor.  In fullscreen this produces the logical game resolution.  In windowed
mode, DPI affects:

* **Window rect reporting** — at 150 % DPI, `pywinctl.width` may return
  physical or logical pixels depending on the DPI-awareness of the process.
* **Client-area coordinates** — `ClientToScreen` returns physical pixels.
* **Mouse input** — `SetCursorPos` takes physical-pixel screen coordinates.

If the scanner process and the game have **different DPI-awareness modes**,
coordinate translations silently shift by the scaling factor.

**Mitigation:**  
- Declare the scanner process as Per-Monitor DPI Aware v2
  (`SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)`)
  at startup.  This ensures all Win32 APIs return physical pixels
  consistently.
- WuWa itself is DPI-unaware on most configurations; confirm with
  `GetWindowDpiAwarenessContext(hwnd)` at runtime and adjust if needed.

### 2.6  Game-specific behaviour differences

WuWa may behave differently in windowed mode:

- **UI scaling:** The game may re-layout its UI to fit a smaller viewport,
  changing element positions in ways the linear `_scaleScreen` model does
  not predict.
- **Alt-Tab / focus loss:** In fullscreen-exclusive mode the game
  minimises on focus loss; in windowed mode it stays visible but reports
  `isActive = False`.  The scanner must handle this gracefully.
- **Borderless windowed vs. true windowed:** Many players use "borderless
  windowed" (`WS_POPUP` at monitor size), which is geometrically
  identical to fullscreen and would "just work" if we add a client-area
  offset path.  Only **true** windowed (with a title bar) introduces all
  the challenges above.

---

## 3  Affected modules — change map

| Module | Change scope | Difficulty |
|--------|-------------|------------|
| `game/screen.py` — `GameWindow` | Add `client_origin`, `client_size` properties; replace monitor-origin math in `capture_*` with client-area origin | Medium |
| `game/screen.py` — `ScreenLayout` | Accept client width × height instead of window width × height | Low (already resolution-agnostic) |
| `game/input_controller.py` | Accept a dynamic offset (window position) instead of a static monitor origin; refresh before each action | Medium |
| `game/navigation.py` | No direct changes — consumes layout + controller | None |
| `scraping/scanning/*` | No changes — consumes pre-cropped images | None |
| `scraping/ocr/*` | No changes | None |
| `scraping/service/*` | No changes | None |
| `cli/scan.py` | Add `--windowed` flag or auto-detect; set minimum resolution guard | Low |

Total estimated change: ~150–250 lines of new/modified code, concentrated in
`screen.py` and `input_controller.py`.

---

## 4  Implementation sketch

### 4.1  `GameWindow` additions

```python
@property
def client_origin(self) -> tuple[int, int]:
    """Top-left of the game **client area** in physical screen pixels."""
    import win32gui
    x, y = win32gui.ClientToScreen(self._window._hWnd, (0, 0))
    return (x, y)

@property
def client_size(self) -> tuple[int, int]:
    """Width and height of the client area in physical pixels."""
    import win32gui
    _, _, w, h = win32gui.GetClientRect(self._window._hWnd)
    return (w, h)
```

### 4.2  `capture_full` / `capture_region` changes

Replace the monitor-origin grab region with:

```python
def capture_full(gw: GameWindow) -> np.ndarray:
    cx, cy = gw.client_origin
    cw, ch = gw.client_size
    region = {'top': cy, 'left': cx, 'width': cw, 'height': ch}
    raw = _mss().grab(region)
    return np.array(raw)[:, :, :3]
```

### 4.3  `InputController` offset injection

```python
class InputController:
    def __init__(self, get_origin: Callable[[], tuple[int, int]]) -> None:
        self._get_origin = get_origin   # e.g. lambda: gw.client_origin
        ...

    def move(self, x, y, wait=0.1):
        ox, oy = self._get_origin()
        self._w32.SetCursorPos((int(x) + ox, int(y) + oy))
        ...
```

This makes the offset **dynamic** — each call re-queries the window
position — with zero overhead from the callers' perspective.

---

## 5  Testing strategy

| Test | Approach |
|------|----------|
| **Client-area geometry** | Unit-test `client_origin` / `client_size` with a mock hwnd returning known rects. |
| **Coordinate accuracy** | Place the game in windowed mode at a known position.  Perform a single-cell scan and assert that the OCR regions match expected crops vs. a reference fullscreen capture shifted by the same offset. |
| **Occlusion** | Overlap a dummy window and run a scan with `mss` vs. `PrintWindow`.  Score OCR outputs to quantify degradation. |
| **Resolution scaling** | Run a scan at 1920 × 1080 (windowed, maximised) and at 1280 × 720 (small window).  Compare echo-result accuracy. |
| **Move-during-scan** | Script a window move mid-scan (via `MoveWindow`).  Verify that the scanner's per-action re-query keeps coordinates correct. |

---

## 6  Risk summary

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|------------|
| `PrintWindow` returns black frame for WuWa | Medium | High — forces `mss` + TOPMOST fallback | Test early; gate the feature on the result |
| Game UI re-layouts in windowed mode | Low–Medium | High — breaks all ROI coordinates | Manual QA at several resolutions before release |
| DPI mismatch between scanner and game | Low | Medium — input/capture offset by scale factor | Enforce Per-Monitor DPI Aware v2 at startup |
| User moves window during scan | Medium | Low — single cell mis-captured, then self-corrects | Re-query origin per cell; warn in UI |
| Small window degrades OCR | Medium | Medium — substats misread | Enforce minimum 1280 × 720 client area |

---

## 7  Conclusion

Supporting true windowed mode is **feasible** with a moderate engineering
effort, primarily concentrated in two files (`screen.py`,
`input_controller.py`).  The scanning workflows, OCR pipeline, and
assemblers are already decoupled from screen positioning and require no
changes.

The main unknowns are:

1. Whether `PrintWindow` / `PW_RENDERFULLCONTENT` produces valid frames for
   WuWa's DirectX renderer — this should be tested before committing to an
   implementation plan.
2. Whether the game's UI scaling in very small windows diverges from the
   linear model in `ScreenInfo._scaleScreen`.

A reasonable approach is:

1. **Spike:** Test `PrintWindow` with the live game (< 1 hour).
2. **Phase 1:** Add `client_origin` / `client_size` to `GameWindow` and
   wire them through `capture_*` and `InputController`.  Gate behind a
   `--windowed` flag.
3. **Phase 2:** Add minimum-resolution guard and optional crop upscaling.
4. **Phase 3:** QA across 3–4 window sizes and two DPI levels.

Borderless windowed mode (which many players already use) would likely work
with Phase 1 alone, since the client area matches the monitor.
