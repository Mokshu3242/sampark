"""
Sampark demo video maker. Fully automatic.

What it does:
  1. Makes a smooth neural voiceover for every scene (edge-tts).
  2. Opens the live site in Chromium, clicks through each scene and records it.
     A visible cursor moves smoothly, so viewers can follow the clicks.
  3. Lines the voice up with each scene, evens out the volume, and writes
     sampark_demo.mp4 plus sampark_demo.srt (subtitles).

Setup (once):
  pip install playwright edge-tts
  playwright install chromium
  winget install ffmpeg          (Windows)   or   brew install ffmpeg (macOS)

Run (from the repo root):
  set ACCESS_KEY=your-key                (PowerShell: $env:ACCESS_KEY="your-key")
  python scripts/make_demo_video.py --probe      # first: lists buttons, checks selectors
  python scripts/make_demo_video.py --headed     # full video, watch it work
  python scripts/make_demo_video.py --scenes 8,9 # test a few scenes only

Output goes to video_out/. Add video_out/ to .gitignore.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import edge_tts
from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

# ---------------------------------------------------------------- settings
SITE = os.environ.get("SAMPARK_URL", "https://sampark-flood.vercel.app")
API = os.environ.get("SAMPARK_API", "https://flood-watch-gn81.onrender.com")
_HERE = Path(__file__).resolve()
ROOT = next((p for p in _HERE.parents if (p / "docs").is_dir()), _HERE.parents[1])  # repo root
OUT = ROOT / "video_out"


def _load_access_key() -> str:
    """Use ACCESS_KEY from the shell, else read it from backend/.env (never printed)."""
    key = os.environ.get("ACCESS_KEY", "").strip()
    if key and key != "your-key":
        return key
    for env in (ROOT / "backend" / ".env", ROOT / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ACCESS_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""
W, H = 1920, 1080
PAD = 0.7                     # quiet gap after each scene's voice (seconds)

# Smooth, warm voices. Try another with --voice if you like:
#   en-US-AndrewMultilingualNeural (default), en-US-BrianMultilingualNeural,
#   en-GB-RyanNeural, en-IN-PrabhatNeural, en-US-AvaMultilingualNeural
VOICE = "en-IN-PrabhatNeural"   # Indian English. Female option: en-IN-NeerjaNeural
RATE = "-3%"                  # close to normal speed, still clear
MUSIC = True
GAP = 0.35                    # pause between sentences (seconds)

# Spoken spelling for hard names. Subtitles keep the real spelling.
SAY = {
    "Sentinel-1": "Sentinel One", "Sentinel-2": "Sentinel Two",
    "OpenStreetMap": "Open Street Map", "2026": "twenty twenty-six",
}

# Selectors. Each entry is a list. The first one that shows up wins.
# "text=..." matches visible text. Run --probe and fix these if a click misses.
CFG = {
    "map":          [".maplibregl-canvas", "canvas"],
    "unlock_input": ["input[placeholder='Access key']", "input[type=password]"],
    "unlock_btn":   ["button:has-text('Unlock')"],
    # The demo run, picked from the "Open a run" dropdown. Any part of the option
    # text or value works: the run ID, or e.g. "made 8 Oct, 23:03".
    # Set DEMO_RUN in the shell to override.
    "demo_run":     os.environ.get("DEMO_RUN", "20261008T063513-021f3e"),
    "run_select":   "select:not(:has(option[value='auto']))",   # skip the copilot language box
    "open_btn":     ["button:has-text('Open')"],
    "imagery":      "Show Sentinel-2 imagery",
    "slider":       [".compare-handle", "[class*='slider-handle']", "[class*='compare'] [class*='handle']"],
    "voice":        "Voice narration",
    "story_start":  ["text=Start story"],
    "story_stop":   ["text=Stop story", "text=Exit story", "text=Stop"],
    "uncertainty":  "Uncertainty",
    "village_name": ["text=Rasuwa Gadhi", "text=Timure"],
    "village_xy":   (0.56, 0.38),   # fallback click spot, as a share of the map box
    "card_close":   ["[aria-label='Close']", "text=×", "text=Close"],
    "repairs":      ["input.repair-check", "ol.repair input[type=checkbox]"],
    "chat_input":   ["textarea", "input[placeholder*='Ask' i]"],
    "chat_answer":  ["text=numbers traced to data", "[class*='assistant']", "[data-role='assistant']", "[class*='answer']"],
    "traced":       ["text=numbers traced to data"],
    "trace":        "Trace downstream from a click",
    "trace_xy":     (0.33, 0.22),
    "new_run":      ["text=Start a new run"],
    "run_button":   ["text=Run pipeline"],
    "stop_run":     ["text=Stop run"],
}

FIG = ROOT / "docs" / "figures"
BANNER = FIG / "readme_banner.png"
ERROR_MAPS = [FIG / "20261008T063513-021f3e_error_map.png",
              FIG / "20261008T063513-021f3e_baseline_error_map.png"]

# ---------------------------------------------------------------- narration
SCENES = [
    (1,  "Namaste. When a flood hits a mountain valley, rescue teams have one big question. "
         "Who can we still reach? This is Sampark. In Nepali, Sampark means connection."),
    (2,  "Here is our case study. In August 2026, floods and mud flows hit the Bhote Koshi and Trishuli valley in central Nepal. "
         "Most villages here have only one road, and it runs along the river. "
         "If one bridge breaks, a whole side valley is cut off. "
         "Sampark finds these villages using free satellite data. It also shows which road to fix first."),
    (16, "On the right side, you can see the summary of this run. "
         "It shows how many villages are cut off, how many buildings are hit, and how many roads and bridges were lost. "
         "Just below, the app compares this run with the official Copernicus map."),
    (3,  "First, how do we see the damage? During the monsoon, clouds can hide the valley for weeks. "
         "So we use radar from Sentinel-1. Radar can see through clouds, by day and by night. "
         "We compare one radar image from before the flood with one from after. "
         "The colour images you see here come from Sentinel-2. They let people check our result with their own eyes."),
    (4,  "Now let's play story mode. Sit back, watch, and listen."),
    (5,  "Next, let's see how the map is made. For every small patch of land, we measure how much the radar signal changed. "
         "A darker signal usually means water. A brighter signal means rough new mud and rock. "
         "Then we use a height map to remove mistakes, like water on top of a steep hill. "
         "Each area also gets a confidence level. This way, nobody mistakes a guess for a fact."),
    (17, "Every layer can be switched on and off. Here is the flood water. Here is the mud and debris. "
         "And here are the roads, with the cut roads shown in red."),
    (6,  "Now let's click on a village. The pink villages are cut off by road. "
         "This card shows satellite images from before and after the flood, and the buildings in the damaged area. "
         "Before the flood, help was only a few kilometres away. Now there is no road to this village at all."),
    (7,  "So how do we know which villages are cut off? We start with the road map from OpenStreetMap, made before the flood. "
         "We remove every road and bridge inside the damaged area. Then we check which villages can still reach the main roads."),
    (18, "You can also search for any village by name. Just type a few letters, and the map takes you there."),
    (8,  "So where should repair crews start? Sampark ranks each road repair by how many buildings it reconnects. "
         "Let's tick the first two repairs. Watch the villages come back online, one by one."),
    (19, "Below the repair plan, there is a table of the most affected villages. "
         "It shows each village's status, the buildings hit, and the distance to help before the flood."),
    (9,  "You can also ask questions. A local officer can type in Nepali or in English. "
         "Here we ask, in Nepali, how many villages are cut off. Then we ask why."),
    (10, "But can we trust these answers? The AI never reads the raw files. It can only use eight tools that read this run's data. "
         "Before an answer is shown, a checker matches every number to the data. If a number has no source, the answer is blocked. "
         "For Nepali, the numbers are locked before translation, so they cannot change."),
    (11, "You can also click anywhere on the map to see where the water flows. Sampark then shows the villages and roads along its path."),
    (12, "Sampark is not only for this valley. You can pick any area and any date. "
         "A cloud worker then runs the full analysis in about thirteen minutes, and you can watch its progress live."),
    (21, "Sampark also makes a one-page report, in English and in Nepali. "
         "A district office can print it, or share it on the phone with the field teams."),
    (13, "Finally, how good is it? We compared our map with the official Copernicus emergency map. We never used that map to build ours. "
         "In the wide valley near Bidur, we found 59 percent of the destroyed buildings. "
         "A simple method marks about 25 times more land than we do. "
         "In the narrow gorge, we miss most of the mud and rock. We found three reasons. "
         "The radar signal changes very little there. The mud strips are very thin. And the slopes are very steep. "
         "We show these limits openly. In a crisis, a map that is honest about what it cannot see is safer."),
    (14, "Everything runs on free services. There is a website, a server, and a cloud worker for the heavy work."),
    (15, "Sampark is an educational prototype, built only on open satellite data. It answers one simple question. "
         "After a flood, who can we still reach? Thank you for watching. Dhanyavaad."),
]


# ---------------------------------------------------------------- voice
def sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.?!])\s+", text) if p.strip()]


def spoken(text: str) -> str:
    for a, b in SAY.items():
        text = text.replace(a, b)
    return text


ENGINE = "edge"          # "edge" (Microsoft neural voices) or "gtts" (Google, Indian accent)


async def _edge(text: str, path: Path, voice: str) -> None:
    await edge_tts.Communicate(text, voice, rate=RATE).save(str(path))


def _gtts(text: str, path: Path, voice: str, male: bool = False) -> None:
    from gtts import gTTS
    lang = "ne" if voice.lower().startswith("ne") else "en"
    tmp = path.with_suffix(".g.mp3")
    gTTS(text, lang=lang, tld="co.in").save(str(tmp))       # co.in = Indian English accent
    # gTTS speaks a little slowly; speed it up a touch so it flows.
    # For a male voice, lower the pitch and the voice "size" together (rubberband).
    filt = "rubberband=pitch=0.78:formant=shifted,atempo=1.12" if male else "atempo=1.12"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-filter:a", filt, str(path)], check=True)
    tmp.unlink(missing_ok=True)


SAPI_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } |
     Where-Object { $_.Culture.Name -eq 'en-IN' -and $_.Gender -eq 'Male' } | Select-Object -First 1
if (-not $v) { Write-Error 'NO_EN_IN_MALE'; exit 3 }
$s.SelectVoice($v.Name)
$s.Rate = [int]$env:SAMPARK_RATE
$s.SetOutputToWaveFile($env:SAMPARK_OUT)
$s.Speak($env:SAMPARK_TEXT)
$s.Dispose()
"""


def _sapi(text: str, path: Path) -> None:
    """Windows' own Indian English male voice (Microsoft Ravi). Offline."""
    wav = path.with_suffix(".s.wav")
    env = dict(os.environ, SAMPARK_TEXT=text, SAMPARK_OUT=str(wav), SAMPARK_RATE="0")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", SAPI_PS], env=env, capture_output=True, text=True)
    if r.returncode != 0 or not wav.exists():
        if "NO_EN_IN_MALE" in (r.stderr or ""):
            raise SystemExit(
                "\nWindows has no Indian English male voice installed.\n"
                "Add it: Settings > Time & language > Language & region > Add a language >\n"
                "English (India), tick 'Speech'. Then run again. Or use --engine gtts-male.")
        raise RuntimeError(r.stderr.strip()[:300])
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), str(path)], check=True)
    wav.unlink(missing_ok=True)


PIPER_VOICE = os.environ.get("PIPER_VOICE", "en_US-ryan-high")   # male. Also: en_GB-alan-medium
_piper = {}


def _piper_model() -> Path:
    """Download the offline voice once (about 60 MB) into video_out/piper."""
    import urllib.request
    name = PIPER_VOICE
    lang, who, quality = name.split("-")
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang.split('_')[0]}/{lang}/{who}/{quality}/{name}"
    d = OUT / "piper"
    d.mkdir(parents=True, exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        f = d / f"{name}{ext}"
        if not f.exists():
            print(f"  downloading offline voice {f.name} (once)...")
            urllib.request.urlretrieve(base + ext + "?download=true", f)
    return d / f"{name}.onnx"


def _piper_say(text: str, path: Path) -> None:
    import wave
    from piper import PiperVoice
    if "v" not in _piper:
        _piper["v"] = PiperVoice.load(str(_piper_model()))
    wav = path.with_suffix(".p.wav")
    with wave.open(str(wav), "wb") as wf:
        _piper["v"].synthesize_wav(text, wf)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), str(path)], check=True)
    wav.unlink(missing_ok=True)


def _eleven(text: str, path: Path) -> None:
    """ElevenLabs. Key and voice come from env vars only, never from the code."""
    import urllib.error
    import urllib.request
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    vid = os.environ.get("ELEVEN_VOICE_ID", "").strip()
    if not key or not vid:
        raise SystemExit("Set ELEVENLABS_API_KEY and ELEVEN_VOICE_ID first (see the instructions).")
    body = json.dumps({
        "text": text,
        "model_id": os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2"),
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.15, "use_speaker_boost": True},
    }).encode()
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}?output_format=mp3_44100_128",
        data=body, method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            path.write_bytes(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        if e.code == 402:
            raise SystemExit(
                "\nElevenLabs: this voice needs a paid plan for the API.\n"
                "Free plan: make your own voice with Voice Design (it is yours, so the API allows it),\n"
                "or pick one of the default voices. Then set ELEVEN_VOICE_ID to that voice.")
        if e.code in (401, 403):
            raise SystemExit(f"ElevenLabs refused the key or voice ({e.code}): {msg}")
        if e.code == 429 or "quota" in msg.lower():
            raise SystemExit(f"ElevenLabs credits used up or too many requests: {msg}")
        raise RuntimeError(f"ElevenLabs {e.code}: {msg}")


# ---------- your own voice ----------
MYVOICE = OUT / "myvoice"
CLEAN = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1,"
         "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse,"
         "highpass=f=80,afftdn=nf=-25")


def own_key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:12]


def _own(text: str, path: Path, voice: str) -> None:
    src = MYVOICE / f"{own_key(text)}.wav"
    if src.exists():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af", CLEAN, str(path)], check=True)
        return
    print(f"  (no recording for: {text[:50]}... using Google Indian voice made deeper)")
    _gtts(text, path, voice, male=True)


def all_lines() -> list[tuple[str, str]]:
    """Every sentence the video speaks: (label, spoken text). Story lines come
    from the last recording (take.json), because the app makes them."""
    rows = []
    for sid, text in SCENES:
        for k, sent in enumerate(sentences(text)):
            rows.append((f"scene {sid}, line {k + 1}", spoken(sent)))
    take = OUT / "take.json"
    if take.exists():
        for n, (_, _, text, _) in enumerate(json.loads(take.read_text(encoding="utf-8"))["story"]):
            rows.append((f"story, line {n + 1}", spoken(text)))
    return rows


def record_voice(redo: bool = False) -> None:
    """A simple teleprompter. Shows each sentence; you read it into the mic."""
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        sys.exit("Recording needs one package. Run:  pip install sounddevice")
    import wave
    MYVOICE.mkdir(parents=True, exist_ok=True)
    rows = all_lines()
    todo = [r for r in rows if redo or not (MYVOICE / f"{own_key(r[1])}.wav").exists()]
    print(f"\n{len(rows)} sentences in the video, {len(todo)} to record.")
    if not (OUT / "take.json").exists():
        print("Note: story lines are not listed yet. Make one video first, then run this again to add them.")
    print("Tips: quiet room, mic 15 cm away, speak calmly. Enter = start, Enter = stop.\n")
    sr = 48000
    for n, (label, text) in enumerate(todo, 1):
        while True:
            print("=" * 70)
            print(f"[{n}/{len(todo)}]  {label}\n\n    {text}\n")
            cmd = input("Enter = record,  s = skip,  q = quit: ").strip().lower()
            if cmd == "q":
                return
            if cmd == "s":
                break
            frames = []
            stream = sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                    callback=lambda data, *_: frames.append(data.copy()))
            with stream:
                input("  ● recording... press Enter when done ")
            audio = np.concatenate(frames) if frames else np.zeros((1, 1), "int16")
            secs = len(audio) / sr
            print(f"  recorded {secs:.1f} s, playing back...")
            sd.play(audio, sr)
            sd.wait()
            ok = input("  Enter = keep,  r = redo: ").strip().lower()
            if ok == "r":
                continue
            with wave.open(str(MYVOICE / f"{own_key(text)}.wav"), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(audio.tobytes())
            break
    print("\nAll done. Now make the video with:  --engine own")


def export_script() -> Path:
    """Write every line to read, numbered, as one text file."""
    rows = all_lines()
    out = OUT / "voice_script.txt"
    lines = ["SAMPARK VOICE SCRIPT", "",
             "Read each line, then stay quiet for about 2 seconds before the next one.",
             "Made a mistake? Pause 2 seconds and read the same line again. The last try is kept.",
             "Do not read the numbers or the [labels].", ""]
    last = None
    for n, (label, text) in enumerate(rows, 1):
        head = label.split(",")[0]
        if head != last:
            lines += ["", f"[{head.upper()}]"]
            last = head
        lines.append(f"{n:3d}.  {text}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t.lower()).split())


def import_voice(src: Path, model_size: str = "small.en") -> None:
    """Cut one long recording into sentences and match each piece to the script.
    Uses the pauses to cut, and Whisper (offline) to know which line each piece is."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("Importing a long recording needs one package. Run:  pip install faster-whisper")
    from difflib import SequenceMatcher
    if not src.exists():
        sys.exit(f"File not found: {src}")
    MYVOICE.mkdir(parents=True, exist_ok=True)
    rows = all_lines()
    texts = [t for _, t in rows]

    # 1) find speech pieces between pauses
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(src), "-af",
                        "highpass=f=80,silencedetect=noise=-38dB:d=0.7", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    total = duration(src)
    if not ends or ends[0] > 0.05:
        starts = [0.0] + starts
    ends = ends + [total]
    pieces = []
    for a in starts:
        b = next((e for e in ends if e > a + 0.3), None)
        if b is not None and b - a > 0.4:
            pieces.append((max(0, a - 0.08), b + 0.12))
    print(f"Found {len(pieces)} spoken pieces in {total / 60:.1f} min of audio.")

    # 2) know what each piece says
    print(f"Loading the speech model ({model_size}, downloads once)...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    tmp = OUT / "audio" / "_piece.wav"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    heard = []
    for a, b in pieces:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
                        "-ac", "1", "-ar", "16000", str(tmp)], check=True)
        segs, _ = model.transcribe(str(tmp), language="en", beam_size=1, vad_filter=False)
        heard.append(" ".join(x.text for x in segs).strip())

    # 3) match pieces to script lines, in order. A retake of the same line replaces the earlier one.
    sim = lambda x, y: SequenceMatcher(None, _norm(x), _norm(y)).ratio()
    pos, got = 0, {}
    for (a, b), said in zip(pieces, heard):
        if not said:
            continue
        window = range(max(0, pos - 1), min(len(texts), pos + 4))
        best = max(window, key=lambda j: sim(said, texts[j]), default=None)
        if best is None or sim(said, texts[best]) < 0.5:
            print(f"  ? skipped a piece I could not match: \"{said[:60]}\"")
            continue
        got[best] = (a, b)
        pos = best + 1 if best >= pos else pos

    # 4) save each line as its own clip
    for j, (a, b) in got.items():
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(src),
                        "-ac", "1", "-ar", "48000", str(MYVOICE / f"{own_key(texts[j])}.wav")], check=True)
    missing = [rows[j][0] for j in range(len(texts)) if j not in got]
    print(f"\nMatched {len(got)} of {len(texts)} lines.")
    if missing:
        print("Missing (these will use the Google Indian voice, or record them with --record-voice):")
        for m in missing:
            print(f"   - {m}")
    print("\nNow make the video with:  --engine own")


def _http_post(url: str, body: bytes, headers: dict) -> bytes:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 402, 403):
            raise SystemExit(f"\nThe voice service refused the request ({e.code}): {msg}\n"
                             "Check the key (and region), or your free credits.")
        raise RuntimeError(f"{e.code}: {msg}")


def _sarvam(text: str, path: Path, voice: str) -> None:
    """Sarvam AI Bulbul: Indian voices made in India. Key from SARVAM_API_KEY."""
    key = os.environ.get("SARVAM_API_KEY", "").strip()
    if not key:
        raise SystemExit("Set SARVAM_API_KEY first.")
    lang = "hi-IN" if voice.lower().startswith("ne") else "en-IN"    # Hindi voice reads Nepali script well
    body = json.dumps({
        "text": text, "language_code": lang,
        "model": os.environ.get("SARVAM_MODEL", "bulbul:v2"),
        "speaker": os.environ.get("SARVAM_SPEAKER", "karun"),          # male: karun, abhilash, hitesh
        "pace": float(os.environ.get("SARVAM_PACE", "1.0")),
        "speech_sample_rate": 24000,
    }).encode()
    data = json.loads(_http_post("https://api.sarvam.ai/text-to-speech", body,
                                 {"api-subscription-key": key, "Content-Type": "application/json"}))
    wav = path.with_suffix(".sv.wav")
    wav.write_bytes(base64.b64decode("".join(data["audios"])))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), str(path)], check=True)
    wav.unlink(missing_ok=True)


def _azure(text: str, path: Path, voice: str) -> None:
    """Azure Speech: the same Prabhat voice as before, through the official free tier.
    Key from AZURE_SPEECH_KEY, region from AZURE_SPEECH_REGION (e.g. centralindia)."""
    from xml.sax.saxutils import escape
    key = os.environ.get("AZURE_SPEECH_KEY", "").strip()
    region = os.environ.get("AZURE_SPEECH_REGION", "").strip()
    if not key or not region:
        raise SystemExit("Set AZURE_SPEECH_KEY and AZURE_SPEECH_REGION first.")
    name = STORY_VOICE_NE if voice.lower().startswith("ne") else os.environ.get("AZURE_VOICE", "en-IN-PrabhatNeural")
    lang = "ne-NP" if name.startswith("ne") else "en-IN"
    ssml = (f"<speak version='1.0' xml:lang='{lang}'><voice name='{name}'>"
            f"<prosody rate='{RATE}'>{escape(text)}</prosody></voice></speak>")
    path.write_bytes(_http_post(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1", ssml.encode("utf-8"),
        {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
         "X-Microsoft-OutputFormat": "audio-24khz-96kbitrate-mono-mp3", "User-Agent": "sampark-demo"}))


async def _tts(text: str, path: Path, voice: str) -> None:
    if ENGINE in ("sarvam", "azure"):
        fn = _sarvam if ENGINE == "sarvam" else _azure
        for n in range(3):
            try:
                fn(text, path, voice)
                return
            except RuntimeError as e:
                print(f"  voice service error {e}, retrying...")
                await asyncio.sleep(5 * (n + 1))
        raise SystemExit("The voice service keeps failing. Try again later.")
    if ENGINE == "own":
        _own(text, path, voice)
        return
    if ENGINE == "eleven":
        if voice.lower().startswith("ne"):          # Nepali story lines: Google Nepali voice
            _gtts(text, path, voice, male=True)
            return
        for n in range(3):
            try:
                _eleven(text, path)
                return
            except RuntimeError as e:
                print(f"  {e}. Retrying...")
                await asyncio.sleep(5 * (n + 1))
        raise SystemExit("ElevenLabs keeps failing. Try again later.")
    if ENGINE == "sapi":
        if voice.lower().startswith("ne"):
            _gtts(text, path, voice, male=True)
        else:
            _sapi(text, path)
        return
    if ENGINE == "gtts-male":
        for n in range(4):
            try:
                _gtts(text, path, voice, male=True)
                return
            except Exception as e:
                print(f"  google voice busy ({e.__class__.__name__}), retrying...")
                await asyncio.sleep(5 * (n + 1))
        raise SystemExit("Google voice failed. Check your internet and try again.")
    if ENGINE == "piper":
        if voice.lower().startswith("ne"):          # no Nepali offline voice: use Google for those lines
            _gtts(text, path, voice)
        else:
            _piper_say(text, path)
        return
    if ENGINE == "gtts":
        for n in range(4):
            try:
                _gtts(text, path, voice)
                return
            except Exception as e:
                print(f"  google voice busy ({e.__class__.__name__}), retrying...")
                await asyncio.sleep(5 * (n + 1))
        raise SystemExit("Google voice also failed. Check your internet and try again.")
    waits = [5, 15, 30]
    for n in range(len(waits) + 1):
        try:
            await _edge(text, path, voice)
            if path.exists() and path.stat().st_size > 0:
                await asyncio.sleep(0.6)        # be gentle, avoids being blocked
                return
        except Exception as e:
            if n == len(waits):
                raise SystemExit(
                    "\nThe Microsoft voice service keeps refusing.\n"
                    "Run again with:  --engine gtts   (Google voice, Indian accent)\n"
                    "or wait 15 minutes and try again (finished lines are cached).")
            print(f"  voice service busy ({e.__class__.__name__}), retrying in {waits[n]} s...")
            await asyncio.sleep(waits[n])
        path.unlink(missing_ok=True)


def pick_engine(voice: str) -> None:
    """In auto mode, test Microsoft once. If it refuses, use Google for the
    whole video so the voice never changes halfway."""
    global ENGINE
    test = OUT / "audio" / "_test.mp3"
    test.parent.mkdir(parents=True, exist_ok=True)
    for n in range(3):
        try:
            asyncio.run(_edge("Namaste.", test, voice))
            if test.exists() and test.stat().st_size > 0:
                ENGINE = "edge"
                print("  voice engine: Microsoft neural")
                return
        except Exception:
            time.sleep(4)
    # Fallbacks, all Indian male: Windows "Ravi" first, then Google made deeper.
    if os.name == "nt":
        try:
            ENGINE = "sapi"
            _sapi("Namaste.", test)
            print("  Microsoft online voice is blocked. Using Windows Indian male voice (Ravi).")
            return
        except BaseException:
            pass
    ENGINE = "gtts-male"
    print("  Microsoft voice is blocked. Using Google Indian voice, made deeper (male).")


def make_voice(scenes, voice: str):
    """One clip per sentence, joined with a short pause. Returns
    {scene: (wav_path, total_seconds, [(sentence, seconds), ...])}."""
    adir = OUT / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    result = {}
    for sid, text in scenes:
        parts = sentences(text)
        clips = []
        for k, sent in enumerate(parts):
            path = adir / f"s{sid:02d}_{k:02d}.mp3"
            tag = path.with_suffix(".hash")
            vkey = PIPER_VOICE if ENGINE == "piper" else os.environ.get("ELEVEN_VOICE_ID", "") if ENGINE == "eleven" else voice
            if ENGINE == "sarvam":
                vkey = os.environ.get("SARVAM_SPEAKER", "karun") + os.environ.get("SARVAM_MODEL", "bulbul:v2")
            if ENGINE == "azure":
                vkey = os.environ.get("AZURE_VOICE", "en-IN-PrabhatNeural")
            if ENGINE == "own":
                mv = MYVOICE / f"{own_key(spoken(sent))}.wav"
                vkey = f"own{mv.stat().st_mtime if mv.exists() else 0}"
            h = hashlib.sha1(f"{ENGINE}|{vkey}|{RATE}|{spoken(sent)}".encode()).hexdigest()
            if not (path.exists() and tag.exists() and tag.read_text() == h):
                print(f"  voice: scene {sid}, line {k + 1}")
                asyncio.run(_tts(spoken(sent), path, voice))
                tag.write_text(h)
            clips.append(path)
        out = adir / f"s{sid:02d}.wav"
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for c in clips:
            cmd += ["-i", str(c)]
        chain = "".join(f"[{i}:a]aresample=48000,apad=pad_dur={GAP}[c{i}];" for i in range(len(clips)))
        chain += "".join(f"[c{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=0:a=1[a]"
        subprocess.run(cmd + ["-filter_complex", chain, "-map", "[a]", str(out)], check=True)
        lines = [(sent, duration(c) + GAP) for sent, c in zip(parts, clips)]
        result[sid] = (out, sum(d for _, d in lines), lines)
    return result


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


# ---------------------------------------------------------------- browser helpers
CURSOR_JS = """
window.__cur = (x, y, down) => {
  let c = document.getElementById('__cur');
  if (!c) {
    c = document.createElement('div'); c.id = '__cur';
    c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M3 2l7 19 2.6-7.4L20 11z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position:'fixed', left:'0', top:'0', zIndex:2147483647, pointerEvents:'none',
      filter:'drop-shadow(0 2px 3px rgba(0,0,0,.5))', transition:'transform .12s ease', transformOrigin:'3px 2px'});
    document.documentElement.appendChild(c);
  }
  c.style.left = (x - 3) + 'px'; c.style.top = (y - 2) + 'px';
  if (down !== undefined) c.style.transform = down ? 'scale(.8)' : 'scale(1)';
};
window.__ripple = (x, y) => {
  const r = document.createElement('div');
  Object.assign(r.style, {position:'fixed', left:(x-22)+'px', top:(y-22)+'px', width:'44px', height:'44px',
    borderRadius:'50%', border:'3px solid #d6336c', zIndex:2147483646, pointerEvents:'none',
    transition:'transform .6s ease-out, opacity .6s ease-out', transform:'scale(.3)', opacity:'1'});
  document.documentElement.appendChild(r);
  requestAnimationFrame(() => { r.style.transform = 'scale(1.6)'; r.style.opacity = '0'; });
  setTimeout(() => r.remove(), 700);
};
window.__ring = (x, y, w, h) => {
  const r = document.createElement('div');
  Object.assign(r.style, {position:'fixed', left:(x-6)+'px', top:(y-6)+'px', width:(w+12)+'px', height:(h+12)+'px',
    borderRadius:'8px', border:'3px solid #d6336c', boxShadow:'0 0 0 4px rgba(214,51,108,.25)',
    zIndex:2147483646, pointerEvents:'none', transition:'opacity .4s', opacity:'0'});
  document.documentElement.appendChild(r);
  requestAnimationFrame(() => r.style.opacity = '1');
  setTimeout(() => { r.style.opacity = '0'; setTimeout(() => r.remove(), 450); }, 1100);
};
"""


class Bot:
    def __init__(self, page: Page):
        self.p = page
        self.x, self.y = W / 2, H / 2

    # The cursor is drawn by the page itself, so it always shows in the video.
    def _draw(self, x, y, down=None):
        try:
            self.p.evaluate("([x,y,d]) => window.__cur && window.__cur(x,y,d===null?undefined:d)",
                            [x, y, down])
        except Exception:
            pass

    def _ensure(self):
        try:
            if not self.p.evaluate("() => !!window.__cur"):
                self.p.evaluate(CURSOR_JS)
        except Exception:
            pass

    def move(self, x: float, y: float, dur: float = 1.1) -> None:
        if x is None or y is None:
            return
        self._ensure()
        n = max(12, int(dur * 40))
        x0, y0 = self.x, self.y
        for i in range(1, n + 1):
            t = i / n
            e = t * t * (3 - 2 * t)
            px, py = x0 + (x - x0) * e, y0 + (y - y0) * e
            self.p.mouse.move(px, py)
            self._draw(px, py)
            time.sleep(dur / n)
        self.x, self.y = x, y

    def click_at(self, x: float, y: float, box=None) -> None:
        self.move(x, y)
        if box:
            try:
                self.p.evaluate("([x,y,w,h]) => window.__ring && window.__ring(x,y,w,h)",
                                [box["x"], box["y"], box["width"], box["height"]])
            except Exception:
                pass
        time.sleep(0.6)
        self._draw(x, y, True)
        try:
            self.p.evaluate("([x,y]) => window.__ripple && window.__ripple(x,y)", [x, y])
        except Exception:
            pass
        self.p.mouse.click(x, y)
        time.sleep(0.15)
        self._draw(x, y, False)
        time.sleep(0.9)

    def drag(self, a, b, dur: float = 2.5) -> None:
        self.move(*a)
        time.sleep(0.3)
        self._draw(*a, True)
        self.p.mouse.down()
        self.move(*b, dur=dur)
        self.p.mouse.up()
        self._draw(*b, False)
        time.sleep(0.5)

    def find(self, cands, timeout: int = 6000):
        each = max(1500, timeout // max(1, len(cands)))
        for c in cands:
            loc = self.p.locator(c).first
            try:
                loc.wait_for(state="visible", timeout=each)
                return loc
            except PWTimeout:
                continue
        return None

    def click(self, cands, timeout: int = 6000) -> bool:
        loc = self.find(cands, timeout)
        if loc is None:
            print(f"  ! not found: {cands}")
            return False
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        if not b:
            loc.click()
            return True
        self.click_at(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, b)
        return True

    def set_check(self, label: str, want: bool = True) -> None:
        cb = self.p.get_by_label(label, exact=False).first
        try:
            if cb.count() and cb.is_checked() == want:
                return
        except Exception:
            pass
        self.click([f"text={label}"])

    def map_point(self, fx: float, fy: float):
        loc = self.find(CFG["map"], 4000)
        b = (loc.bounding_box() if loc else None) or {"x": 0, "y": 0, "width": W, "height": H}
        return b["x"] + b["width"] * fx, b["y"] + b["height"] * fy

    def wheel(self, dy: int, times: int = 4) -> None:
        """Smooth zoom: many small wheel steps."""
        for _ in range(times * 8):
            self.p.mouse.wheel(0, dy / 8)
            time.sleep(0.05)
        time.sleep(0.6)

    def slide(self, title: str, images: list[Path], caption: str = "") -> None:
        imgs = ""
        for im in images:
            if im.exists():
                data = base64.b64encode(im.read_bytes()).decode()
                imgs += f'<img src="data:image/png;base64,{data}">'
            else:
                print(f"  ! missing image {im}")
        html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
          body{{margin:0;height:100vh;background:#0f1720;color:#eef2f6;font:28px system-ui,sans-serif;
               display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px}}
          .row{{display:flex;gap:28px;justify-content:center;max-width:94vw}}
          img{{max-height:76vh;max-width:{90 // max(1, len(images))}vw;border-radius:10px;box-shadow:0 8px 30px #0008}}
          h1{{margin:0;font-size:44px;font-weight:600}} p{{margin:0;opacity:.75}}
          pre{{font-size:22px;line-height:1.35;background:#16212d;padding:28px 36px;border-radius:12px}}
        </style></head><body>{f'<h1>{title}</h1>' if title else ''}<div class="row">{imgs}</div>
        {caption}</body></html>"""
        self.p.set_content(html, wait_until="load")


# The app speaks the story with the browser voice, which a recording cannot hear.
# This swaps the browser voice for ours: every sentence the story says is sent to
# Python, spoken with edge-tts, and placed in the video at the exact moment.
# The story still waits for each sentence to finish, so timing stays right.
SPEECH_JS = """
(() => {
  // Playwright's Chromium has no system voices, so the app thinks voice is not
  // available. We give it fake voices and our own utterance class.
  const mk = (name, lang) => ({name, lang, voiceURI: name, localService: true, default: lang === 'en-IN'});
  const VOICES = [mk('Sampark Indian English', 'en-IN'), mk('Sampark English', 'en-US'),
                  mk('Sampark English UK', 'en-GB'), mk('Sampark Nepali', 'ne-NP'), mk('Sampark Hindi', 'hi-IN')];

  class Utter extends EventTarget {
    constructor(text) { super(); this.text = text || ''; this.lang = ''; this.voice = null;
      this.rate = 1; this.pitch = 1; this.volume = 1;
      this.onstart = this.onend = this.onerror = this.onboundary = this.onpause = this.onresume = this.onmark = null; }
    _fire(type) { const e = new Event(type); try { e.utterance = this; e.charIndex = 0; e.elapsedTime = 0; } catch (x) {}
      this.dispatchEvent(e); const h = this['on' + type]; if (typeof h === 'function') h.call(this, e); }
  }
  window.SpeechSynthesisUtterance = Utter;

  let speaking = false, cur = null, timer = null;
  const queue = [];
  const listeners = {};
  const synth = {
    get speaking() { return speaking; }, get pending() { return queue.length > 0; }, get paused() { return false; },
    onvoiceschanged: null,
    getVoices: () => VOICES.slice(),
    addEventListener: (t, f) => { (listeners[t] = listeners[t] || []).push(f); },
    removeEventListener: (t, f) => { listeners[t] = (listeners[t] || []).filter(x => x !== f); },
    pause() {}, resume() {},
    speak(u) { queue.push(u); if (!speaking) next(); },
    cancel() {
      queue.length = 0; clearTimeout(timer);
      if (cur) { const u = cur; cur = null; speaking = false; window.__sayCancel && window.__sayCancel(); u._fire && u._fire('end'); }
      speaking = false;
    },
  };
  function next() {
    const u = queue.shift();
    if (!u) { speaking = false; return; }
    cur = u; speaking = true;
    const lang = String(u.lang || (u.voice && u.voice.lang) || 'en-IN');
    setTimeout(() => u._fire && u._fire('start'), 0);
    const done = () => { if (cur !== u) return; cur = null; speaking = false; u._fire && u._fire('end'); next(); };
    if (!window.__say) { timer = setTimeout(done, 300); return; }
    window.__say(String(u.text || ''), lang).then(d => {
      if (cur !== u) return;
      timer = setTimeout(done, Math.max(0.2, d) * 1000);
    });
  }
  try { Object.defineProperty(window, 'speechSynthesis', {get: () => synth, configurable: true}); }
  catch (e) { window.speechSynthesis = synth; }
  setTimeout(() => {
    const e = new Event('voiceschanged');
    (listeners.voiceschanged || []).forEach(f => f(e));
    if (typeof synth.onvoiceschanged === 'function') synth.onvoiceschanged(e);
  }, 50);
})();
"""

STORY_VOICE_NE = "ne-NP-SagarNeural"


# ---------------------------------------------------------------- server checks
def backend_runs(page: Page) -> list[str]:
    try:
        r = page.request.get(f"{API}/runs", timeout=60_000)
        if r.ok:
            return [x.get("run_id") for x in r.json().get("runs", []) if x.get("ready")]
    except Exception:
        pass
    return []


def wait_backend(page: Page, run_id: str, minutes: float = 6) -> bool:
    """Wait until the free server is up and has this run loaded.
    After a restart it restores runs one by one, so this can take a few minutes."""
    t0 = time.monotonic()
    shown = False
    while time.monotonic() - t0 < minutes * 60:
        try:
            r = page.request.get(f"{API}/runs/{run_id}", timeout=60_000)
            if r.ok and r.json().get("ready"):
                return True
        except Exception:
            pass
        if not shown:
            print(f"  waiting for the server to load run {run_id} (it may be restarting)...")
            shown = True
        time.sleep(15)
    return False


def pick_demo_run(page: Page) -> str:
    want = CFG["demo_run"]
    if wait_backend(page, want, minutes=6):
        return want
    ready = backend_runs(page)
    if ready:
        print(f"  ! run {want} is not on the server. Using {ready[0]} instead.")
        CFG["demo_run"] = ready[0]
        return ready[0]
    sys.exit("The server has no finished runs right now. Check Render, then try again.")


# ---------------------------------------------------------------- scenes
def open_site(bot: Bot) -> None:
    bot.p.goto(SITE, wait_until="networkidle", timeout=90_000)
    time.sleep(1.5)
    unlock(bot)


def unlock(bot: Bot) -> None:
    key = getattr(bot, "key", "")
    if not key:
        return
    box = bot.find(CFG["unlock_input"], 3000)
    if box is None:
        return                      # already unlocked
    box.fill(key)
    bot.click(CFG["unlock_btn"], 3000)
    time.sleep(1.5)


def open_run(bot: Bot, timeout: int = 120_000) -> bool:
    sel = bot.p.locator(CFG["run_select"]).first
    try:
        sel.wait_for(state="visible", timeout=timeout)
    except PWTimeout:
        print("  ! run dropdown not found")
        return False
    want = CFG["demo_run"]
    value = sel.evaluate("""(el, want) => {
        const o = [...el.options].find(o => o.value.includes(want) || o.text.includes(want));
        return o ? o.value : null; }""", want)
    if value is None:
        opts = sel.evaluate("el => [...el.options].map(o => o.value + '  |  ' + o.text)")
        print(f"  ! demo run '{want}' not in dropdown. Options:\n    " + "\n    ".join(opts))
        return False
    b = sel.bounding_box()
    if b:
        bot.click_at(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    time.sleep(0.4)
    bot.p.keyboard.press("Escape")
    sel.select_option(value)
    time.sleep(0.8)
    bot.click(CFG["open_btn"])
    return True


def s1(bot):
    # Hold the banner for most of the opening line, then load the site.
    bot.slide("", [BANNER])
    hold = getattr(record, "_durs", {}).get(1, 8) - 4
    time.sleep(max(4, hold))
    open_site(bot)


def s2(bot):
    open_run(bot)
    time.sleep(6)


def s3(bot):
    bot.set_check(CFG["imagery"], True)
    bot.move(*bot.map_point(0.5, 0.5))
    bot.wheel(-240, 2)                    # zoom in a little so the image is big
    time.sleep(4)                         # let the satellite image load
    if bot.click(["button:has-text('Play')"], 3000):
        time.sleep(9)                     # Play sweeps before -> after
    rng = bot.p.locator("input[type=range]").first
    try:
        rng.wait_for(state="visible", timeout=3000)
        rng.scroll_into_view_if_needed()
        b = rng.bounding_box()
        if not b:
            raise PWTimeout("no box")
        y = b["y"] + b["height"] / 2
        left, right = b["x"] + 4, b["x"] + b["width"] - 4
        bot.drag((right, y), (left, y), dur=3)
        time.sleep(1)
        bot.drag((left, y), (right, y), dur=3)
    except PWTimeout:
        print("  ! before/after slider not found")
    bot.wheel(240, 2)


def s16(bot):
    for label in ("text=Villages cut off", "text=Buildings affected", "text=Bridges lost", "text=Check vs Copernicus"):
        loc = bot.find([label], 1500)
        b = loc.bounding_box() if loc else None
        if b:
            bot.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, dur=0.9)
            time.sleep(1.6)


def s17(bot):
    for label in ("Flood water", "Debris / scour", "Roads (red = cut)"):
        bot.set_check(label, False)
        time.sleep(0.8)
        bot.set_check(label, True)
        time.sleep(1.2)


def s18(bot):
    box = bot.find(["input[placeholder*='Type a name']"], 3000)
    if not box:
        print("  ! village search not found")
        return
    box.scroll_into_view_if_needed()
    b = box.bounding_box()
    if b:
        bot.click_at(b["x"] + 40, b["y"] + b["height"] / 2, b)
    box.press_sequentially("Syabru", delay=110)
    time.sleep(1.2)
    hit = bot.find(["[role=option]", "li:has-text('Syabru')", "text=/Syabru/"], 2000)
    if hit and hit != box:
        bb = hit.bounding_box()
        if bb:
            bot.click_at(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2, bb)
    else:
        box.press("Enter")
    time.sleep(3)


def s19(bot):
    loc = bot.find(["text=Most affected villages"], 3000)
    x, y = W - 180, 600
    bot.move(x, y)
    for _ in range(10):                   # visible, smooth scroll of the right panel
        bot.p.mouse.wheel(0, 70)
        time.sleep(0.08)
    if loc:
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        if b:
            bot.move(b["x"] + 120, b["y"] + 90, dur=1)
    time.sleep(3)
    for _ in range(10):
        bot.p.mouse.wheel(0, -70)
        time.sleep(0.08)


def grab_reports(page: Page) -> list[Path]:
    """Load each report URL on a hidden page and save it as one tall image."""
    shots = []
    for n, text in enumerate(("Report in English", "रिपोर्ट नेपालीमा")):
        out = OUT / f"report_{n}.png"
        link = page.locator(f"a:has-text('{text}')").first
        try:
            link.wait_for(state="visible", timeout=10_000)
            href = link.evaluate("e => e.href")
        except PWTimeout:
            print(f"  ! report link not found: {text}")
            continue
        print(f"  report: {href}")
        try:
            for attempt in range(3):              # Render may restart once, so retry
                resp = page.request.get(href, timeout=120_000)
                if resp.ok or resp.status not in (502, 503, 504):
                    break
                print(f"  ! report returned {resp.status}, retrying in 25 s...")
                time.sleep(25)
            ctype = resp.headers.get("content-type", "")
            if not resp.ok:
                print(f"  ! report returned {resp.status}")
                continue
            body = resp.body()
            if "pdf" in ctype or body[:4] == b"%PDF":
                if not pdf_to_png(body, out):
                    continue
            else:
                pg = page.context.new_page()
                pg.set_viewport_size({"width": 1100, "height": 1400})
                pg.goto(href, wait_until="networkidle", timeout=90_000)
                pg.wait_for_timeout(1500)
                pg.screenshot(path=str(out), full_page=True)
                pg.close()
            shots.append(out)
            print(f"  report saved: {out.name}  ({ctype or 'unknown type'})")
        except Exception as e:
            print(f"  ! report '{text}' failed: {e}")
    return shots


def grab_reports_direct(page: Page, run_id: str) -> list[Path]:
    """Fetch the reports straight from the API and save them as tall images."""
    shots = []
    for n, lang in enumerate(("en", "ne")):
        url = f"{API}/runs/{run_id}/report?lang={lang}"
        out = OUT / f"report_{n}.png"
        print(f"  report: {url}")
        resp = None
        for attempt in range(3):
            try:
                k = _load_access_key()
                resp = page.request.get(url, timeout=120_000, headers={"X-Access-Key": k} if k else None)
            except Exception as e:
                print(f"  ! report request failed: {e}")
                resp = None
            if resp is not None and (resp.ok or resp.status not in (502, 503, 504)):
                break
            print("  ! server busy or restarting, waiting 30 s...")
            time.sleep(30)
            wait_backend(page, run_id, minutes=4)
        if resp is None or not resp.ok:
            print(f"  ! report ({lang}) failed: {resp.status if resp else 'no answer'}")
            continue
        ctype = resp.headers.get("content-type", "")
        body = resp.body()
        try:
            if "pdf" in ctype or body[:4] == b"%PDF":
                if not pdf_to_png(body, out):
                    continue
            else:
                pg = page.context.browser.new_page(viewport={"width": 1100, "height": 1400})
                base = url.rsplit("/", 1)[0] + "/"
                html = body.decode("utf-8", "replace")
                if "<head" in html:
                    html = re.sub(r"(<head[^>]*>)", rf'\1<base href="{base}">', html, count=1)
                pg.set_content(html, wait_until="networkidle", timeout=60_000)
                pg.wait_for_timeout(1500)
                pg.screenshot(path=str(out), full_page=True)
                pg.close()
            shots.append(out)
            print(f"  report saved: {out.name}  ({ctype or 'unknown type'})")
        except Exception as e:
            print(f"  ! report ({lang}) could not be drawn: {e}")
    return shots


def pdf_to_png(data: bytes, out: Path) -> bool:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        print("  ! report is a PDF. Run: pip install pypdfium2 pillow")
        return False
    pdf = pdfium.PdfDocument(data)
    imgs = [pdf[i].render(scale=2).to_pil() for i in range(min(2, len(pdf)))]
    from PIL import Image
    w = max(i.width for i in imgs)
    sheet = Image.new("RGB", (w, sum(i.height for i in imgs)), "white")
    y = 0
    for im in imgs:
        sheet.paste(im, (0, y))
        y += im.height
    sheet.save(out)
    return True


def show_image_scroll(bot, png: Path, secs: float) -> None:
    data = base64.b64encode(png.read_bytes()).decode()
    bot.p.evaluate("""([d, secs]) => {
      const shade = document.createElement('div'); shade.id = '__shade';
      Object.assign(shade.style, {position:'fixed', inset:'0', background:'rgba(10,15,25,.6)',
        zIndex:2147483639, opacity:'0', transition:'opacity .4s'});
      const box = document.createElement('div'); box.id = '__rep';
      Object.assign(box.style, {position:'fixed', top:'40px', bottom:'40px', left:'50%', width:'1000px',
        marginLeft:'-500px', overflow:'hidden', background:'#fff', borderRadius:'12px',
        boxShadow:'0 20px 60px rgba(0,0,0,.5)', zIndex:2147483640, opacity:'0', transition:'opacity .4s'});
      const img = new Image(); img.src = 'data:image/png;base64,' + d;
      Object.assign(img.style, {width:'100%', display:'block', transform:'translateY(0)'});
      box.appendChild(img); document.body.append(shade, box);
      requestAnimationFrame(() => { shade.style.opacity = '1'; box.style.opacity = '1'; });
      img.onload = () => {
        const travel = Math.max(0, img.height - box.clientHeight);
        setTimeout(() => {
          img.style.transition = `transform ${Math.max(2, secs - 3)}s ease-in-out`;
          img.style.transform = `translateY(-${travel}px)`;
        }, 1200);
      };
    }""", [data, secs])
    time.sleep(secs)
    bot.p.evaluate("() => { for (const id of ['__rep','__shade']) document.getElementById(id)?.remove(); }")


def s21(bot):
    shots = getattr(bot, "reports", [])
    links = ("Report in English", "रिपोर्ट नेपालीमा")
    if not shots:
        print("  ! no report images, skipping")
        return
    for n, png in enumerate(shots):
        a = bot.find([f"a:has-text('{links[min(n, 1)]}')"], 2000)
        if a:
            a.scroll_into_view_if_needed()
            b = a.bounding_box()
            if b:
                bot.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                bot.p.evaluate("([x,y,w,h]) => window.__ring && window.__ring(x,y,w,h)",
                               [b["x"], b["y"], b["width"], b["height"]])
                time.sleep(1)
        show_image_scroll(bot, png, 7)


def s4(bot):
    bot.set_check(CFG["imagery"], False)
    bot.set_check(CFG["voice"], True)          # story voice on (we capture it)
    bot.set_check("River sound", False)
    time.sleep(0.5)
    hold = getattr(record, "_durs", {}).get(4, 3)
    time.sleep(max(0, hold - 2.5))             # let the intro line finish first
    bot.click(CFG["story_start"])
    bot.move(W - 60, H - 60, dur=1.2)           # park the cursor out of the way
    t0 = time.monotonic()
    time.sleep(4)
    # Stay silent and wait until the story ends by itself.
    # While the story plays, the app root has the class "cinema".
    seen = False
    while time.monotonic() - t0 < 300:
        on = bot.p.locator(".app.cinema").count() > 0
        seen = seen or on
        if seen and not on:
            break
        if not seen and time.monotonic() - t0 > 20:
            print("  ! story did not start")
            break
        time.sleep(0.5)
    time.sleep(1.5)
    bot.extra_hold = 0                          # no narration left to wait for


def s5(bot):
    bot.set_check(CFG["uncertainty"], True)
    time.sleep(2)
    loc = bot.find(["text=Legend", "[class*='legend']"], 2000)
    b = loc.bounding_box() if loc else None
    if b:
        bot.move(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2, dur=1)
    time.sleep(9)
    bot.set_check(CFG["uncertainty"], False)


def s6(bot):
    if not bot.click(CFG["village_name"], 3000):
        bot.click_at(*bot.map_point(*CFG["village_xy"]))
    time.sleep(3)


def s7(bot):
    if not bot.click(CFG["card_close"], 2500):
        bot.p.keyboard.press("Escape")
    bot.move(*bot.map_point(0.5, 0.5))
    bot.wheel(260, 4)
    time.sleep(2)


def s8(bot):
    for i in range(2):
        loc = None
        for sel in CFG["repairs"]:
            boxes = bot.p.locator(sel)
            if boxes.count() > i:
                loc = boxes.nth(i)
                break
        if loc is None:
            print("  ! repair checkbox not found")
            return
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        if b:
            bot.click_at(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        else:
            loc.click(force=True)
        time.sleep(4)


def ask(bot, text: str) -> None:
    box = bot.find(CFG["chat_input"])
    if box is None:
        print("  ! chat box not found")
        return
    box.scroll_into_view_if_needed()
    b = box.bounding_box()
    if b:
        bot.click_at(b["x"] + 40, b["y"] + b["height"] / 2)
    before = sum(bot.p.locator(s).count() for s in CFG["chat_answer"])
    box.press_sequentially(text, delay=70)
    time.sleep(0.4)
    box.press("Enter")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 45:
        if sum(bot.p.locator(s).count() for s in CFG["chat_answer"]) > before:
            break
        time.sleep(0.5)
    time.sleep(3)


def s9(bot):
    ask(bot, "कति गाउँ सम्पर्कविहीन छन्?")
    ask(bot, "why?")


def s10(bot):
    loc = bot.p.locator(CFG["traced"][0]).last
    try:
        loc.wait_for(state="visible", timeout=4000)
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        if b:
            bot.click_at(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
            time.sleep(1)
            bot.move(b["x"] + 60, b["y"] + 80, dur=1.5)
    except PWTimeout:
        print("  ! traced link not found")


def s11(bot):
    bot.set_check(CFG["trace"], True)
    time.sleep(1)
    bot.click_at(*bot.map_point(*CFG["trace_xy"]))
    time.sleep(5)


def s12(bot, real_run: bool):
    bot.click(CFG["new_run"])
    time.sleep(2)
    if real_run:
        bot.click(CFG["run_button"])
        time.sleep(8)
        bot.click(CFG["stop_run"])
    time.sleep(2)


def s13(bot):
    loc = bot.find(["text=Check vs Copernicus"], 2000)
    if loc:
        loc.scroll_into_view_if_needed()
        b = loc.bounding_box()
        if b:
            bot.move(b["x"] + 80, b["y"] + 40, dur=1)
            bot.p.evaluate("([x,y,w,h]) => window.__ring && window.__ring(x,y,w,h)", [b["x"], b["y"], 300, 160])
            time.sleep(5)
    bot.slide("Checked against Copernicus EMS (EMSR927)", error_maps(),
              "<p>Sampark (left) · simple 3 dB threshold (right)</p>")


def error_maps() -> list[Path]:
    """Sampark error map first, baseline second. Uses whatever is in docs/figures."""
    found = sorted(FIG.glob("*error_map*.png"))
    if not found:
        print(f"  ! no error maps in {FIG}")
        return []
    run = CFG["demo_run"]
    pref = [p for p in found if run in p.name] or found
    ours = [p for p in pref if "baseline" not in p.name.lower()][:1]
    base = [p for p in pref if "baseline" in p.name.lower()][:1]
    return ours + base


ARCH = """Browser ──► Vercel (React + MapLibre)
              │
              ▼
           Render free (FastAPI, 512 MB)
              │        ▲
   start job  │        │ pull finished run
              ▼        │
     GitHub Actions ───┤──► MongoDB Atlas
     (pipeline, 7 GB)  └──► Backblaze B2
              │
              ▼
     Copernicus Data Space (openEO)"""


def s14(bot):
    bot.slide("Runs on free services", [], f"<pre>{ARCH}</pre>")


def s15(bot):
    bot.slide("", [BANNER], "<p>Educational prototype · open satellite data</p>")


# ---------------------------------------------------------------- probe
def probe(headed: bool) -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=not headed)
        page = br.new_page(viewport={"width": W, "height": H})
        page.goto(SITE, wait_until="networkidle", timeout=90_000)
        bot = Bot(page)
        print("Waiting for the run list (the server may be waking up)...")
        page.locator(CFG["run_select"]).first.wait_for(state="visible", timeout=120_000)
        opts = page.locator(CFG["run_select"]).first.evaluate(
            "el => [...el.options].map(o => o.value + '  |  ' + o.text)")
        print("Runs in the dropdown:\n  " + "\n  ".join(opts))
        if open_run(bot):
            time.sleep(8)
        items = page.evaluate("""() => [...document.querySelectorAll(
            'button,label,summary,a,input,textarea,[role=button],[class*=legend],[class*=repair]')]
            .filter(e => e.offsetParent !== null)
            .map(e => `${e.tagName.toLowerCase()}  class="${e.className}"  text="${(e.innerText||e.placeholder||e.type||'').trim().slice(0,60)}"`)""")
        (OUT / "probe.txt").write_text("\n".join(items), encoding="utf-8")
        page.screenshot(path=str(OUT / "probe.png"))
        print("\nChecking selectors:")
        for key, val in CFG.items():
            if isinstance(val, tuple) or key in ("demo_run", "run_select"):
                continue
            cands = val if isinstance(val, list) else [f"text={val}"]
            hit = next((c for c in cands if page.locator(c).count()), None)
            print(f"  {'ok ' if hit else 'MISS'} {key:13} {hit or cands}")
        br.close()
    print(f"\nWrote {OUT/'probe.txt'} and probe.png. Fix CFG for any MISS.")
    print("(card_close, chat_answer, traced, stop_run only show later, so MISS there is normal.)")


# ---------------------------------------------------------------- record
def record(picked: list[int], headed: bool, real_run: bool, voice: str):
    key = _load_access_key()
    if not key:
        print("! ACCESS_KEY not found in the shell or backend/.env. The copilot scenes will be locked.")
    actions = {1: s1, 2: s2, 3: s3, 4: s4, 5: s5, 6: s6, 7: s7, 8: s8, 9: s9,
               10: s10, 11: s11, 12: lambda b: s12(b, real_run), 13: s13, 14: s14, 15: s15,
               16: s16, 17: s17, 18: s18, 19: s19, 21: s21}
    vdir = OUT / "raw"
    shutil.rmtree(vdir, ignore_errors=True)
    (OUT / "audio").mkdir(parents=True, exist_ok=True)

    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(1)
    story = []                                   # [abs_time, path, text, seconds, cut_at]

    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=not headed, args=["--force-device-scale-factor=1"])

        print("Warming up the server and image cache...")
        warm = br.new_page(viewport={"width": W, "height": H})
        run_id = pick_demo_run(warm)
        print(f"  demo run: {run_id}")
        reports = []
        if 21 in picked:
            reports = grab_reports_direct(warm, run_id)
            # The report can make the free server restart. Wait until it is back.
            if not wait_backend(warm, run_id, minutes=6):
                sys.exit("The server did not come back after the report. Check Render logs.")
        warm.goto(SITE, wait_until="networkidle", timeout=120_000)
        wb = Bot(warm)
        wb.key = key
        unlock(wb)
        if not open_run(wb):
            sys.exit("Could not open the demo run in the app.")
        time.sleep(8)
        wb.set_check(CFG["imagery"], True)
        time.sleep(6)
        warm.close()

        ctx = br.new_context(viewport={"width": W, "height": H},
                             record_video_dir=str(vdir), record_video_size={"width": W, "height": H})
        ctx.add_init_script(CURSOR_JS)
        ctx.add_init_script(SPEECH_JS)

        page_holder = {}

        def say(text: str, lang: str) -> float:
            text = text.strip()
            if not text:
                return 0.2
            v = STORY_VOICE_NE if lang.lower().startswith("ne") else voice
            vk = os.environ.get("ELEVEN_VOICE_ID", "") if ENGINE == "eleven" else v
            if ENGINE == "sarvam":
                vk = v + os.environ.get("SARVAM_SPEAKER", "karun") + os.environ.get("SARVAM_MODEL", "bulbul:v2")
            if ENGINE == "own":
                mv = MYVOICE / f"{own_key(spoken(text))}.wav"
                vk = f"own{mv.stat().st_mtime if mv.exists() else 0}"
            h = hashlib.sha1(f"{ENGINE}|{vk}|{RATE}|{text}".encode()).hexdigest()[:12]
            path = OUT / "audio" / f"story_{h}.mp3"
            if not path.exists():
                pool.submit(lambda: asyncio.run(_tts(spoken(text), path, v))).result()
            d = duration(path)
            story.append([time.monotonic(), path, text, d, None])
            b = page_holder.get("bot")
            if b:
                b.last_say_end = time.monotonic() + d
            return d + 0.25

        def say_cancel() -> None:
            if story and story[-1][4] is None:
                story[-1][4] = time.monotonic()

        ctx.expose_function("__say", say)
        ctx.expose_function("__sayCancel", say_cancel)

        page = ctx.new_page()
        t_rec = time.monotonic()
        bot = Bot(page)
        bot.key = key
        bot.reports = reports
        page_holder["bot"] = bot

        open_site(bot)
        if 1 not in picked:
            open_run(bot)              # later scenes need the run open
            time.sleep(6)
        else:
            bot.slide("", [BANNER])    # clean first frame before the trim point
        time.sleep(1)

        trim = time.monotonic() - t_rec
        cues = []
        for sid in picked:
            if sid == 21 and not reports:
                print("Scene 21 skipped (no report images)")
                continue
            start = time.monotonic()
            print(f"Scene {sid}")
            try:
                actions[sid](bot)
            except Exception as e:     # keep going, one bad click should not kill the take
                print(f"  ! scene {sid} error: {e}")
            cues.append((sid, start - t_rec - trim))
            hold = getattr(record, "_durs", {}).get(sid, 0) + PAD
            left = hold - (time.monotonic() - start)
            if left > 0:
                time.sleep(left)
        end = time.monotonic() - t_rec - trim + 1.0
        time.sleep(1.0)

        video = page.video
        ctx.close()                                 # this finishes writing the video file
        video_path = Path(video.path())             # read it before Playwright stops
        br.close()
    pool.shutdown()
    story_cues = []
    for t, path, text, d, cut in story:
        start = t - t_rec - trim
        length = (cut - t) if cut else d
        if length > 0.1:
            story_cues.append((start, path, text, min(d, length)))
    return video_path, trim, cues, end, story_cues


# ---------------------------------------------------------------- subtitles + mix
def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(rows, path: Path) -> None:
    out = []
    for n, (a, b, text) in enumerate(sorted(rows), start=1):
        out += [str(n), f"{srt_time(a)} --> {srt_time(b)}", text, ""]
    path.write_text("\n".join(out), encoding="utf-8")


def mix(video: Path, trim: float, clips, end: float, srt: Path, out: Path) -> None:
    """clips: [(path, start_seconds, max_seconds_or_None)]"""
    cmd = ["ffmpeg", "-y", "-ss", f"{trim:.3f}", "-i", str(video)]
    for path, _, _ in clips:
        cmd += ["-i", str(path)]
    cmd += ["-i", str(srt)]
    chains, labels = [], []
    for i, (_, start, maxlen) in enumerate(clips, start=1):
        ms = int(max(0, start) * 1000)
        cut = f"atrim=0:{maxlen:.3f},afade=t=out:st={max(0, maxlen - 0.15):.3f}:d=0.15," if maxlen else ""
        chains.append(f"[{i}:a]aresample=48000,{cut}afade=t=in:d=0.04,adelay={ms}|{ms},apad[a{i}]")
        labels.append(f"[a{i}]")
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest,"
                  f"highpass=f=70,acompressor=threshold=-20dB:ratio=2.5:attack=10:release=200,"
                  f"loudnorm=I=-16:TP=-1.5:LRA=11[voice]")
    if MUSIC:
        # Sa-Pa-Sa drone with a slow swell, like a tanpura in the background.
        expr = ("(0.5*sin(2*PI*130.81*t)+0.35*sin(2*PI*196*t)+0.3*sin(2*PI*261.63*t)"
                "+0.12*sin(2*PI*392*t)+0.08*sin(2*PI*523.25*t))*(0.75+0.25*sin(2*PI*0.2*t))")
        chains.append(f"aevalsrc='{expr}':s=48000:d={end:.2f},lowpass=f=1800,"
                      f"aecho=0.8:0.6:120|240:0.3|0.2,volume=0.05,"
                      f"afade=t=in:d=3,afade=t=out:st={max(0, end - 4):.2f}:d=4[music]")
        chains.append("[voice][music]amix=inputs=2:normalize=0:duration=first[aout]")
    else:
        chains[-1] = chains[-1].replace("[voice]", "[aout]")
    sub_idx = len(clips) + 1
    cmd += ["-filter_complex", ";".join(chains),
            "-map", "0:v", "-map", "[aout]", "-map", f"{sub_idx}:s",
            "-t", f"{end:.3f}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-r", "30", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
            "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="list page elements and check selectors")
    ap.add_argument("--headed", action="store_true", help="show the browser while recording")
    ap.add_argument("--scenes", default="", help="e.g. 2,3,8 (default: all)")
    ap.add_argument("--short", action="store_true", help="skip optional scenes 11, 12, 14")
    ap.add_argument("--new-run", action="store_true", help="scene 12 really starts and stops a run")
    ap.add_argument("--voice", default=VOICE)
    ap.add_argument("--export-script", action="store_true", help="write video_out/voice_script.txt to read")
    ap.add_argument("--import-voice", default="", help="one long recording of the script, to cut into lines")
    ap.add_argument("--silent", action="store_true", help="no narration in the final video (music only)")
    ap.add_argument("--record-voice", action="store_true", help="read each line into your mic (teleprompter)")
    ap.add_argument("--redo", action="store_true", help="with --record-voice: record every line again")
    ap.add_argument("--remix", action="store_true", help="rebuild sound and video from the last recording")
    ap.add_argument("--no-music", action="store_true", help="no background drone")
    ap.add_argument("--engine", choices=["auto", "own", "edge", "azure", "sarvam", "eleven", "gtts", "gtts-male", "sapi", "piper"], default="auto",
                    help="voice: auto, edge (Microsoft Indian male), gtts (Indian female), "
                         "gtts-male (Indian accent, made deeper), sapi (Windows Ravi, Indian male), "
                         "piper (offline US male)")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found. Install ffmpeg first.")

    if args.probe:
        probe(args.headed)
        return
    if args.record_voice:
        record_voice(args.redo)
        return
    if args.export_script:
        print(f"Script written: {export_script()}")
        return
    if args.import_voice:
        import_voice(Path(args.import_voice))
        return

    global MUSIC, ENGINE
    MUSIC = not args.no_music
    texts = dict(SCENES)
    picked = [int(x) for x in args.scenes.split(",") if x.strip()] or [s for s, _ in SCENES]
    if args.short:
        picked = [s for s in picked if s not in (11, 12, 14)]

    OUT.mkdir(exist_ok=True)
    if args.engine == "auto":
        pick_engine(args.voice)
    else:
        ENGINE = args.engine
    if ENGINE in ("gtts", "gtts-male", "sapi", "eleven", "own"):
        try:
            import gtts  # noqa: F401
        except ImportError:
            sys.exit("Google voice needs one package. Run:  pip install gTTS")
    if ENGINE == "piper":
        try:
            import piper  # noqa: F401
        except ImportError:
            sys.exit("The offline male voice needs one package. Run:  pip install piper-tts")
    print("Making the voice...")
    audio = make_voice([(s, texts[s]) for s in picked], args.voice)
    durs = {s: v[1] for s, v in audio.items()}
    record._durs = durs

    print("Recording...")
    take = OUT / "take.json"
    if args.remix and take.exists():
        t = json.loads(take.read_text(encoding="utf-8"))
        video, trim, end = Path(t["video"]), t["trim"], t["end"]
        cues = [tuple(c) for c in t["cues"]]
        story = [(a, Path(b), c, d) for a, b, c, d in t["story"]]
        picked = [sid for sid, _ in cues]
        audio = make_voice([(s, texts[s]) for s in picked], args.voice)
        print("Using the saved recording (no new browser run).")
    else:
        video, trim, cues, end, story = record(picked, args.headed, args.new_run, args.voice)
        take.write_text(json.dumps({"video": str(video), "trim": trim, "end": end, "cues": cues,
                                    "story": [(a, str(b), c, d) for a, b, c, d in story]}), encoding="utf-8")

    clips, rows = [], []
    for sid, start in cues:
        clips.append((audio[sid][0], start, None))
        t = start
        for sent, d in audio[sid][2]:
            rows.append((t, t + d - GAP * 0.5, sent))
            t += d
    for start, path, text, d in story:
        clips.append((path, start, d))
        rows.append((start, start + d, text))
    print(f"  story lines captured: {len(story)}")

    srt = OUT / "sampark_demo.srt"
    write_srt(rows, srt)
    out = OUT / "sampark_demo.mp4"
    if args.silent:
        silence = OUT / "audio" / "_silence.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                        "-t", "1", str(silence)], check=True)
        clips = [(silence, 0, 1.0)]
        out = OUT / "sampark_demo_silent.mp4"
    print("Mixing...")
    mix(video, trim, clips, end, srt, out)
    print(f"\nDone: {out}  ({end/60:.1f} min)\nSubtitles: {srt}")


if __name__ == "__main__":
    main()