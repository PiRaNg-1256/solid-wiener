# Gaming Shorts Clipper

Give it a YouTube gameplay video. It finds the part people rewatch most (YouTube's **"Most replayed"** graph),
cuts it into a vertical Short with a hook title, word-by-word captions (English or Hindi) and background
music, and saves it with a ready-to-paste title and description. Free, and it runs on your own PC.

![Layout options](docs/layout-mockup.png)

## What it does

1. Reads the video's "Most replayed" graph (no API key needed).
2. Picks the highest spike that clearly stands out. It ignores the start and end of the video, sponsor reads
   marked on SponsorBlock, and the fake spike right after a sponsor, where people who skipped the ad land.
3. Downloads **only that part** of the video, not the whole thing.
4. Snaps the cut to a scene change or a pause in speech, so it never cuts mid-word. Length: 20–59 seconds.
5. Turns speech into captions (English or Hindi, detected automatically). Hindi comes out in English letters
   ("bhai ye kya ho gaya"), in Hindi script, or both.
6. Builds a 1080×1920 Short: gameplay on a blurred background, your hook on top, captions below, and a small
   "footage: @creator" credit.
7. Adds music that starts at the track's energetic part and dips while someone talks. Loudness is set to
   YouTube's level (-14 LUFS).
8. Saves `clip.mp4`, a no-music copy, a thumbnail and `post.txt` (title ideas, description with credits, hashtags).

## Set up on Windows (once)

1. Open **Terminal** (or PowerShell) and install Python and FFmpeg:

   ```bash
   winget install Python.Python.3.12
   ```

   ```bash
   winget install Gyan.FFmpeg
   ```

   Then close the terminal window.
2. Get this project: on GitHub click **Code → Download ZIP**, then unzip it (for example into `Documents`).
   If you use git, clone it instead, so updates are a `git pull`.
3. Double-click **`setup.cmd`**. The first run downloads a few hundred MB of packages and takes a few minutes.
   It ends with a checklist; everything except "Music" should have a ✓.
4. Add music to `assets\music`. See the [README there](assets/music/README.md) for free sources and credits.

To run commands, open a terminal in the project folder (in File Explorer, right-click the folder → **Open in Terminal**).

## Making clips

Always put links in quotes. In PowerShell, type `.\clipper`; in the old Command Prompt, just `clipper`.

Preview the graph and the moments it would pick (downloads nothing):

```bash
.\clipper peaks "https://www.youtube.com/watch?v=VIDEO_ID"
```

Make the clip. It shows what's said in the clip and asks you for the hook text:

```bash
.\clipper make "https://www.youtube.com/watch?v=VIDEO_ID"
```

| Option | What it does |
|---|---|
| `--count 3` | Make clips of the top 3 moments |
| `--hook "GTA 6 COPS \| DON'T PLAY"` | Set the hook without being asked (`\|` starts a new line) |
| `--start 12:30 --end 12:58` | Choose the moment yourself |
| `--lang hi` or `--lang en` | Force the caption language (default: detect English or Hindi) |
| `--translate` | Hindi speech → English captions |
| `--hindi-script latin` | Hindi captions in English letters only (`devanagari` = Hindi script only, `both` = default) |
| `--layout crop` | Fill the whole screen instead of the blurred layout |
| `--zoom 1.0` | Blurred layout showing the entire game frame (default 1.25 = bigger gameplay) |
| `--music none` or `--music "C:\music\track.mp3"` | No music, or a specific track |
| `--no-captions`, `--no-credit` | Leave out captions or the on-screen credit |
| `--no-gemini` | Use only the local speech model for this run |
| `--force` | Clip a part you already clipped before |

It also works on a video file: `.\clipper make "D:\videos\gameplay.mp4"`.

Other commands:
- `.\clipper doctor` checks the setup: GPU, speech model, video encoder, Gemini key. Add `--recheck-gpu` after a
  driver update.
- `.\clipper gemini-test` checks that your Gemini key works on the free tier.
- `.\clipper update` updates the YouTube downloader (the fix for most download errors).

## GPU

The tool uses your graphics card automatically when it can:

- **Speech recognition (captions) needs an NVIDIA card.** Setup installs NVIDIA's GPU library (about 550 MB) when
  it finds one. The first clip runs a quick GPU test in a separate process, so a driver problem can never crash
  the tool; the result is remembered. If the test passes, captions use the large, most accurate model
  (`large-v3`); otherwise the CPU runs the `small` model.
- **Video encoding works on NVIDIA, AMD or Intel graphics.** The tool tries a tiny test encode and uses the
  first GPU encoder that works, otherwise the CPU.
- The CPU still cuts, blurs, draws the captions and mixes the audio, so both are busy while a clip renders.

`.\clipper doctor` shows what was found.

## Gemini (optional, free)

Gemini can double-check the caption words: it hears misheard words better and writes Hinglish the way people
type it ("friend", not "phrend"). The local model still decides when each word appears.

1. Go to **aistudio.google.com**, sign in, click **Get API key → Create API key**, in a new project.
   **Don't turn on billing for that project.** Without billing Google can't charge the key: when the free
   quota runs out, it just says so.
2. Put the key in `config.yaml` under `captions:` → `gemini:` → `api_key: "..."`. Never post it anywhere.
3. Run `.\clipper gemini-test`.

When Gemini fails for any reason (free quota used up, no reply, a garbled answer, or words that don't match the
speech), that clip uses the local model's words, and `post.txt` says why. After "quota used up" the tool stops
asking Gemini for 30 minutes. Google doesn't publish the free daily limit; your exact numbers are in AI Studio.
On the free tier, Google may use what you send (the clip's audio) to improve its products.

## What you get

```
output\
  review\2026-11-20\001_gta-6-cops-don-t-play\
      clip.mp4           the Short
      clip_nomusic.mp4   same clip, game audio only
      clip_devanagari.mp4  for Hindi speech: same clip with Hindi-script captions
                           (clip.mp4 then has Hindi in English letters)
      post.txt           title ideas, description (with credits), hashtags, checklist
      thumb.jpg          frame from the most intense moment
      meta.json          source link, times, scores, music used
  ready\                 move the clips you like here and post from this folder
```

When posting, paste the title and description from `post.txt`. To use a trending sound instead of the
built-in music, upload `clip_nomusic.mp4` from the YouTube app and add the sound there.

## How the moment is picked

YouTube's graph has 100 points across the video, scored 0–1. The tool smooths it and finds the spikes. Each
spike is scored half on height and half on how much it stands out from its surroundings, so a slow slope near
the start can't beat a real spike. The clip starts 5 s before the spike (the setup) and ends 3 s after it (the
reaction), then the cut is adjusted to the nearest scene change or sentence break.

The graph's resolution is the video length ÷ 100: 12-second steps for a 20-minute video, 72-second steps for a
2-hour stream. For long videos, the tool centres the clip on the loudest part of the spike.

**No graph?** YouTube only shows it once a video has enough views, so brand-new uploads often don't have one.
The tool then falls back to the loudest moments, which is a weaker signal. You can also pick the moment
yourself with `--start`.

## Speed and cost

- Measured on a 4-core CPU: a 30-second clip from a 1080p 60 fps source took about 37 seconds to cut, mix and
  render. Downloading and speech recognition come on top.
- The speech model downloads once on first use: `large-v3` (about 3 GB) with a working NVIDIA GPU,
  `small` (about 0.5 GB) on the CPU. On a CPU-only PC, `captions.model: medium` gives better Hindi but is slower.
- I couldn't measure GPU speed (the machine I built this on has no GPU).
- Cost: $0. All the software is free, and it runs on your computer.

## Settings

Everything is in `config.yaml` (created by setup, with comments). Common changes:

- `style.layout`, `style.zoom`, `style.caption_size`, `style.highlight_color`
- `captions.model`, `captions.device`, `captions.translate_to_english`, `captions.hindi_script` (both / latin / devanagari)
- `captions.gemini.api_key` (see the Gemini section)
- `render.encoder` to force a video encoder
- `music.volume_db`, `music.duck_db`
- `render.preset: veryfast` if rendering is too slow

## Troubleshooting

- **"Sign in to confirm you're not a bot"**: log into a throwaway Google account in Firefox, then set
  `youtube.cookies_from_browser: firefox` in `config.yaml`. Don't use your main account.
- **Download errors**: run `.\clipper update`. YouTube changes often break downloaders.
- **FFmpeg not found**: run `winget install Gyan.FFmpeg` and open a new terminal, or put the full path to
  `ffmpeg.exe` in `config.yaml` under `ffmpeg:`.
- **Wrong caption language**: add `--lang hi` or `--lang en`.
- **Captions on the CPU although you have an NVIDIA card**: `doctor` says why. Usually: run `setup.cmd` again, or
  update the NVIDIA driver, then run `.\clipper doctor --recheck-gpu`.
- **"DLL load failed" when making captions**: install the Microsoft C++ runtime with
  `winget install Microsoft.VCRedist.2015+.x64`, then try again.
- **A link with `&` in it gets cut off**: run `setup.cmd` again (it adds `clipper.exe`, which handles this),
  or delete everything from `&` onwards in the link.

## Before you post

These are also printed in every `post.txt`:

- **Over 60 seconds + any copyright claim = blocked worldwide** as a Short. That's why clips stop at 59 s.
- Other people's footage: the creator can claim or strike your video, and 3 strikes in 90 days deletes the
  channel. The on-screen credit is good manners, not permission.
- **Graphic violence in the first ~7 seconds** means no ads (YouTube's September 2026 rule, per news reports).
- Clips focused on **violence against civilians** get age-restricted, which kills their reach.
- No story spoilers in the hook or title.

## For developers

```bash
.venv\Scripts\python -m pytest
```

The pipeline lives in `clipper/`: `metadata` (yt-dlp), `moments` (graph analysis), `download`, `refine`,
`transcribe` (faster-whisper), `captions` (.ass subtitles), `layout`, `audio`, `render`, `package`, plus
`pipeline` (glue) and `cli`. The tests use synthetic videos, fake speech and a local web server in place of
YouTube. See [PLAN.md](PLAN.md) for the roadmap.

**Next up (phase 2):** batch runs over search results and channel lists, finding moments from timestamps in
comments, and a review page to approve/reject clips and edit hooks before re-rendering.
