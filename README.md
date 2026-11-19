# Gaming Shorts Clipper

Put YouTube gameplay links in an Excel sheet. The tool finds the parts people rewatch most, cuts them into
vertical Shorts with a hook, word-by-word captions (English, or Hindi in English letters) and background music,
lets you fix the captions before the video is made, and writes titles and a description for posting.
Free, and it runs on your own PC.

![Layout options](docs/layout-mockup.png)

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
3. Double-click **`setup.cmd`**. The first run downloads packages and takes a few minutes. It ends with a
   checklist; everything except "Music" should have a ✓.
4. Add music to `assets\music`. See the [README there](assets/music/README.md) for free sources and credits.
5. Optional, free: paste a Gemini key into **`gemini_api_key`** on the first lines of **`config.yaml`**
   (see [Gemini](#gemini-optional-free)).

## Everyday use: clips.xlsx

1. Open **`clips.xlsx`** (in the project folder). On the **Videos** tab, put one YouTube link per row in
   **Video link**. Leave **Status** empty. Fill in only what you want to change: every empty cell uses the default.
2. Save and **close** the file (the tool can't write to it while Excel has it open).
3. Double-click **`run.cmd`**.
4. Rows now say **Edit captions**. Open the folder in **Clip folders**. In each clip folder:
   - `captions.srt`: the captions with their timestamps. Fix any wrong words (Notepad is fine).
   - `hook.txt`: the text at the top of the Short. Change it if you like.
5. In clips.xlsx set those rows' **Status** to **Ready**, save, close, and double-click `run.cmd` again.
6. The videos are made. Status becomes **Done**, and **Videos** shows where each `clip.mp4` is. The title and
   description to paste when posting are in `post.txt` next to it.

Don't want to check captions for a row? Put **No** in **Check captions first** and the video is made in one go.
The tool never works on a row twice: rows that say Done, Edit captions, Failed or Skip are left alone.

### The columns

| Column | What to type | Empty means |
|---|---|---|
| **Status** | Leave empty for new rows. Set **Ready** after checking captions, **Redo** to make the row again, **Skip** to ignore it. | New row: work on it. |
| **Video link** | The YouTube link (or the full path of a video file on your PC). | Row is ignored. |
| **Clip times** | Your own clips: `start-end`, several separated by commas: `1:20-1:45, 5:10-5:40`. Only a start (`2:05`) = 30 seconds from there. | The tool finds the best moments. |
| **How many clips** | How many of the best moments to clip (only when Clip times is empty). | 1 |
| **Hook text** | Your hook. For several clips, separate them with `;` in the same order as the clip times. `-` = no hook, `\|` = new line. | Gemini writes one (without Gemini: from the video title). |
| **Check captions first** | Yes / No | Yes |
| **Caption language** | Auto / English / Hindi (Hindi is written in English letters). | Auto |
| **Translate Hindi to English** | Yes = English captions for Hindi speech. | No |
| **Layout** | Blur (whole gameplay on a blurred background) / Crop (fill the screen). | Blur |
| **Zoom** | 1 to 2. Blur layout only: higher = bigger gameplay, edges trimmed. | 1.25 |
| **Facecam** | For streams with a webcam: **Auto**, or the corner it's in (**Bottom right**, Bottom left, Top right, Top left), or an exact box. **Off** = normal layout. See [Facecam split](#facecam-split-streams-with-a-webcam). | Normal layout |
| **Music** | Random / None / the name of a file in `assets\music`. | Random |
| **Show credit** | Yes / No ("footage: @creator" on the video). | Yes |
| **Use Gemini** | Yes / No. | Yes, when a key is set |
| **Game** | gta6 / gta5 / rdr2 (for hashtags). | Detected from the video title |

The grey columns on the right are filled in by the tool: video title, clip folders, finished videos, hooks used,
titles, notes (where the moments came from, problems) and the time of the last run.
Hover over any header in Excel to see its explanation, and see the **How to use** tab.

### Examples

| What you want | What to type |
|---|---|
| 1 clip of the most-replayed moment | Only the link. |
| The best 3 moments | **How many clips:** `3` |
| **5 clips, each at your own times** | **Clip times:** `1:20-1:45, 5:10-5:40, 8:00-8:30, 12:00-12:25, 15:30-16:00` |
| Your own hook for each of those | **Hook text:** `COPS GOT ME; THIS JUMP; NO WAY; BRO WHAT; LAST ONE` |
| Hooks for only some clips | **Hook text:** `COPS GOT ME;; NO WAY` (clips 2, 4, 5 get Gemini's hook) |
| A hook on two lines | **Hook text:** `GTA 6 COPS \| DON'T PLAY` |
| No hook | **Hook text:** `-` |
| A clip starting at 2:05 | **Clip times:** `2:05` (30 seconds) |
| Times after the first hour | **Clip times:** `1:02:10-1:02:40` |
| Webcam big on top, gameplay below | **Facecam:** `Auto` (or the corner, like `Bottom right`) |
| Different settings for different clips of one video | One row per clip, with the same link. |
| Make a finished row again | **Status:** `Redo` |

Times are minutes:seconds (`1:20`) or hours:minutes:seconds (`1:02:10`). The tool may move a cut by up to a
second so it doesn't cut a word in half. Clips over 59 seconds get blocked as Shorts if anyone claims copyright.

### Editing captions

`captions.srt` looks like this. Each block is one caption on screen; its words light up one by one.

```
1
00:00:01,200 --> 00:00:02,050
bhai dekh police

2
00:00:02,300 --> 00:00:03,100
aa gayi
```

- Change the words freely. If a caption keeps the same number of words, each word keeps its exact timing.
  Otherwise the caption's time is shared between its words.
- You can change the times, delete a block, or add one (with its own time line). The numbers don't matter.
- Write normally: the video shows captions in capitals with the spoken word in yellow.
- Free tool with a preview, if you want one: Subtitle Edit (opens .srt files).

## Facecam split (streams with a webcam)

Put **Auto** (or the webcam's corner) in the **Facecam** column. The Short is split in two:

- **Top half: the webcam, enlarged.** It's cropped around the face to fit the panel, then scaled up.
- **Bottom half: the gameplay.** It's taken from the side away from the webcam, so the webcam's original corner
  is left out and the face never shows twice. If the webcam sits so central that it can't be avoided, that spot
  is blurred over instead.
- Nothing covers the face: the hook sits on the line between the two halves, and the captions go just under it
  (with no hook, the captions sit on the line).

How the webcam is found: **Auto** samples a dozen frames and looks for a face that stays in the same place (game
characters move around; a webcam doesn't), then finds the webcam's rectangle from its border. That border is the
edge that stays put while the gameplay changes. A **corner** name works even when no face is visible (for example,
a streamer who is turned away). With Auto and no webcam, the clip falls back to the normal layout, and Notes says so.

**Check `preview.jpg`** in the clip folder (it's made with the captions). If the webcam box is off:
- type the corner instead of Auto, or
- give the exact box as `x,y,w,h` in percent of the video frame: `75,70,25,30` means "starts 75% from the left and
  70% from the top; 25% wide and 30% tall";

then set Status to Ready. The box is worked out again before the video is made.

Quality: a small webcam stretched to half the screen looks soft. It works best with 1080p sources and bigger
webcams. `style.facecam_share` in `config.yaml` sets how much of the height the webcam gets (default 0.5).

In a clips.xlsx made by an older version, the **Facecam** column is added at the far right on the next run.

## What you get

```
output\
  GTA 6 - First 20 Minutes of Gameplay\        one folder per YouTube video
    clip 12.30-12.58\                          one folder per clip (its times in the video)
      captions.srt       editable captions with timestamps
      hook.txt           editable hook (each line = a line on screen)
      clip.mp4           the Short (after the Ready step)
      clip_nomusic.mp4   same clip, game audio only
      post.txt           title ideas, description (with credits), hashtags, checklist
      preview.jpg        how the Short will look (made with the captions, before the video)
      thumb.jpg          frame from the most intense moment
      job.json, meta.json  details the tool keeps
```

To use a trending sound instead of the built-in music, upload `clip_nomusic.mp4` from the YouTube app and add
the sound there.

## How the moments are picked

In order:
1. **Your clip times**, if you typed any.
2. **YouTube's "Most replayed" graph.** The highest spike that clearly stands out; the start and end of the
   video and sponsor reads (SponsorBlock) are ignored.
3. **New videos** (no graph yet, because YouTube only shows it once a video has enough views):
   - **finished livestreams:** bursts in the live-chat replay (more messages, more "LOL / 😂 / 💀");
   - **timestamps in the comments:** viewers write "4:32 💀" within hours of an upload. Comments with more
     likes count more, and a comment listing many timestamps (chapters) counts less.
4. **The loudest moments**, if nothing else works. This is a weak signal; `Notes` says when it happened.

Each clip starts a few seconds before the moment (the setup) and is cut on a scene change or a pause in speech.

**Livestreams:** a stream that's still live can't be clipped; put its link in once it has ended and YouTube shows
the recording. The live-chat replay is used until the recording gets its "Most replayed" graph.

## Gemini (optional, free)

With a free Gemini key, each clip gets one Gemini request that:
- double-checks the caption words (better at misheard words; writes Hinglish the way people type it: "friend",
  not "phrend"). The local speech model still decides when each word appears;
- writes the hook (when you leave Hook text empty), three title ideas, a description and extra hashtags. Gemini
  sees four frames of the clip, so the hook fits what happens even when nobody talks.

To get a key:
1. Go to **aistudio.google.com**, sign in, click **Get API key → Create API key**, in a new project.
   **Don't turn on billing for that project.** Without billing Google can't charge the key: when the free
   quota runs out, it just says so.
2. Paste it between the quotes of `gemini_api_key: ""` on the first lines of `config.yaml`. Never post it anywhere.
3. Run `.\clipper gemini-test` in a terminal opened in the project folder.

When Gemini fails (free quota used up, no reply, a garbled answer), the clip uses the local captions, a hook from
the video title and template titles, and `Notes`/`post.txt` say why. After "quota used up" the tool stops asking
Gemini for 30 minutes. Google doesn't publish the free daily limit; your exact numbers are in AI Studio. On the
free tier, Google may use what you send (the clip's audio and four frames) to improve its products.

## GPU

The tool uses your graphics card automatically when it can:

- **Speech recognition needs an NVIDIA card.** Setup installs NVIDIA's GPU library (about 550 MB) when it finds
  one. The first clip runs a quick GPU test in a separate process, so a driver problem can't crash the tool; the
  result is remembered. If the test passes, captions use the large, most accurate model (`large-v3`); otherwise
  the CPU runs the `small` model.
- **Video encoding works on NVIDIA, AMD or Intel graphics.** The tool tries a tiny test encode and uses the first
  GPU encoder that works, otherwise the CPU.
- The CPU still cuts, blurs, draws the captions and mixes the audio, so both are busy while a clip renders.

`.\clipper doctor` shows what was found.

## Command line (optional)

Everything above works from the sheet. For one-off clips, in a terminal opened in the project folder
(PowerShell: `.\clipper`; Command Prompt: `clipper`), always with the link in quotes:

```bash
.\clipper make "https://www.youtube.com/watch?v=VIDEO_ID" --count 3
```

| Command | What it does |
|---|---|
| `make "<link>"` | Make clips now. Options: `--count 3`, `--start 12:30 --end 12:58`, `--hook "TEXT"`, `--lang hi`, `--translate`, `--layout crop`, `--zoom 1.0`, `--facecam auto` (or `bottom-right` ...), `--music none`, `--no-captions`, `--no-credit`, `--no-gemini`, `--force`, `--review` (stop after captions) |
| `render "<clip folder>"` | Make the video of a folder prepared with `--review` |
| `run` | The same as double-clicking run.cmd |
| `peaks "<link>"` | Show the "Most replayed" graph and the moments it would pick (downloads nothing) |
| `doctor` | Check the setup (add `--recheck-gpu` after a driver update) |
| `gemini-test` | Check your Gemini key works on the free tier |
| `update` | Update the YouTube downloader (fixes most download errors) |

## Speed and cost

- Measured on a 4-core CPU: a 30-second clip from a 1080p 60 fps source took about 37 seconds to cut, mix and
  render. Downloading and speech recognition come on top. I couldn't measure GPU speed (the machine I built
  this on has no GPU).
- The speech model downloads once on first use: `large-v3` (about 3 GB) with a working NVIDIA GPU, `small`
  (about 0.5 GB) on the CPU.
- Cost: $0. All the software is free, and Gemini is used only on its free tier.

## Settings

Everything is in `config.yaml` (created by setup, with comments). Common changes:

- `gemini_api_key` (first lines of the file)
- `sheet.check_captions_first`: the default for the sheet's "Check captions first" column
- `style.layout`, `style.zoom`, `style.caption_size`, `style.highlight_color`
- `captions.model`, `captions.device`, `captions.translate_to_english`, `captions.hindi_script`
  (`latin` = Hindi in English letters, `devanagari` = Hindi script)
- `music.volume_db`, `music.duck_db`
- `render.encoder` to force a video encoder; `render.preset: veryfast` if CPU rendering is too slow

## Troubleshooting

- **"clips.xlsx is open in Excel"**: close it and run again. If Excel grabs it during a run, the results go to a
  copy named `clips (results ...).xlsx`.
- **A row says Failed**: the reason follows "Failed:". Fix it, clear the Status (or set Redo), run again.
- **"Sign in to confirm you're not a bot"**: log into a throwaway Google account in Firefox, then set
  `youtube.cookies_from_browser: firefox` in `config.yaml`. Don't use your main account.
- **Download errors**: run `.\clipper update`. YouTube changes often break downloaders.
- **FFmpeg not found**: run `winget install Gyan.FFmpeg` and open a new terminal, or put the full path to
  `ffmpeg.exe` in `config.yaml` under `ffmpeg:`.
- **Captions on the CPU although you have an NVIDIA card**: `doctor` says why. Usually: run `setup.cmd` again, or
  update the NVIDIA driver, then run `.\clipper doctor --recheck-gpu`.
- **"DLL load failed" when making captions**: install the Microsoft C++ runtime with
  `winget install Microsoft.VCRedist.2015+.x64`, then try again.

## Before you post

These are also printed in every `post.txt`:

- **Over 60 seconds + any copyright claim = blocked worldwide** as a Short. That's why automatic clips stop at 59 s.
- Other people's footage: the creator can claim or strike your video, and 3 strikes in 90 days deletes the
  channel. The on-screen credit is good manners, not permission.
- **Graphic violence in the first ~7 seconds** means no ads (YouTube's September 2026 rule, per news reports).
- Clips focused on **violence against civilians** get age-restricted, which kills their reach.
- No story spoilers in the hook or title.

## For developers

```bash
.venv\Scripts\python -m pytest
```

The code is in `clipper/`: `sheet` (clips.xlsx), `pipeline` (prepare and render steps), `metadata` (yt-dlp),
`moments` (graph analysis), `signals` (comments, live chat), `download`, `refine`, `transcribe` (faster-whisper),
`gemini`, `align`, `captions` (.srt and .ass), `layout`, `audio`, `render`, `package`, `gpu` and `cli`. The tests
use synthetic videos, fake speech and local web servers in place of YouTube and Gemini. See [PLAN.md](PLAN.md).
