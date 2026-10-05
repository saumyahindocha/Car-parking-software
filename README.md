# for RaRa 🤍

A private, mobile-first birthday site for Radha's 19th birthday (12 October 2026).
Plain HTML/CSS/JS with no build step. Built for iPhone Safari first.

```
index.html            page skeleton
js/content.js         ← ALL the words (edit this)
js/app.js             behaviour (countdown, gate, audio, sections)
js/lilies.js          the illustrated lilies / diyas
css/style.css         the look
assets/photos/now/    6–7 recent photos
assets/photos/little/ 8–10 childhood photos
assets/photos/funny/  2–3 funny photos
assets/music/her.mp3
assets/music/say-you-wont-let-go.mp3
assets/audio/voice-note.m4a
tools/                photo optimiser + local server (not deployed)
```

---

## 1. Add your files

**Music and voice note.** Use exactly these names:

| file | plays |
|---|---|
| `assets/music/her.mp3` | from unlocking until the letter, on loop |
| `assets/music/say-you-wont-let-go.mp3` | from the birthday letter to the cake, on loop |
| `assets/audio/voice-note.m4a` | at the finale, when she taps play |

To get the voice note from Voice Memos: open the memo → `…` → **Share** → **Save to Files**. It is already an `.m4a`; rename it to `voice-note.m4a`.

**Photos**

1. Drop the photos into `assets/photos/now/`, `assets/photos/little/` and `assets/photos/funny/`. Any file names are fine. Use JPEG/PNG; if a HEIC won't convert, AirDrop or export it as "Most Compatible".
2. Run the optimiser. You need [Node.js](https://nodejs.org) 18 or newer, and the first run needs `npm install`:
   ```bash
   cd tools
   npm install        # first time only
   npm run photos
   ```
   The optimiser:
   - resizes the photos to 1400px JPEGs, usually 150–300 KB each
   - fixes iPhone rotation
   - renames them `01.jpg`, `02.jpg`… in filename order
   - moves your originals to `photos-original/`, which is git-ignored and never deployed
   - writes `assets/photos/manifest.js`
   - tells you if the number of photos and captions don't match
3. **Captions match photos by position**: the 1st caption in `content.js` goes with `01.jpg`, and so on. To change the order, rename the files in `photos-original/<folder>/` (for example `1-beach.jpg`, `2-red-dress.jpg`) and run `npm run photos` again. It always rebuilds from the originals.

If a photo is missing, the site shows a soft "photo coming soon" lily card, so nothing breaks.

## 2. Edit the words

Open **`js/content.js`**. Every heading, caption, letter, reason, timeline entry and hint is there, grouped by section and numbered in page order.

- Keep the `"quotes"` and the comma after each item.
- `*word*` gives *italic*, `**word**` gives **bold**, and `\n` is a line break.
- Gujarati lines have their meaning in a `// comment` next to them. Comments never show on the site.
- **Before you deploy**, set `settings.whatsappNumber` to your number with country code and digits only, e.g. `"919876543210"`.
- The gate answer (22 Aug) is **not** stored in the files. Only a scrambled hash is in `app.js`, so the hint text is the only clue she can see.

## 3. Preview it before her birthday

```bash
node tools/serve.mjs
```

The server prints two links:
- `http://localhost:5173/?preview=ash` opens it on your computer.
- `http://192.168.x.x:5173/?preview=ash` opens it on **your iPhone**. The phone must be on the same Wi-Fi.

Useful preview tricks (they only work together with `preview=ash`):
- `?preview=ash`: skips the countdown and goes straight to the password gate. Once she unlocks, the site remembers it and shows "Welcome back, RaRa" next time.
- `?preview=ash&reset`: forgets that unlock so you can test the password again.
- `?preview=ash&now=2026-10-18T10:00`: pretends it's that time in India. This one shows the "We made it" Navratri message.

Without `?preview=ash`, the site shows only the countdown until **12 Oct 2026, 00:00 IST**. That's correct whatever time zone her phone is set to.

The gate accepts things like `22/08`, `22-08-2026`, `22.8.26`, `2208`, `22 aug`, `August 22`, `22nd August 2026`, `2026-08-22` and `08/22`.

## 4. Deploy to Vercel (free link)

**Option A: Vercel CLI, no GitHub needed.** Easiest, and the photos stay off GitHub.

1. Sign up free at [vercel.com](https://vercel.com).
2. Install Node, then from this folder run:
   ```bash
   npx vercel          # first time: log in, accept the defaults ("Other" framework, no build command)
   npx vercel --prod   # publishes → https://<name>.vercel.app
   ```
3. To get a nicer link, go to Vercel dashboard → your project → **Settings → Domains** and edit it to something like `for-rara.vercel.app`.
4. After any change, run `npx vercel --prod` again.

**Option B: through GitHub.** Keep the repo **private**, because it holds her photos.

1. Commit and push everything, including the processed `assets/photos/**` and the audio files.
2. In Vercel, choose **Add New → Project** and import the repo.
3. Set Framework Preset to **Other**. Leave the Build Command empty and leave the Output Directory empty (the root).
4. Click **Deploy**. Every push redeploys automatically.

**Privacy built in.** Every page has `noindex, nofollow`. `robots.txt` blocks all crawlers. `vercel.json` sends an `X-Robots-Tag: noindex` header. Fonts are self-hosted, so there are no Google requests, and there are no analytics or trackers. Anyone with the link can still open it, so only send it to her.

## 5. iPhone notes

- She should open the link in **Safari**. Music starts on the tap that unlocks the gate, because iOS doesn't allow autoplay.
- Songs switch automatically as she scrolls. Where iOS supports it, the switch is a soft fade (Web Audio plus `navigator.audioSession`); otherwise it's a clean pause → play. The small round button at the bottom-right mutes or unmutes at any time.
- If the ring/silent switch is on, iOS still plays `<audio>` media, so the site works in silent mode too.
- Animations are reduced automatically if she has "Reduce Motion" turned on.
