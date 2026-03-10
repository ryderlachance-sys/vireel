How to run the Clipper web UI

1. Install dependencies (from project root, clipper folder):
   pip install -r webapp/requirements_web.txt

2. Start the server:
   webapp\run_web.bat

   Or manually:
   cd clipper
   python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8011 --reload

3. Open in browser:
   http://127.0.0.1:8011

4. Paste a YouTube URL, click Generate. Progress streams live; when done, clips appear with play and download links. Outputs are saved to clipper\outputs as short_1.mp4, etc.

Optional: Enable "Use Ollama for titles" and run Ollama locally (e.g. llama3.1) to generate titles and hashtags per clip. If Ollama is not running, fallback titles are used.

YOUTUBE "SIGN IN TO CONFIRM YOU'RE NOT A BOT"
---------------------------------------------
If Generate fails with "Sign in to confirm you're not a bot", YouTube is blocking the download. Use a cookies file:

1) Install a browser extension that exports cookies in Netscape format (e.g. "Get cookies.txt LOCALLY" for Chrome).
2) Open youtube.com in that browser and log in. Use the extension to export cookies and save as cookies.txt.
3) Put cookies.txt in the clipper project folder (same folder as clip.py). The app will use it automatically.
4) Run Generate again.

Do not commit cookies.txt (it is in .gitignore). To use a different path, set YT_DLP_COOKIES=path\to\cookies.txt.

"CHALLENGE SOLVING FAILED" / "ONLY IMAGES AVAILABLE"
-----------------------------------------------------
If Generate fails with "challenge solving failed" or "Only images are available for download", YouTube is limiting what yt-dlp can see. Update yt-dlp to the latest version (YouTube changes often; new releases fix this):

  pip install -U yt-dlp

Or download the latest Windows exe from https://github.com/yt-dlp/yt-dlp/releases and replace your yt-dlp. Then try Generate again.

OFFLINE NEURAL VOICE SETUP (Reddit Video Builder)
-------------------------------------------------
Play Full Story in the Reddit Video Builder uses Piper for high-quality offline TTS when configured.

Steps:
1) Download the Piper Windows binary from https://github.com/rhasspy/piper/releases
2) Download an English voice model (e.g. en_US-lessac-medium) and extract the .onnx (and optional .onnx.json) file.
3) Set environment variables:
   PIPER_BIN=path_to_piper.exe
   PIPER_MODEL=path_to_model.onnx
   Example (PowerShell): $env:PIPER_BIN="C:\Piper\piper.exe"; $env:PIPER_MODEL="C:\Piper\voice\en_US-lessac-medium.onnx"
4) Restart the server. Play Full Story will POST to /api/tts_offline and play the WAV; the Download WAV button appears after generation.

If PIPER_BIN or PIPER_MODEL are not set or files are missing, the UI will show an error (503) with instructions.

AI Voice (Text-to-Speech)
------------------------
The "AI Voice" tab uses the browser's built-in SpeechSynthesis (free, no API key). Paste text, choose a voice from the dropdown (populated from your system/browser), set speed (0.5–2.0), then use Play / Pause / Resume / Stop. Download MP3 is disabled (would require an offline TTS add-on).
