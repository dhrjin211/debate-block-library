**If you download this, please star the repo!** It's free and open source, and keeping it updated takes a lot of my time. If i see success, I will be incentivised to spend more time for more features!



# Debate block library

- Should work for all formats and topics
- It's a Python script so no Gen AI 
- Sorting alg will be improved on when i have time 

---

## 1. Download it

**Easiest:** go to the [**latest release**](../../releases/latest) and click
**debate-blocks.zip** to download it.

**Or:** at the top of this page, click the green **Code** button, then
**Download ZIP**.

Then:

1. Find the zip in your **Downloads** folder and double-click it to unzip it.
2. Move the unzipped folder somewhere it can stay, like **Documents**. The app
   keeps your library inside this folder, so don't leave it in Downloads if you
   clean that out.

## 2. Start it

### Mac

1. Open the folder and **right-click** (or Control-click)
   **Start (Mac).command**, then choose **Open**.
2. macOS warns that it can't check the developer. Click **Open**. You only
   have to do this the first time.
   - If there's no **Open** button: open **System Settings → Privacy &
     Security**, scroll down, click **Open Anyway** next to
     "Start (Mac).command", then try again.
3. A Terminal window opens and sets things up. The first time takes about a
   minute.
   - If your Mac asks to install **command line developer tools**, click
     **Install**, wait for it to finish, then double-click **Start (Mac).command**
     again.
   - If it opens the Python website instead, download and install Python,
     then double-click **Start (Mac).command** again.
4. Your browser opens the app at **http://localhost:8420**. Bookmark it.

From then on, just double-click **Start (Mac).command**.

### Windows

1. You need Python first. Get it from
   [python.org/downloads](https://www.python.org/downloads/). On the first
   screen of the installer, tick **"Add python.exe to PATH"** before you click
   **Install Now**.
2. Open the folder and double-click **Start (Windows).bat**.
3. If Windows says **"Windows protected your PC"**, click **More info**, then
   **Run anyway**.
4. The first time takes about a minute. Then your browser opens the app at
   **http://localhost:8420**. Bookmark it.

From then on, just double-click **Start (Windows).bat**.

## 3. Set it up (once)

Click **⚙ Settings** and fill in:

- **Topic name:** for example *Resolved: European NATO members should ratify
  the Treaty on the Prohibition of Nuclear Weapons*. The app also uses these
  words to skip files from the old topic.
- **OpenCaselist caselist:** paste your caselist's link, or just the part after
  `opencaselist.com/`, for example `hspf26`.
- **Download files posted since:** already filled in with the date your topic
  came out. It moves forward on its own when the next topic starts. Change it
  only if your topic came out on a different day.
- **Only keep files that mention** (optional): leave it blank and the app uses
  your topic name. Fill it in if you'd rather pick the words.
- **Include case cards** (on by default): keeps 1AC/1NC case cards as well as
  responses. Turn it off for responses only.

Click **Save**.

## 4. Get your blocks

Click **↻ Update blocks**, sign in with your **OpenCaselist** email and
password, and click **Update now**.

The window shows what's happening and how much time is left. **The first
update is the long one.** It downloads the topic's files and reads every one:
roughly 5–15 minutes depending on your computer and internet. You can keep
using your computer while it runs.

**Before every tournament** (the night before is perfect), click
**↻ Update blocks** again. The app remembers what it already has and only gets
new files, so later updates take seconds to a couple of minutes.

---

## Everyday use

| Input| Output |
| --- | --- |
| Get the newest files | **↻ Update blocks** → **Update now** |
| Add your own Word files | **↻ Update blocks** → **Add Word files…** |
| Find a card | Type in the search box, or click a side and an argument on the left |
| Read the whole card | **▸ Show full card** shows the original highlighting and underlining |
| Copy a card into your doc | **Copy formatted**, then paste into Word or Google Docs |
| Download one card | **Download .docx** |
| Put everything in Google Docs | **Export all for Google Docs**, then in Google Docs: File → Open → Upload |
| Switch to a new topic | Change the topic name in **⚙ Settings**. Then delete the old months in **My files** |
| Delete downloaded files | **My files** → **Delete** next to a month |

**How cards are sorted**
- **AT AFF / AT NEG:** responses to the Aff (Pro) or the Neg (Con).
- **Case cards count as answers.** A Pro team's case card is listed under
  **AT NEG**, because you can read it against Neg arguments, and a Con case card
  under **AT AFF**. They carry a small **Pro case card** / **Con case card** label.
- Cards that don't clearly answer one argument go in **Unsorted**.
- Inside each section, cards read by the most teams come first.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| The page won't load | Double-click **Start** again, then reload http://localhost:8420 |
| "OpenCaselist did not accept that email and password" | Check them at opencaselist.com. The app uses the same login |
| "No caselist called …" | Check the caselist name in **⚙ Settings** |
| A team's files seem to be missing | Fill in **Only keep files that mention** with a few topic words, then update again |
| Some files say "could not download" | OpenCaselist is busy. They're retried automatically next update |
| You want a fresh start | Delete every month in **My files**, then update again |

**Stopping it:** the app keeps running in the background after you close the
Terminal window, which is why the next start is instant. It stops when you
restart your computer.

**Uninstalling:** delete the folder. Nothing is installed anywhere else.

## Your password and files

- Your OpenCaselist email and password are sent only to OpenCaselist. The app
  keeps them in memory while it's running and never writes them to a file.
- Downloaded files stay on your computer, stored by month, so you can delete a
  whole month with one click. A deleted month is never downloaded again.
- Please respect OpenCaselist's terms and the teams who share their evidence.
  Use the files for your own prep, and don't repost them.

---

## For developers

| File | What it does |
| --- | --- |
| `dashboard.py` | The local web app and its page |
| `refresh.py` | OpenCaselist downloads, uploads, delete-by-month, topic filter, rebuild |
| `cardgrab.py` | Reads `.docx` files under `docs/` into `cards.db` (in parallel, cached) |
| `cardparse.py`, `cardformat.py` | Card boundaries and exact source formatting |
| `organize.py` | Rule-based categories; unknown `AT …` headings become their own category |
| `library_export.py` | Whole-library Google Docs export |
| `settings.py` | Settings and NSDA topic release dates |
| `start.py`, `server.py` | Launcher (background server on port 8420) |

```bash
python3 -m venv .venv
```

```bash
.venv/bin/pip install -r requirements.txt
```

```bash
.venv/bin/python server.py --port 8420
```

```bash
.venv/bin/python refresh.py download
```

```bash
.venv/bin/python -m unittest discover -p 'test_*.py'
```

`refresh.py` without `download` just rebuilds from `docs/`. Your own data
(`docs/`, `cards.db`, `settings.json`, `download-history.json`,
`parse-cache.db`) is git-ignored.

## License

MIT. See [LICENSE](LICENSE).
