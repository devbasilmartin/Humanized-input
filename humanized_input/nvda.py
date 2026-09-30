"""Talking to a real NVDA screen reader from Python (Windows).

Two ways to put NVDA in the loop:

1. Let NVDA listen. Start NVDA, then run a desktop session. Our keystrokes
   are real (SendInput), so NVDA announces focus changes exactly as it would
   for a person. Open NVDA's Tools > Speech Viewer to see its text next to
   our own transcript; the differences are a good way to learn how NVDA
   decides what to say.

2. Make NVDA speak our transcript with NvdaSpeech, using NVDA's own voice and
   speech rate settings. This needs the NVDA Controller Client DLL
   (nvdaControllerClient.dll, 64-bit Python needs the x64 build), which is
   published with NVDA's source/releases as "controllerClient".
"""

from __future__ import annotations

import ctypes
import sys

from .screen_reader import Speech


class NvdaSpeech(Speech):
    def __init__(self, dll_path: str = "nvdaControllerClient.dll", interrupt: bool = False):
        if sys.platform != "win32":
            raise OSError("NVDA runs on Windows only")
        self.dll = ctypes.windll.LoadLibrary(dll_path)
        self.dll.nvdaController_speakText.argtypes = (ctypes.c_wchar_p,)
        if self.dll.nvdaController_testIfRunning() != 0:
            raise RuntimeError("NVDA is not running")
        self.interrupt = interrupt

    def say(self, text: str) -> None:
        if self.interrupt:
            self.dll.nvdaController_cancelSpeech()
        self.dll.nvdaController_speakText(text)

    def braille(self, text: str) -> None:
        """Show a message on a connected braille display."""
        self.dll.nvdaController_brailleMessage.argtypes = (ctypes.c_wchar_p,)
        self.dll.nvdaController_brailleMessage(text)
