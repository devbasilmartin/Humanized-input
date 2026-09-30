# Humanized Input

A study project for writing Python scripts that **use web pages the way screen
reader users do**: they navigate only by what they hear, they follow a
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

## Setup

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

## How it works (read the code in this order)

| File | Concept |
|---|---|
| [`profiles.py`](humanized_input/profiles.py) | What makes users different: typing speed, speech rate, how much they listen before skipping, reaction/think time, navigation strategy. |
| [`timing.py`](humanized_input/timing.py) | Log-normal keystroke intervals, typos on neighbouring keys with delayed noticing and backspacing, listening time from speech rate. A `VirtualClock` adds up time without waiting; `RealClock` really waits. |
| [`ax.py`](humanized_input/ax.py) | Reads Chromium's **accessibility tree** over the DevTools Protocol. This is what screen readers actually consume, not the HTML. |
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
- **Windows desktop apps:** `pywinauto` with the UIA backend or `comtypes` +
  UI Automation gives you the same role/name/state tree for native apps.
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
