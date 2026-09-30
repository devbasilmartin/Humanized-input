# Humanized Input

A study project for writing Python scripts that **use web pages and Windows
desktop apps the way screen reader users do**: they navigate only by what they hear, they follow a
**profile** (expert, novice, motor-impaired...), and they type and pause with
**human-like timing**.

The practical use is accessibility testing. Instead of asking "does this CSS
selector exist?", you ask "can a novice screen reader user sign up, and how
long does it take them?" Bugs like an unlabelled field show up as a user who
can't find something, rather than as a lint warning.

```
$ python examples/compare_profiles.py
Profile 'expert': 25.9s simulated
  [ok  ] fill 'Full name': 1 steps, 4.3s
  [ok  ] fill 'Email': 1 steps, 4.8s
  [ok  ] fill 'Library card': 21 steps, 13.0s  (quicknav failed; guessed from nearby text: field has no accessible name)
  ...
Profile 'novice': 76.6s simulated
  ...
Accessibility issues on the page:
  - [heading-skip] Heading jumps from level 1 to 3.  (heard as: "Your details, heading, level 3")
  - [unnamed-control] A textbox has no accessible name.  (heard as: "edit, blank")
  - [unnamed-control] A button has no accessible name.  (heard as: "button")
```

## Windows desktop apps

The same profiles, virtual screen reader and simulated user also drive real
Windows programs. On Windows the project reads **UI Automation** (the
accessibility API Narrator uses, and NVDA/JAWS use for modern apps) and
sends **real keystrokes** through `SendInput`, held down and spaced out like a
person's.

```powershell
pip install -r requirements.txt          # installs uiautomation on Windows
python examples\windows\signup_demo.py --speak                  # WinForms sign-up app, every profile
python examples\windows\notepad_demo.py --profile novice        # human typing in Notepad, a dialog announced
python examples\windows\explore_window.py --title "Calculator" --tab 10
```

Leave the keyboard alone while a script runs; the key presses are real. The
backend only types into the target app and pulls it back to the front if
focus wanders.

```python
from humanized_input import PrintSpeech
from humanized_input.desktop import desktop_session

with desktop_session("novice", title=r".*Notepad$", launch=["notepad.exe"], speech=PrintSpeech()) as user:
    user.type("Hello from a simulated user.")
    user.shortcut("Control+Shift+s")   # "Save As, dialog ..." gets read out
    user.shortcut("Escape")
```

Differences from the browser:

- Desktop apps have no browse mode, so there are no H/F/B quick-nav keys.
  The "expert" profile Tabs instead, and the virtual cursor acts like NVDA's
  object navigation for users who read line by line.
- When a window or dialog opens, its title and text are read, like a
  message box.
- The audit skips web-only rules (headings, landmarks) and adds "can this
  control be reached with the keyboard?".

**Using the real NVDA.** Start NVDA before running a desktop script. Because
the keystrokes are real, NVDA announces everything just as it would for a
person. Open *NVDA menu → Tools → Speech viewer* to compare what NVDA says with
our transcript; the differences are a good way to learn how NVDA works. To
have NVDA speak our transcript in its own voice, use `--nvda path\to\nvdaControllerClient.dll`
(see [`nvda.py`](humanized_input/nvda.py)).

**Limits.** Windows blocks a normal process from sending keys to an admin
(elevated) window, so run Python as admin to test admin apps. UAC prompts and
the lock screen cannot be driven at all. Tk apps expose very little to UI
Automation; WinForms, WPF, WinUI, Qt and Chromium/Electron apps expose a lot.

## Setup (browser)

```bash
pip install -r requirements.txt
playwright install chromium   # skip if you already have Chromium; or set CHROMIUM_PATH
python examples/compare_profiles.py --speak   # print everything the "screen reader" says
python -m pytest
```

Useful flags on the example: `--profiles expert novice`, `--real 4` (actually
wait, 4x faster than life), `--headed` (show the browser).

## Writing your own script

```python
from humanized_input import audit, load_profile, PrintSpeech
from humanized_input.session import user_session

with user_session("https://example.org/signup", load_profile("novice"), speech=PrintSpeech()) as user:
    user.read_page()                     # listen to the whole page once
    user.fill("Email", "me@example.org") # find the field by ear, then type
    user.check("I agree")
    user.press("Create account")
    print(user.report.summary())
    print(audit(user.sr.title, user.sr.items))
```

Custom profiles go in JSON or YAML; see [`profiles/example.yaml`](profiles/example.yaml).

## Mouse input

Low-vision, motor-impaired and sighted users use a mouse too. `user.click()`
moves the pointer the way a hand does and then clicks. It works in the
browser (Playwright's mouse) and on Windows (real `SendInput` mouse events).

```python
with user_session(url, load_profile("motor")) as user:
    user.click("Create account")             # find it on screen, move, click
    user.click("I agree", kind="field")
    user.click("Row 3", kind="any", clicks=2) # double click
    print(user.report.summary())             # "missed the target (12x12 px)" when it happens
```

```
$ python examples/target_sizes.py
profile            8px   12px   16px   24px   32px   44px   (hit rate from 700 px away)
expert           100%   100%   100%   100%   100%   100%
motor             55%    90%    92%    94%    95%    96%
```

How [`pointer.py`](humanized_input/pointer.py) models a hand:

1. **Fitts's law** sets the movement time, `a + b*log2(D/W + 1)`, so small,
   far targets take longer. `pointer_a_s` / `pointer_b_s` in the profile.
2. **Submovements.** The first fast move undershoots a little and scatters;
   short corrective moves follow after a pause until the pointer is on target.
3. **Minimum-jerk velocity.** Each move speeds up smoothly and slows to a stop.
4. **Curved paths.** Paths bow to one side (`pointer_curvature`).
5. **Click scatter.** Aim points spread around the centre with SD = W/4.133,
   the "effective width" convention from Fitts's law research (`pointer_spread`).
6. **Tremor** (`pointer_tremor_px`). A 4-8 Hz wobble whose size varies,
   and which continues while the button is held down. If the release lands off
   the target, the app never sees a click. That is a WCAG 2.5.8 (Target Size)
   failure, found the way a user runs into it.

Moves are sent as one report per 8 ms (a 125 Hz mouse), so the page gets a
normal stream of `mousemove`, `mousedown` and `mouseup` events.

## How it works (read the code in this order)

| File | Concept |
|---|---|
| [`profiles.py`](humanized_input/profiles.py) | What makes users different: typing speed, speech rate, how much they listen before skipping, reaction/think time, navigation strategy. |
| [`timing.py`](humanized_input/timing.py) | Log-normal keystroke intervals, typos on neighbouring keys with delayed noticing and backspacing, listening time from speech rate. A `VirtualClock` adds up time without waiting; `RealClock` really waits. |
| [`ax.py`](humanized_input/ax.py) | Reads Chromium's **accessibility tree** over the DevTools Protocol. This is what screen readers actually consume, not the HTML. |
| [`pointer.py`](humanized_input/pointer.py) | Mouse movement: Fitts's law timing, minimum-jerk submovements, corrective moves, curved paths, click scatter, tremor. |
| [`backends/`](humanized_input/backends/__init__.py) | The small interface (snapshot, focused, press) that lets the same user drive a browser ([`browser.py`](humanized_input/backends/browser.py)) or a Windows app ([`windows_uia.py`](humanized_input/backends/windows_uia.py): UI Automation to AXItems, [`win_input.py`](humanized_input/backends/win_input.py): SendInput keystrokes and mouse). |
| [`desktop.py`](humanized_input/desktop.py) | Launch or attach to a Windows app by window title. |
| [`screen_reader.py`](humanized_input/screen_reader.py) | A virtual screen reader: browse mode (virtual cursor, quick-nav keys H/F/B/K/D), focus mode (Tab), NVDA-style phrasing, live-region announcements, a transcript. |
| [`user.py`](humanized_input/user.py) | The simulated user. Finds things only by what it hears, with fallback to line-by-line reading and "guessing from nearby text" like real users. |
| [`audit.py`](humanized_input/audit.py) | Simple checks tied to what the user heard. |

### Key concepts to study

1. **The accessibility tree.** Every element gets a role, an accessible name and
   states. Open Chrome DevTools → Elements → Accessibility pane to see it.
   Read the W3C *Accessible Name and Description Computation* spec to learn
   where names come from (`<label>`, `aria-label`, `aria-labelledby`, alt text, content).
2. **Browse mode vs focus mode.** In browse mode, the screen reader eats your
   keystrokes (H = next heading). In focus mode, keys go to the page. Most
   confusion in screen reader testing comes from mixing these up.
3. **Navigation strategies.** WebAIM's *Screen Reader User Survey* shows most
   users navigate by headings first. That is why heading structure matters so much.
4. **Human timing.** Inter-key intervals are skewed: mostly fast, occasionally
   slow. Model them with a log-normal distribution, not a uniform random delay.
   Experienced users run speech at 300-450+ WPM and interrupt it constantly.
5. **Live regions.** `role="alert"` / `aria-live` content is spoken without
   the user moving there. Error messages that are not in a live region are
   silent to a screen reader user.

## Driving a real screen reader

This project uses a virtual screen reader so it runs anywhere, headless, and
repeatably. When you want to go further:

- **NVDA (Windows):** `nvdaControllerClient.dll` (ships with NVDA) lets Python
  make NVDA speak via `ctypes`. For checking what NVDA says, the
  *NVDA Remote* or a speech-logging add-on is the usual route; Guidepup
  (`@guidepup/guidepup`, Node) automates NVDA and VoiceOver end to end.
- **Orca (Linux):** Orca and every Linux app expose the AT-SPI tree; read it with
  `pyatspi` / `gi.repository.Atspi`. Speech goes through speech-dispatcher
  (`SpeechDispatcherSpeech` in `screen_reader.py` uses it).
- **Windows desktop apps:** built in (see above). To explore UI Automation by
  hand, use Microsoft's *Accessibility Insights for Windows* or *Inspect.exe*
  (Windows SDK); they show the same tree `windows_uia.py` reads.
- **macOS:** VoiceOver can be driven with AppleScript; the AX API is reachable
  through `pyobjc`.
- Real text-to-speech for the virtual reader: `Pyttsx3Speech` in
  `screen_reader.py` (`pip install pyttsx3`).

## Scope

The human-like timing here exists so tests reflect how long real people take
and how they make mistakes. It is meant for testing sites and apps you own
or are authorized to test. It is not designed for, and should not be used
for, getting around bot detection, CAPTCHAs, or rate limits, or for posing as
real people on services.
